# Reservas con vencimiento. Cada reserva es su propio registro (tabla reservations) y siempre va
# junto con su movimiento de inventario en la MISMA transacción:
#   crear    -> RESERVATION (disponible -> reservado)
#   liberar  -> RELEASE (reservado -> disponible)
#   vender   -> SALE que sale de lo reservado
#   vencer   -> RELEASE hecho por el vigilante cuando pasa la fecha límite
# Las reservas de transferencia las crea y las cierra la saga (worker de transferencias).
import uuid
from datetime import timedelta
from fastapi import HTTPException, status
from app.config import settings
from app.modules.inventory.models import ReservationEntity
from app.modules.inventory.schemas import MovementCreate
from app.modules.inventory.inventory_service import InventoryService
from app.modules.alerts.service import raiseAlert, resolveAlerts
from app.modules.products.models import ProductEntity
from app.modules.shared.utils import schemas_utils

kindLabels = {"SALE": "venta", "MANUAL": "manual", "TRANSFER": "transferencia"}


def newReservationId():
    return "RES-" + uuid.uuid4().hex[:6].upper()


def productLabel(db, productId):
    product = db.query(ProductEntity).filter(ProductEntity.id == productId).first()
    return product.sku + " · " + product.name if product is not None else str(productId)


class ReservationService:
    def __init__(self, db):
        self.db = db
        self.inventory = InventoryService(db)

    def lockReservation(self, reservationId):
        reservation = self.db.query(ReservationEntity).filter(ReservationEntity.id == reservationId).with_for_update().first()
        if reservation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reserva no encontrada")
        return reservation

    # Solo las reservas de venta y las manuales las cierra una persona
    def checkOpenByPerson(self, reservation):
        if reservation.status != "ACTIVE":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="La reserva ya está cerrada (" + reservation.status + ")")
        if reservation.kind == "TRANSFER":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Esta reserva es de la transferencia " + str(reservation.reference) + ": la cierra la saga, no una persona")

    def close(self, reservation, newStatus, username, note):
        reservation.status = newStatus
        reservation.closed_by = username
        reservation.closed_at = schemas_utils.current_utc_time()
        reservation.close_note = note

    def commit(self, productId):
        self.db.commit()
        self.inventory.after_commit(productId)

    # Reserva de venta (48 horas) o manual (vencimiento obligatorio, máximo 7 días)
    def create(self, productId, quantity, kind, hours, reason, counterparty, username):
        kind = (kind or "").upper()
        if kind not in ["SALE", "MANUAL"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="kind debe ser SALE (venta) o MANUAL")
        if kind == "SALE":
            hours = settings.SALE_RESERVATION_HOURS
        elif hours is None or hours < 1 or hours > settings.MANUAL_RESERVATION_MAX_HOURS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Una reserva manual necesita cuántas horas dura: de 1 a " +
                                       str(settings.MANUAL_RESERVATION_MAX_HOURS) + " (7 días)")
        if kind == "MANUAL" and len((reason or "").strip()) < 5:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Una reserva manual necesita el motivo (mínimo 5 caracteres)")
        reservationId = newReservationId()
        try:
            self.inventory.apply_movement(MovementCreate(product_id=productId, type="RESERVATION", quantity=quantity,
                                                         reference=reservationId), username)
            reservation = ReservationEntity(id=reservationId, product_id=productId, quantity=quantity, kind=kind,
                                            reason=(reason or "").strip() or None, counterparty=counterparty,
                                            expires_at=schemas_utils.current_utc_time() + timedelta(hours=hours),
                                            created_by=username)
            self.db.add(reservation)
            self.commit(productId)
        except Exception:
            self.db.rollback()
            raise
        return reservation

    def release(self, reservationId, username, note):
        try:
            reservation = self.lockReservation(reservationId)
            self.checkOpenByPerson(reservation)
            self.inventory.apply_movement(MovementCreate(product_id=reservation.product_id, type="RELEASE",
                                                         quantity=reservation.quantity, reference=reservation.id), username)
            self.close(reservation, "RELEASED", username, note)
            self.commit(reservation.product_id)
        except Exception:
            self.db.rollback()
            raise
        return reservation

    # La reserva se convierte en venta: las unidades apartadas salen con su precio (RF-13)
    def sell(self, reservationId, username, priceType, unitPrice, counterparty, lotCode):
        try:
            reservation = self.lockReservation(reservationId)
            self.checkOpenByPerson(reservation)
            movement = self.inventory.apply_movement(MovementCreate(
                product_id=reservation.product_id, type="SALE", quantity=reservation.quantity,
                reference=reservation.id, price_type=priceType, unit_price=unitPrice, lot_code=lotCode,
                counterparty=counterparty or reservation.counterparty), username, reservation.id)
            self.close(reservation, "SOLD", username, "Venta " + str(movement.id))
            self.commit(reservation.product_id)
        except Exception:
            self.db.rollback()
            raise
        return reservation

    # Lo usa el vigilante: si la reserva sigue activa y ya pasó su fecha, se libera sola
    def expire(self, reservationId):
        expired = False
        try:
            reservation = self.lockReservation(reservationId)
            now = schemas_utils.current_utc_time()
            if reservation.status == "ACTIVE" and reservation.kind in ["SALE", "MANUAL"] and reservation.expires_at < now:
                self.inventory.apply_movement(MovementCreate(product_id=reservation.product_id, type="RELEASE",
                                                             quantity=reservation.quantity, reference=reservation.id),
                                              "sistema:vencimiento")
                self.close(reservation, "EXPIRED", "sistema:vencimiento", "Se venció sin venderse ni liberarse")
                raiseAlert(self.db, "RES:" + reservation.id, "RESERVA_VENCIDA", "WARNING", reservation.product_id,
                           "Reserva " + reservation.id + " vencida en " + settings.WAREHOUSE_ID,
                           "La reserva de " + kindLabels[reservation.kind] + " " + reservation.id + " (" +
                           productLabel(self.db, reservation.product_id) + " x" + str(reservation.quantity) + ", de " +
                           reservation.created_by + ") se venció. Las unidades volvieron a estar disponibles.")
                self.commit(reservation.product_id)
                expired = True
            else:
                self.db.rollback()
        except Exception:
            self.db.rollback()
            raise
        return expired

    # ---- Reservas de la saga de transferencias (no hacen commit: el worker guarda todo junto) ----

    def reserveForTransfer(self, productId, quantity, transferId, username):
        self.inventory.apply_movement(MovementCreate(product_id=productId, type="RESERVATION", quantity=quantity,
                                                     reference=transferId), username)
        self.db.add(ReservationEntity(id=newReservationId(), product_id=productId, quantity=quantity, kind="TRANSFER",
                                      reference=transferId, reason="Transferencia " + transferId,
                                      expires_at=schemas_utils.current_utc_time() + timedelta(hours=settings.TRANSFER_ALERT_HOURS),
                                      created_by=username))

    # COMMIT: las unidades se fueron (CONSUMED). RELEASE: la transferencia falló y vuelven (RELEASED).
    def closeTransfer(self, productId, quantity, transferId, command, username):
        movementType = "RESERVATION_CONFIRM" if command == "COMMIT" else "RELEASE"
        self.inventory.apply_movement(MovementCreate(product_id=productId, type=movementType, quantity=quantity,
                                                     reference=transferId), username)
        reservation = self.db.query(ReservationEntity).filter(ReservationEntity.kind == "TRANSFER",
                                                              ReservationEntity.reference == transferId,
                                                              ReservationEntity.status == "ACTIVE").with_for_update().first()
        if reservation is not None:
            self.close(reservation, "CONSUMED" if command == "COMMIT" else "RELEASED", username, None)
            resolveAlerts(self.db, "TR:" + transferId)
