from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from app.database import get_db
from sqlalchemy.orm import Session
from app.config import settings
from app.modules.inventory.schemas import MovementCreate, MovementResponse, InventoryResponse
from app.modules.inventory.inventory_service import InventoryService
from app.modules.inventory.models import InventoryMovementEntity, LotEntity, MovementLotEntity
from app.modules.inventory.lots import isExpired, normalizeLotCode
from app.modules.products.models import ProductEntity
from app.modules.auth.auth import requireRoles, checkWarehouse
from app.modules.auth.roles import ADMIN_BODEGA, ENCARGADO, ALL_ROLES

router = APIRouter(tags=["Inventory"])

# Los tipos que una persona puede registrar a mano:
#   PURCHASE (compra) y SALE (venta) llevan precio (RF-13); RECEIPT, SHIPMENT y RETURN son entradas,
#   salidas y devoluciones sin precio. Las reservas se hacen en /reservations.
# TRANSFER_IN, RESERVATION_CONFIRM, RESERVATION y RELEASE los hace el sistema (saga y reservas).
manualTypes = ["PURCHASE", "SALE", "RECEIPT", "SHIPMENT", "RETURN"]


# Las fechas de los movimientos se guardan en UTC sin zona: se les pone la Z para que el navegador
# las muestre en la hora local
def isoUtc(value):
    text = None
    if value is not None:
        text = value.isoformat() + ("Z" if value.tzinfo is None else "")
    return text


def lotsByMovement(db, movementIds):
    result = {}
    if len(movementIds) > 0:
        rows = db.query(MovementLotEntity.movement_id, LotEntity.lot_code, MovementLotEntity.quantity).join(
            LotEntity, LotEntity.id == MovementLotEntity.lot_id).filter(MovementLotEntity.movement_id.in_(movementIds)).all()
        for movementId, lotCode, quantity in rows:
            result.setdefault(movementId, []).append({"lot_code": lotCode, "quantity": quantity})
    return result


def movementRow(movement, product, lots):
    return {
        "id": movement.id,
        "product_id": movement.product_id,
        "sku": product.sku if product else str(movement.product_id),
        "product_name": product.name if product else "",
        "type": movement.type,
        "quantity": movement.quantity,
        "reference": movement.reference,
        "created_by": movement.created_by,
        "created_at": isoUtc(movement.created_at),
        "unit_price": movement.unit_price,
        "total": movement.total,
        "price_type": movement.price_type,
        "counterparty": movement.counterparty,
        "lot_code": movement.lot_code,
        "reservation_id": movement.reservation_id,
        "lots": lots
    }


@router.get("/inventory/{product_id}", response_model=InventoryResponse)
def get_inventory(product_id: int, db: Session = Depends(get_db), user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    service = InventoryService(db)
    return service.get_stock(product_id)


# Registran movimientos el Encargado de recepción y envío y el Administrador por Bodega (RF-09)
@router.post("/inventory/movements", response_model=MovementResponse, status_code=status.HTTP_201_CREATED)
def create_movement(payload: MovementCreate, db: Session = Depends(get_db),
                    user=Depends(requireRoles(ADMIN_BODEGA, ENCARGADO))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    if payload.type not in manualTypes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Tipo no permitido. Opciones: " + ", ".join(manualTypes) + ". Las reservas se hacen en /reservations")
    service = InventoryService(db)
    # El controlador solo delega y responde. No maneja commits ni fallas transaccionales de DB.
    return service.process_movement(payload, user["username"])


# Historial completo de un producto (RF-27): entradas, salidas, compras, ventas, reservas y transferencias,
# con el lote, el precio, quién lo hizo y cómo quedó el stock después de cada movimiento.
@router.get("/inventory/{product_id}/movements")
def get_movements(product_id: int, db: Session = Depends(get_db), user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    product = db.get(ProductEntity, product_id)
    movements = InventoryService(db).get_movements(product_id)
    lots = lotsByMovement(db, [m.id for m in movements])
    available = 0
    reserved = 0
    rows = []
    for movement in movements:
        row = movementRow(movement, product, lots.get(movement.id, []))
        if movement.type == "SALE" and movement.reservation_id:
            reserved -= movement.quantity
        elif movement.type in ["RECEIPT", "PURCHASE", "RETURN", "TRANSFER_IN"]:
            available += movement.quantity
        elif movement.type in ["SHIPMENT", "SALE", "TRANSFER_OUT"]:
            available -= movement.quantity
        elif movement.type == "RESERVATION":
            available -= movement.quantity
            reserved += movement.quantity
        elif movement.type == "RELEASE":
            available += movement.quantity
            reserved -= movement.quantity
        elif movement.type == "RESERVATION_CONFIRM":
            reserved -= movement.quantity
        row["available_after"] = available
        row["reserved_after"] = reserved
        rows.append(row)
    return rows


# Todos los movimientos de la bodega, del más nuevo al más viejo (vista Historial)
@router.get("/movements")
def list_movements(limit: int = 500, type: Optional[str] = None, db: Session = Depends(get_db),
                   user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    query = db.query(InventoryMovementEntity)
    if type:
        query = query.filter(InventoryMovementEntity.type.in_(type.upper().split(",")))
    movements = query.order_by(InventoryMovementEntity.id.desc()).limit(min(max(limit, 1), 2000)).all()
    products = {p.id: p for p in db.query(ProductEntity).all()}
    lots = lotsByMovement(db, [m.id for m in movements])
    return [movementRow(m, products.get(m.product_id), lots.get(m.id, [])) for m in movements]


# Compras y ventas de la bodega con sus totales (RF-13)
@router.get("/transactions")
def list_transactions(type: Optional[str] = None, product_id: Optional[int] = None, db: Session = Depends(get_db),
                      user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    types = ["PURCHASE", "SALE"] if not type else [type.upper()]
    query = db.query(InventoryMovementEntity).filter(InventoryMovementEntity.type.in_(types))
    if product_id is not None:
        query = query.filter(InventoryMovementEntity.product_id == product_id)
    movements = query.order_by(InventoryMovementEntity.id.desc()).limit(2000).all()
    products = {p.id: p for p in db.query(ProductEntity).all()}
    lots = lotsByMovement(db, [m.id for m in movements])
    summary = {"PURCHASE": {"count": 0, "units": 0, "total": 0}, "SALE": {"count": 0, "units": 0, "total": 0}}
    for movement in movements:
        summary[movement.type]["count"] += 1
        summary[movement.type]["units"] += movement.quantity
        summary[movement.type]["total"] += float(movement.total or 0)
    return {"summary": {"purchases": summary["PURCHASE"], "sales": summary["SALE"]},
            "transactions": [movementRow(m, products.get(m.product_id), lots.get(m.id, [])) for m in movements]}


def lotRow(lot, product):
    return {
        "id": lot.id,
        "product_id": lot.product_id,
        "sku": product.sku if product else str(lot.product_id),
        "product_name": product.name if product else "",
        "lot_code": lot.lot_code,
        "quantity": lot.quantity,
        "initial_quantity": lot.initial_quantity,
        "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
        "expired": isExpired(lot),
        "received_at": isoUtc(lot.received_at),
        "created_by": lot.created_by
    }


# Lotes y cuántas unidades tiene cada uno (RF-12)
@router.get("/lots")
def list_lots(product_id: Optional[int] = None, include_empty: bool = False, db: Session = Depends(get_db),
              user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    query = db.query(LotEntity)
    if product_id is not None:
        query = query.filter(LotEntity.product_id == product_id)
    if not include_empty:
        query = query.filter(LotEntity.quantity > 0)
    products = {p.id: p for p in db.query(ProductEntity).all()}
    lots = query.order_by(LotEntity.product_id, LotEntity.expiry_date.asc().nulls_last(), LotEntity.received_at).all()
    return [lotRow(lot, products.get(lot.product_id)) for lot in lots]


# Seguimiento de un lote (RF-11): qué movimientos le metieron o le sacaron unidades
@router.get("/lots/{product_id}/{lot_code}")
def lot_detail(product_id: int, lot_code: str, db: Session = Depends(get_db), user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    lot = db.query(LotEntity).filter(LotEntity.product_id == product_id, LotEntity.lot_code == normalizeLotCode(lot_code)).first()
    if lot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lote no encontrado")
    product = db.get(ProductEntity, product_id)
    rows = db.query(InventoryMovementEntity, MovementLotEntity.quantity).join(
        MovementLotEntity, MovementLotEntity.movement_id == InventoryMovementEntity.id).filter(
        MovementLotEntity.lot_id == lot.id).order_by(InventoryMovementEntity.id).all()
    result = lotRow(lot, product)
    result["movements"] = [dict(movementRow(movement, product, []), lot_quantity=quantity) for movement, quantity in rows]
    return result
