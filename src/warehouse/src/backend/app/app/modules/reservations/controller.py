from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.modules.inventory.models import ReservationEntity
from app.modules.products.models import ProductEntity
from app.modules.reservations.service import ReservationService
from app.modules.auth.auth import requireRoles, checkWarehouse
from app.modules.auth.roles import ADMIN_BODEGA, ENCARGADO, ALL_ROLES
from app.modules.shared.utils import schemas_utils

router = APIRouter(prefix="/reservations", tags=["Reservations"])


class ReservationCreate(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)
    kind: str = Field(..., description="SALE (venta, 48 horas) o MANUAL (con hours, máximo 168)")
    hours: Optional[int] = None
    reason: Optional[str] = Field(None, max_length=200)
    counterparty: Optional[str] = Field(None, max_length=120)  # cliente


class ReservationRelease(BaseModel):
    note: Optional[str] = Field(None, max_length=200)


class ReservationSale(BaseModel):
    price_type: Optional[str] = "DETAL"
    unit_price: Optional[Decimal] = Field(None, ge=0)
    counterparty: Optional[str] = Field(None, max_length=120)
    lot_code: Optional[str] = None


def reservationRow(reservation, product):
    now = schemas_utils.current_utc_time()
    minutesLeft = None
    if reservation.status == "ACTIVE":
        minutesLeft = int((reservation.expires_at - now).total_seconds() // 60)
    return {
        "id": reservation.id,
        "product_id": reservation.product_id,
        "sku": product.sku if product else str(reservation.product_id),
        "product_name": product.name if product else "",
        "quantity": reservation.quantity,
        "kind": reservation.kind,
        "status": reservation.status,
        "reason": reservation.reason,
        "counterparty": reservation.counterparty,
        "reference": reservation.reference,
        "expires_at": reservation.expires_at,
        "minutes_left": minutesLeft,
        "created_by": reservation.created_by,
        "created_at": reservation.created_at,
        "closed_by": reservation.closed_by,
        "closed_at": reservation.closed_at,
        "close_note": reservation.close_note
    }


def withProduct(db, reservation):
    return reservationRow(reservation, db.get(ProductEntity, reservation.product_id))


@router.get("")
def list_reservations(status: Optional[str] = None, db: Session = Depends(get_db), user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    query = db.query(ReservationEntity)
    if status:
        query = query.filter(ReservationEntity.status == status.upper())
    products = {p.id: p for p in db.query(ProductEntity).all()}
    reservations = query.order_by((ReservationEntity.status == "ACTIVE").desc(), ReservationEntity.created_at.desc()).limit(500).all()
    return [reservationRow(r, products.get(r.product_id)) for r in reservations]


# Apartar unidades para una venta (48 horas) o por otro motivo (manual, hasta 7 días)
@router.post("", status_code=status.HTTP_201_CREATED)
def create_reservation(payload: ReservationCreate, db: Session = Depends(get_db),
                       user=Depends(requireRoles(ADMIN_BODEGA, ENCARGADO))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    reservation = ReservationService(db).create(payload.product_id, payload.quantity, payload.kind, payload.hours,
                                                payload.reason, payload.counterparty, user["username"])
    return withProduct(db, reservation)


@router.post("/{reservation_id}/release")
def release_reservation(reservation_id: str, payload: ReservationRelease, db: Session = Depends(get_db),
                        user=Depends(requireRoles(ADMIN_BODEGA, ENCARGADO))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    reservation = ReservationService(db).release(reservation_id.upper(), user["username"], payload.note)
    return withProduct(db, reservation)


# La reserva se vende: las unidades apartadas salen como venta con su precio
@router.post("/{reservation_id}/sell")
def sell_reservation(reservation_id: str, payload: ReservationSale, db: Session = Depends(get_db),
                     user=Depends(requireRoles(ADMIN_BODEGA, ENCARGADO))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    reservation = ReservationService(db).sell(reservation_id.upper(), user["username"], payload.price_type,
                                              payload.unit_price, payload.counterparty, payload.lot_code)
    return withProduct(db, reservation)
