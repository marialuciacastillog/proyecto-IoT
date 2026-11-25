from sqlalchemy import Column, String, Boolean, DateTime
from app.database import Base
from app.modules.shared.utils import schemas_utils

# Órdenes de transferencia que esta bodega ya procesó.
# La llave es "TR-xxx:RESERVE", "TR-xxx:RECEIVE"... así una orden repetida no se aplica dos veces.
class TransferCommandEntity(Base):
    __tablename__ = "transfer_commands"

    id = Column(String, primary_key=True)
    transfer_id = Column(String, nullable=False)
    command = Column(String, nullable=False)
    ok = Column(Boolean, nullable=False)
    reason = Column(String)
    created_at = Column(DateTime, default=schemas_utils.current_utc_time)
