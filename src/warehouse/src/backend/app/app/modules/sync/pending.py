import uuid
from app.config import settings
from app.modules.sync.models import PendingEventEntity

# Guarda un evento pendiente. NO hace commit: se guarda en la misma transacción del movimiento
def savePendingEvent(db, productId, quantity):
    pendingEvent = PendingEventEntity(
        event_id=str(uuid.uuid4()),
        warehouse_id=settings.WAREHOUSE_ID,
        product_id=str(productId),
        quantity=quantity,
        status="PENDING"
    )
    db.add(pendingEvent)
    return pendingEvent
