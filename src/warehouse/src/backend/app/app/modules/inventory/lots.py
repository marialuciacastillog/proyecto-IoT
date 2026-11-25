# Lotes (RF-11, RF-12). No hacen commit: van dentro de la transacción del movimiento.
#   - Lo que ENTRA (compra, entrada, devolución, transferencia que llega) va a un lote.
#     Si no se dice cuál, se usa un lote por día: L-AAAAMMDD (devoluciones: DEV-AAAAMMDD).
#   - Lo que SALE (venta, salida, transferencia que se va) se saca de los lotes que vencen primero
#     (y luego de los más viejos). Si se dice el lote, se saca solo de ese.
#   - Las ventas no sacan de lotes vencidos.
import re
from datetime import date
from fastapi import HTTPException, status
from app.modules.inventory.models import LotEntity, MovementLotEntity
from app.modules.shared.utils import schemas_utils

lotPattern = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{1,39}$")


def normalizeLotCode(code):
    value = None
    if code is not None and code.strip() != "":
        value = code.strip().upper()
        if not lotPattern.match(value):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Código de lote inválido: usa de 2 a 40 letras, números, guiones o puntos (por ejemplo L-2026-11)")
    return value


def defaultLotCode(movementType):
    today = schemas_utils.current_utc_time().strftime("%Y%m%d")
    prefix = "DEV-" if movementType == "RETURN" else "L-"
    return prefix + today


def findLot(db, productId, lotCode):
    return db.query(LotEntity).filter(LotEntity.product_id == productId, LotEntity.lot_code == lotCode).first()


# Suma unidades a un lote (lo crea si no existe). Devuelve el texto del lote para el movimiento.
def addToLot(db, movement, productId, quantity, lotCode, expiryDate, createdBy):
    lot = findLot(db, productId, lotCode)
    if lot is None:
        lot = LotEntity(product_id=productId, lot_code=lotCode, quantity=0, initial_quantity=0,
                        expiry_date=expiryDate, created_by=createdBy)
        db.add(lot)
        db.flush()
    elif expiryDate is not None and lot.expiry_date is not None and lot.expiry_date != expiryDate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="El lote " + lotCode + " ya existe y vence el " + lot.expiry_date.isoformat() +
                                   ". Usa otro código de lote para la fecha " + expiryDate.isoformat())
    elif expiryDate is not None and lot.expiry_date is None:
        lot.expiry_date = expiryDate
    # Si el lote estaba vacío y vuelve a recibir unidades, el aviso de vencido se puede volver a dar
    if lot.quantity == 0:
        lot.expired_alerted = False
    lot.quantity = lot.quantity + quantity
    lot.initial_quantity = lot.initial_quantity + quantity
    db.add(MovementLotEntity(movement_id=movement.id, lot_id=lot.id, quantity=quantity))
    return lot.lot_code


def isExpired(lot):
    return lot.expiry_date is not None and lot.expiry_date < date.today()


# Saca unidades de los lotes. Devuelve el texto de los lotes usados, por ejemplo "L-1 (3), L-2 (2)".
def takeFromLots(db, movement, productId, quantity, lotCode, skipExpired):
    query = db.query(LotEntity).filter(LotEntity.product_id == productId, LotEntity.quantity > 0)
    if lotCode is not None:
        query = query.filter(LotEntity.lot_code == lotCode)
    lots = query.order_by(LotEntity.expiry_date.asc().nulls_last(), LotEntity.received_at, LotEntity.id).with_for_update().all()
    usable = [lot for lot in lots if not (skipExpired and isExpired(lot))]
    totalUsable = sum(lot.quantity for lot in usable)
    if lotCode is not None and len(lots) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El lote " + lotCode + " no existe o está vacío")
    if totalUsable < quantity:
        detail = "No hay suficientes unidades en los lotes. Hay " + str(totalUsable) + ", se piden " + str(quantity)
        if lotCode is not None and len(usable) == 0:
            detail = "El lote " + lotCode + " venció el " + lots[0].expiry_date.isoformat() + ": no se puede vender. Sácalo con una salida"
        elif len(usable) < len(lots):
            detail = "Solo hay " + str(totalUsable) + " unidades sin vencer. Las demás están en lotes vencidos: " + \
                     ", ".join(lot.lot_code for lot in lots if isExpired(lot)) + ". Sácalas con una salida"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    pending = quantity
    used = []
    for lot in usable:
        if pending > 0:
            taken = min(lot.quantity, pending)
            lot.quantity = lot.quantity - taken
            pending = pending - taken
            db.add(MovementLotEntity(movement_id=movement.id, lot_id=lot.id, quantity=taken))
            used.append(lot.lot_code + " (" + str(taken) + ")")
    return ", ".join(used)
