# Vigilante de la bodega. Corre solo (un contenedor por bodega) y cada minuto:
#   1. Vence las reservas de venta y manuales que pasaron su fecha: las libera y avisa.
#   2. Avisa (sin liberar) si una reserva de transferencia lleva más de 24 horas abierta.
#   3. Avisa si un lote venció y todavía tiene unidades.
#   4. Busca incongruencias entre los movimientos y el stock (RF-15):
#        - stock negativo
#        - el stock no da lo mismo que la suma de sus movimientos
#        - lo reservado no da lo mismo que la suma de las reservas activas
#        - las unidades (disponible + reservado) no dan lo mismo que la suma de los lotes
# Cada alerta queda en stock_alerts y llega a la central como notificación.
import time
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.config import settings
from app.database import SessionLocal, engine, Base
from app.modules.products.models import ProductEntity
from app.modules.inventory.models import InventoryEntity, InventoryMovementEntity, LotEntity, ReservationEntity
from app.modules.sync.models import PendingEventEntity
from app.modules.transfers.models import TransferCommandEntity
from app.modules.catalog.models import CatalogEventEntity
from app.modules.alerts.models import StockAlertEntity
from app.modules.alerts.service import raiseAlert, resolveAlerts
from app.modules.reservations.service import ReservationService
from app.modules.inventory.inventory_service import entryTypes, exitTypes
from app.modules.shared.correlation import currentCorrelationId, newCorrelationId
from app.modules.shared.utils import schemas_utils
from datetime import date

# Las cuentas de incongruencias se leen en UNA sola foto de la base (REPEATABLE READ),
# para que un movimiento que entra justo en medio no parezca una incongruencia.
snapshotEngine = engine.execution_options(isolation_level="REPEATABLE READ")

checkTitles = {
    "NEGATIVO": "stock negativo",
    "MOVIMIENTOS": "el stock no cuadra con los movimientos",
    "RESERVAS": "lo reservado no cuadra con las reservas",
    "LOTES": "las unidades no cuadran con los lotes"
}


def expireReservations(db):
    now = schemas_utils.current_utc_time()
    ids = [row.id for row in db.query(ReservationEntity.id).filter(
        ReservationEntity.status == "ACTIVE", ReservationEntity.kind.in_(["SALE", "MANUAL"]),
        ReservationEntity.expires_at < now).all()]
    db.rollback()
    count = 0
    for reservationId in ids:
        if ReservationService(db).expire(reservationId):
            count = count + 1
            print("RESERVA_VENCIDA", reservationId, flush=True)
    return count


def warnOldTransfers(db):
    now = schemas_utils.current_utc_time()
    old = db.query(ReservationEntity).filter(ReservationEntity.status == "ACTIVE", ReservationEntity.kind == "TRANSFER",
                                             ReservationEntity.alerted == False, ReservationEntity.expires_at < now).all()
    for reservation in old:
        product = db.get(ProductEntity, reservation.product_id)
        raiseAlert(db, "TR:" + str(reservation.reference), "TRANSFERENCIA_SIN_CERRAR", "WARNING", reservation.product_id,
                   "Transferencia " + str(reservation.reference) + " sin cerrar en " + settings.WAREHOUSE_ID,
                   "La transferencia " + str(reservation.reference) + " tiene apartadas " + str(reservation.quantity) +
                   " unidades de " + (product.sku if product else "?") + " desde hace más de " +
                   str(settings.TRANSFER_ALERT_HOURS) + " horas. No se liberan solas porque las maneja la saga: revisa la transferencia.")
        reservation.alerted = True
        print("TRANSFERENCIA_SIN_CERRAR", reservation.reference, flush=True)
    db.commit()
    return len(old)


def warnExpiredLots(db):
    expired = db.query(LotEntity).filter(LotEntity.quantity > 0, LotEntity.expiry_date < date.today(),
                                         LotEntity.expired_alerted == False).all()
    for lot in expired:
        product = db.get(ProductEntity, lot.product_id)
        raiseAlert(db, "LOT:" + str(lot.id), "LOTE_VENCIDO", "WARNING", lot.product_id,
                   "Lote " + lot.lot_code + " vencido en " + settings.WAREHOUSE_ID,
                   "El lote " + lot.lot_code + " de " + (product.sku if product else "?") + " venció el " +
                   lot.expiry_date.isoformat() + " y todavía tiene " + str(lot.quantity) +
                   " unidades. No se pueden vender: sácalas con una salida.")
        lot.expired_alerted = True
        print("LOTE_VENCIDO", lot.lot_code, flush=True)
    # Si un lote vencido ya quedó vacío, su alerta se resuelve
    for lot in db.query(LotEntity).filter(LotEntity.quantity == 0, LotEntity.expired_alerted == True).all():
        resolveAlerts(db, "LOT:" + str(lot.id))
    db.commit()
    return len(expired)


# Lo que dicen los movimientos: se suman desde cero todos los movimientos de cada producto
def replayMovements(snapshot):
    totals = {}
    rows = snapshot.query(InventoryMovementEntity.product_id, InventoryMovementEntity.type,
                          InventoryMovementEntity.reservation_id.isnot(None),
                          func.sum(InventoryMovementEntity.quantity)).group_by(
        InventoryMovementEntity.product_id, InventoryMovementEntity.type,
        InventoryMovementEntity.reservation_id.isnot(None)).all()
    for productId, movementType, fromReservation, quantity in rows:
        total = totals.setdefault(productId, {"available": 0, "reserved": 0})
        if movementType == "SALE" and fromReservation:
            total["reserved"] -= quantity
        elif movementType in entryTypes:
            total["available"] += quantity
        elif movementType in exitTypes:
            total["available"] -= quantity
        elif movementType == "RESERVATION":
            total["available"] -= quantity
            total["reserved"] += quantity
        elif movementType == "RELEASE":
            total["available"] += quantity
            total["reserved"] -= quantity
        elif movementType == "RESERVATION_CONFIRM":
            total["reserved"] -= quantity
    return totals


# Devuelve {product_id: {"sku":..., "NEGATIVO": mensaje o None, ...}}
def findIncongruences():
    findings = {}
    snapshot = Session(bind=snapshotEngine)
    try:
        movements = replayMovements(snapshot)
        reservations = dict(snapshot.query(ReservationEntity.product_id, func.sum(ReservationEntity.quantity)).filter(
            ReservationEntity.status == "ACTIVE").group_by(ReservationEntity.product_id).all())
        lots = dict(snapshot.query(LotEntity.product_id, func.sum(LotEntity.quantity)).group_by(LotEntity.product_id).all())
        skus = dict(snapshot.query(ProductEntity.id, ProductEntity.sku).all())
        for inventory in snapshot.query(InventoryEntity).all():
            productId = inventory.product_id
            sku = skus.get(productId, str(productId))
            available = inventory.quantity
            reserved = inventory.reserved_quantity
            expected = movements.get(productId, {"available": 0, "reserved": 0})
            activeReserved = int(reservations.get(productId) or 0)
            inLots = int(lots.get(productId) or 0)
            result = {"sku": sku, "NEGATIVO": None, "MOVIMIENTOS": None, "RESERVAS": None, "LOTES": None}
            if available < 0 or reserved < 0:
                result["NEGATIVO"] = sku + " tiene stock negativo: disponible " + str(available) + ", reservado " + str(reserved) + "."
            if available != expected["available"] or reserved != expected["reserved"]:
                result["MOVIMIENTOS"] = ("El stock de " + sku + " dice " + str(available) + " disponibles y " + str(reserved) +
                                         " reservadas, pero sus movimientos suman " + str(expected["available"]) +
                                         " disponibles y " + str(expected["reserved"]) + " reservadas.")
            if reserved != activeReserved:
                result["RESERVAS"] = (sku + " tiene " + str(reserved) + " unidades reservadas, pero sus reservas activas suman " +
                                      str(activeReserved) + ".")
            if available + reserved != inLots:
                result["LOTES"] = (sku + " tiene " + str(available + reserved) + " unidades (disponible + reservado), pero sus lotes suman " +
                                   str(inLots) + ".")
            findings[productId] = result
    finally:
        snapshot.close()
    return findings


def checkIncongruences(db):
    raised = 0
    for productId, result in findIncongruences().items():
        for check in checkTitles:
            key = "INC:" + check + ":" + str(productId)
            if result[check] is not None:
                created = raiseAlert(db, key, "INCONGRUENCIA", "ERROR", productId,
                                     "Incongruencia en " + result["sku"] + " (" + settings.WAREHOUSE_ID + "): " + checkTitles[check],
                                     result[check])
                if created is not None:
                    raised = raised + 1
                    print("INCONGRUENCIA", check, result[check], flush=True)
            else:
                resolveAlerts(db, key)
    db.commit()
    return raised


def sweep():
    currentCorrelationId.set(newCorrelationId())
    db = SessionLocal()
    try:
        expireReservations(db)
        warnOldTransfers(db)
        warnExpiredLots(db)
        checkIncongruences(db)
    except Exception as error:
        db.rollback()
        print("Error en la revisión, se intenta en la próxima vuelta:", error, flush=True)
    finally:
        db.close()


def createTables():
    ready = False
    while not ready:
        try:
            Base.metadata.create_all(bind=engine)
            ready = True
        except Exception as error:
            print("Esperando la base de datos:", error, flush=True)
            time.sleep(3)


def main():
    createTables()
    print("Vigilante iniciado para", settings.WAREHOUSE_ID, "| revisa cada", settings.SWEEP_SECONDS, "segundos", flush=True)
    while True:
        sweep()
        time.sleep(settings.SWEEP_SECONDS)


if __name__ == "__main__":
    main()
