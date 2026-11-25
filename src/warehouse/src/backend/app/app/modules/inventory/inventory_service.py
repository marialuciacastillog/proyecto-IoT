from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.modules.inventory.schemas import MovementCreate
from app.modules.inventory.inventory_repository import InventoryRepository
from app.modules.sync.pending import savePendingEvent
from app.modules.cache.redis_cache import buildInventoryKey, getCache, setCache, deleteCache, countCache

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

    def process_movement(self, movement_in: MovementCreate):
        try:
            # 1. Obtener balance actual bloqueando la fila de manera atómica
            inventory = self.repo.get_by_product_for_update(movement_in.product_id)
            if not inventory:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El inventario no existe")

            new_qty = inventory.quantity
            new_reserved = inventory.reserved_quantity
            m_type = movement_in.type
            qty = movement_in.quantity

            # 2. Evaluación estricta de Reglas de Negocio en Capa de Servicio
            if m_type in ["SHIPMENT", "TRANSFER_OUT"]:
                if new_qty < qty:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Stock insuficiente. Disponible: {new_qty}, Requerido: {qty}"
                    )
                new_qty -= qty

            elif m_type in ["RECEIPT", "TRANSFER_IN", "RETURN"]:
                new_qty += qty

            elif m_type == "RESERVATION":
                if new_qty < qty:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stock insuficiente para reservar")
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
            self.repo.update_stock(inventory, new_qty, new_reserved)
            movement = self.repo.register_movement(movement_in.product_id, m_type, qty, movement_in.reference)

            # 3.1 Guardar el evento pendiente para la central (misma transacción: o se guarda todo o nada)
            savePendingEvent(self.db, movement_in.product_id, new_qty)
            
            # 4. Control de Transacción centralizado en el Servicio
            self.db.commit()

            # 5. Fase 2 - Invalidación explícita: el dato de Redis ya es viejo, se borra.
            #    Va DESPUÉS del commit para que nadie vuelva a guardar en Redis el valor anterior.
            deleteCache(buildInventoryKey(movement_in.product_id))
            return movement

        except Exception as e:
            self.db.rollback()
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    def get_movements(self, product_id: int):
        return self.repo.get_movements_by_product(product_id)
