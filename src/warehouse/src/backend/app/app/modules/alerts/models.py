from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base
from app.modules.shared.utils import schemas_utils

# Alertas de la bodega (RF-15). Las crea el vigilante de la bodega y se avisan a la central.
#   OPEN     -> el problema sigue
#   RESOLVED -> el problema desapareció solo (el vigilante lo volvió a revisar y ya cuadra)
#   REVIEWED -> el Administrador por Bodega la revisó y dejó una nota
# alert_key evita repetir la misma alerta cada minuto mientras el problema sigue abierto.
class StockAlertEntity(Base):
    __tablename__ = "stock_alerts"

    id = Column(Integer, primary_key=True, index=True)
    alert_key = Column(String, nullable=False, index=True)
    alert_type = Column(String, nullable=False)
    level = Column(String, nullable=False)          # INFO, WARNING, ERROR
    product_id = Column(Integer)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    status = Column(String, nullable=False, default="OPEN")
    created_at = Column(DateTime(timezone=True), default=schemas_utils.current_utc_time)
    resolved_at = Column(DateTime(timezone=True))
    reviewed_by = Column(String)
    reviewed_at = Column(DateTime(timezone=True))
    review_note = Column(String)
