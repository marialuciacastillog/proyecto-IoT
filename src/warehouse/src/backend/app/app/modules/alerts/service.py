# Crear y cerrar alertas. No hacen commit: van en la misma transacción de quien las llama.
# Cada alerta nueva deja un evento STOCK_ALERT en pending_events: el publicador lo manda a la central
# (SNS -> sync-queue) y la central lo convierte en notificación para el Admin Global y el de la bodega.
# Si la central está caída, la alerta espera en pending_events y llega después.
from app.modules.alerts.models import StockAlertEntity
from app.modules.inventory.models import InventoryEntity
from app.modules.sync.pending import savePendingEvent
from app.modules.shared.utils import schemas_utils


def currentQuantity(db, productId):
    quantity = 0
    if productId is not None:
        inventory = db.query(InventoryEntity).filter(InventoryEntity.product_id == productId).first()
        if inventory is not None:
            quantity = inventory.quantity
    return quantity


def sendToCentral(db, alert, title, level, message):
    savePendingEvent(db, alert.product_id, currentQuantity(db, alert.product_id), "sistema:vigilante", "STOCK_ALERT", {
        "alertId": alert.id,
        "alertType": alert.alert_type,
        "level": level,
        "title": title,
        "message": message
    })


# Devuelve la alerta creada, o None si ya había una sin resolver con la misma llave
# (abierta, o revisada por alguien pero con el problema todavía ahí)
def raiseAlert(db, key, alertType, level, productId, title, message):
    created = None
    openAlert = db.query(StockAlertEntity).filter(StockAlertEntity.alert_key == key,
                                                  StockAlertEntity.resolved_at.is_(None)).first()
    if openAlert is None:
        created = StockAlertEntity(alert_key=key, alert_type=alertType, level=level, product_id=productId,
                                   title=title, message=message, status="OPEN")
        db.add(created)
        db.flush()
        sendToCentral(db, created, title, level, message)
    return created


# El problema ya no está: las alertas sin resolver con esa llave quedan resueltas y se avisa a la central.
# Si alguien ya la había revisado, sigue diciendo REVIEWED (con su nota), pero con la fecha en que se resolvió.
def resolveAlerts(db, key):
    resolved = 0
    openAlerts = db.query(StockAlertEntity).filter(StockAlertEntity.alert_key == key,
                                                   StockAlertEntity.resolved_at.is_(None)).all()
    for alert in openAlerts:
        if alert.status == "OPEN":
            alert.status = "RESOLVED"
        alert.resolved_at = schemas_utils.current_utc_time()
        sendToCentral(db, alert, "Resuelta: " + alert.title, "INFO",
                     "El vigilante volvió a revisar y el problema ya no está. Antes: " + alert.message)
        resolved = resolved + 1
    return resolved
