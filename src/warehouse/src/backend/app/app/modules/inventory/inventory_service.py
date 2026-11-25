from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.modules.inventory.schemas import MovementCreate
from app.modules.inventory.inventory_repository import InventoryRepository
from app.modules.inventory.lots import normalizeLotCode, defaultLotCode, addToLot, takeFromLots
from app.modules.products.models import ProductEntity
from app.modules.sync.pending import savePendingEvent
from app.modules.cache.redis_cache import buildInventoryKey, getCache, setCache, deleteCache, countCache

# Lo que suma al disponible y entra a un lote
entryTypes = ["RECEIPT", "PURCHASE", "RETURN", "TRANSFER_IN"]
# Lo que resta del disponible y sale de los lotes
exitTypes = ["SHIPMENT", "SALE", "TRANSFER_OUT"]
priceTypes = ["DETAL", "MAYORISTA"]


class InventoryService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = InventoryRepository(db)

    def initialize_stock(self, product_id: int) -> None:
        # Método expuesto de forma segura para otros servicios del monolito
        self.repo.initialize_inventory(product_id)

    # Fase 2 - Cache Aside: primero Redis; si no está (MISS), PostgreSQL y se guarda en Redis
    def get_stock(self, product_id: int):
        key = buildInventoryKey(product_id)
        inventory = getCache(key)
        if inventory is not None:
            print("CACHE HIT", key)
            countCache("hits")
        else:
            print("CACHE MISS", key)
            countCache("misses")
            entity = self.repo.get_by_product(product_id)
            if not entity:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventario no encontrado")
            inventory = {
                "product_id": entity.product_id,
                "warehouse_id": entity.warehouse_id,
                "quantity": entity.quantity,
                "reserved_quantity": entity.reserved_quantity,
                "updated_at": entity.updated_at
            }
            setCache(key, inventory)
        return inventory

    # Precio de compras y ventas (RF-13). Si no se manda, se usa el del catálogo.
    def pricing(self, movement_in: MovementCreate, product):
        result = {"unit_price": None, "total": None, "price_type": None, "counterparty": movement_in.counterparty}
        if movement_in.type == "PURCHASE":
            unitPrice = movement_in.unit_price if movement_in.unit_price is not None else product.cost
            result["unit_price"] = unitPrice
            if not result["counterparty"]:
                result["counterparty"] = product.supplier
        elif movement_in.type == "SALE":
            priceType = (movement_in.price_type or "DETAL").upper()
            if priceType not in priceTypes:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="price_type debe ser DETAL o MAYORISTA")
            catalogPrice = product.sale_price if priceType == "DETAL" else product.wholesale_price
            result["unit_price"] = movement_in.unit_price if movement_in.unit_price is not None else catalogPrice
            result["price_type"] = priceType
        if result["unit_price"] is not None:
            result["total"] = Decimal(result["unit_price"]) * movement_in.quantity
        return result

    # Aplica el movimiento SIN hacer commit, para que quien lo llama pueda guardar más cosas en la
    # misma transacción (por ejemplo, la reserva). reservation_id: la venta sale de esa reserva.
    def apply_movement(self, movement_in: MovementCreate, created_by: str = "sistema", reservation_id: str = None):
        # 1. Obtener balance actual bloqueando la fila de manera atómica
        inventory = self.repo.get_by_product_for_update(movement_in.product_id)
        if not inventory:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El inventario no existe")
        product = self.db.query(ProductEntity).filter(ProductEntity.id == movement_in.product_id).first()

        new_qty = inventory.quantity
        new_reserved = inventory.reserved_quantity
        m_type = movement_in.type
        qty = movement_in.quantity

        # 2. Evaluación estricta de Reglas de Negocio en Capa de Servicio
        if m_type == "SALE" and reservation_id is not None:
            # La venta de una reserva saca las unidades apartadas, no las disponibles
            if new_reserved < qty:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La venta excede lo reservado")
            new_reserved -= qty

        elif m_type in exitTypes:
            if new_qty < qty:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Stock insuficiente. Disponible: {new_qty}, Requerido: {qty}"
                )
            new_qty -= qty

        elif m_type in entryTypes:
            new_qty += qty

        elif m_type == "RESERVATION":
            if new_qty < qty:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Stock insuficiente para reservar. Disponible: {new_qty}, Requerido: {qty}")
            new_qty -= qty
            new_reserved += qty

        elif m_type == "RESERVATION_CONFIRM":
            # Fase 6: las unidades apartadas salen definitivamente (transferencia completada)
            if new_reserved < qty:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Confirmación excede reserva existente")
            new_reserved -= qty

        elif m_type == "RELEASE":
            if new_reserved < qty:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Liberación excede reserva existente")
            new_reserved -= qty
            new_qty += qty
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tipo de movimiento inválido")

        # 3. Aplicar mutación y registrar auditoría a través del repositorio
        prices = self.pricing(movement_in, product)
        self.repo.update_stock(inventory, new_qty, new_reserved)
        movement = self.repo.register_movement(movement_in.product_id, m_type, qty, movement_in.reference, created_by)
        movement.unit_price = prices["unit_price"]
        movement.total = prices["total"]
        movement.price_type = prices["price_type"]
        movement.counterparty = prices["counterparty"]
        movement.reservation_id = reservation_id
        self.db.flush()

        # 3.1 Lotes (RF-11): lo que entra va a un lote; lo que sale físicamente se saca de los lotes
        lotCode = normalizeLotCode(movement_in.lot_code)
        if m_type in entryTypes:
            if lotCode is None:
                lotCode = normalizeLotCode(movement_in.reference) if m_type == "TRANSFER_IN" and movement_in.reference else defaultLotCode(m_type)
            movement.lot_code = addToLot(self.db, movement, movement_in.product_id, qty, lotCode, movement_in.expiry_date, created_by)
        elif m_type in exitTypes or m_type == "RESERVATION_CONFIRM":
            movement.lot_code = takeFromLots(self.db, movement, movement_in.product_id, qty, lotCode, m_type == "SALE")

        # 3.2 Guardar el evento pendiente para la central (misma transacción: o se guarda todo o nada)
        savePendingEvent(self.db, movement_in.product_id, new_qty, created_by, "INVENTORY_UPDATED", {
            "movementType": m_type,
            "movementQuantity": qty,
            "reservedAfter": new_reserved,
            "unitPrice": movement.unit_price,
            "total": movement.total,
            "priceType": movement.price_type,
            "counterparty": movement.counterparty,
            "lotCode": movement.lot_code,
            "reference": movement.reference,
            "reservationId": reservation_id
        })
        return movement

    # Después del commit: el dato de Redis ya es viejo, se borra (Fase 2 - invalidación explícita).
    # Va DESPUÉS del commit para que nadie vuelva a guardar en Redis el valor anterior.
    def after_commit(self, product_id: int):
        deleteCache(buildInventoryKey(product_id))

    # created_by: usuario que hizo el movimiento (RF-28). Los movimientos automáticos dicen "sistema".
    def process_movement(self, movement_in: MovementCreate, created_by: str = "sistema"):
        try:
            movement = self.apply_movement(movement_in, created_by)
            # 4. Control de Transacción centralizado en el Servicio
            self.db.commit()
            self.after_commit(movement_in.product_id)
            return movement

        except Exception as e:
            self.db.rollback()
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    def get_movements(self, product_id: int):
        return self.repo.get_movements_by_product(product_id)
