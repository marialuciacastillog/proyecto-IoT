from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.modules.alerts.models import StockAlertEntity
from app.modules.products.models import ProductEntity
from app.modules.auth.auth import requireRoles, checkWarehouse
from app.modules.auth.roles import ADMIN_BODEGA, ADMIN_GLOBAL, ALL_ROLES
from app.modules.shared.utils import schemas_utils

router = APIRouter(prefix="/alerts", tags=["Alerts"])


class AlertReview(BaseModel):
    note: str = Field("", max_length=300)


def alertRow(alert, product):
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "level": alert.level,
        "product_id": alert.product_id,
        "sku": product.sku if product else None,
        "title": alert.title,
        "message": alert.message,
        "status": alert.status,
        "created_at": alert.created_at,
        "resolved_at": alert.resolved_at,
        "reviewed_by": alert.reviewed_by,
        "reviewed_at": alert.reviewed_at,
        "review_note": alert.review_note
    }


# Alertas de esta bodega (RF-15). Las abiertas primero.
@router.get("")
def list_alerts(status: Optional[str] = None, db: Session = Depends(get_db), user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    query = db.query(StockAlertEntity)
    if status:
        query = query.filter(StockAlertEntity.status == status.upper())
    products = {p.id: p for p in db.query(ProductEntity).all()}
    alerts = query.order_by((StockAlertEntity.status == "OPEN").desc(), StockAlertEntity.id.desc()).limit(500).all()
    return [alertRow(a, products.get(a.product_id)) for a in alerts]


# El Administrador por Bodega revisa la alerta y deja una nota de qué hizo
@router.post("/{alert_id}/review")
def review_alert(alert_id: int, payload: AlertReview, db: Session = Depends(get_db),
                 user=Depends(requireRoles(ADMIN_BODEGA, ADMIN_GLOBAL))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    if len(payload.note.strip()) < 5:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Escribe qué revisaste o qué hiciste (mínimo 5 caracteres)")
    alert = db.query(StockAlertEntity).filter(StockAlertEntity.id == alert_id).with_for_update().first()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alerta no encontrada")
    if alert.status == "REVIEWED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="La alerta ya fue revisada por " + str(alert.reviewed_by))
    alert.status = "REVIEWED"
    alert.reviewed_by = user["username"]
    alert.reviewed_at = schemas_utils.current_utc_time()
    alert.review_note = payload.note.strip()
    db.commit()
    return alertRow(alert, db.get(ProductEntity, alert.product_id) if alert.product_id else None)
