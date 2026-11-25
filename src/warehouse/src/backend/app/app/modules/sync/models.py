from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base
from app.modules.shared.utils import schemas_utils

# Tabla de pendientes: cada movimiento deja aquí un evento para enviar a SQS
class PendingEventEntity(Base):
    __tablename__ = "pending_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String, unique=True, nullable=False)
    warehouse_id = Column(String, nullable=False)
    product_id = Column(String, nullable=False)
    event_type = Column(String, nullable=False, default="INVENTORY_UPDATED")  # Fase 3: tipo de evento
    correlation_id = Column(String, nullable=True)  # Fase 3: para seguir la operación de punta a punta
    quantity = Column(Integer, nullable=False)  # cantidad TOTAL después del movimiento
    performed_by = Column(String, nullable=False, default="sistema")  # Usuario que hizo el movimiento
    status = Column(String, nullable=False, default="PENDING")  # PENDING o SENT
    created_at = Column(DateTime, default=schemas_utils.current_utc_time)
    sent_at = Column(DateTime, nullable=True)
