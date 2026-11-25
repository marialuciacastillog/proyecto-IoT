import uuid
from app.config import settings
from app.modules.sync.models import PendingEventEntity
from app.modules.products.models import ProductEntity
from app.modules.shared.correlation import getCorrelationId

# Guarda un evento pendiente. NO hace commit: se guarda en la misma transacción del movimiento.
# Se manda el código del producto (sku, por ejemplo P-001) y no el id interno,
# porque cada bodega numera sus productos por su lado y el id 1 de una bodega
# puede ser otro producto en otra bodega.
def savePendingEvent(db, productId, quantity, performedBy="sistema"):
    product = db.query(ProductEntity).filter(ProductEntity.id == productId).first()
    productCode = str(productId)
    if product is not None:
        productCode = product.sku
    pendingEvent = PendingEventEntity(
        event_id=str(uuid.uuid4()),
        warehouse_id=settings.WAREHOUSE_ID,
        event_type="INVENTORY_UPDATED",
        correlation_id=getCorrelationId(),
        product_id=productCode,
        quantity=quantity,
        performed_by=performedBy,
        status="PENDING"
    )
    db.add(pendingEvent)
    return pendingEvent
