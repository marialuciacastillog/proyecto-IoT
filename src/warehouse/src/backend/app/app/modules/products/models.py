import datetime
from sqlalchemy import Column, Integer, String, DateTime, Numeric
from app.database import Base
from app.modules.shared.utils import schemas_utils

class ProductEntity(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String)
    category = Column(String)
    unit = Column(String)
    # Etapa B: datos que vienen del catálogo global de la central (RF-04)
    supplier = Column(String)
    cost = Column(Numeric(14, 2))
    sale_price = Column(Numeric(14, 2))
    wholesale_price = Column(Numeric(14, 2))
    created_at = Column(DateTime, default=schemas_utils.current_utc_time)
    updated_at = Column(DateTime, default=schemas_utils.current_utc_time, onupdate=schemas_utils.current_utc_time)
