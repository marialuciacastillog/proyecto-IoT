from sqlalchemy import Column, String, DateTime
from app.database import Base
from app.modules.shared.utils import schemas_utils


# Etapa B: avisos del catálogo que ya se aplicaron. Si SQS entrega el mismo aviso dos veces,
# el segundo se ignora (idempotencia por event_id).
class CatalogEventEntity(Base):
    __tablename__ = "catalog_events_applied"
    event_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False)
    result = Column(String, nullable=False)
    applied_at = Column(DateTime, default=schemas_utils.current_utc_time)
