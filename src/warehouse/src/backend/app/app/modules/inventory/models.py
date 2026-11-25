import datetime
from sqlalchemy import Column, Integer, String, DateTime, Date, Numeric, Boolean, UniqueConstraint
from app.database import Base
from app.modules.shared.utils import schemas_utils

class InventoryEntity(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, unique=True, nullable=False) # Desacoplado de FK fuerte si se prefiere, o apuntado directo
    warehouse_id = Column(String, nullable=False)
    quantity = Column(Integer, default=0)
    reserved_quantity = Column(Integer, default=0)
    updated_at = Column(DateTime, default=schemas_utils.current_utc_time, onupdate=schemas_utils.current_utc_time)

class InventoryMovementEntity(Base):
    __tablename__ = "inventory_movements"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, nullable=False)
    warehouse_id = Column(String, nullable=False)
    type = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    reference = Column(String)
    created_by = Column(String, nullable=False, default="sistema")  # Quién hizo el movimiento (RF-28)
    created_at = Column(DateTime, default=schemas_utils.current_utc_time)
    # Compras y ventas (RF-13): precio por unidad, total y a quién se le compró o vendió
    unit_price = Column(Numeric(14, 2))
    total = Column(Numeric(14, 2))
    price_type = Column(String)        # Ventas: DETAL o MAYORISTA
    counterparty = Column(String)      # Proveedor (compra) o cliente (venta)
    lot_code = Column(String)          # Lote o lotes que tocó el movimiento (RF-11)
    reservation_id = Column(String)    # Si salió de una reserva


# Lotes (RF-11, RF-12). Las unidades físicas de un producto están repartidas en lotes:
# la suma de los lotes es siempre disponible + reservado.
class LotEntity(Base):
    __tablename__ = "lots"
    __table_args__ = (UniqueConstraint("product_id", "lot_code"),)

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, nullable=False)
    lot_code = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=0)          # unidades que quedan
    initial_quantity = Column(Integer, nullable=False, default=0)  # unidades que entraron en total
    expiry_date = Column(Date)                                     # vencimiento (opcional)
    expired_alerted = Column(Boolean, nullable=False, default=False)
    created_by = Column(String, nullable=False, default="sistema")
    received_at = Column(DateTime(timezone=True), default=schemas_utils.current_utc_time)


# De qué lote salió o a qué lote entró cada movimiento (una venta puede tocar varios lotes)
class MovementLotEntity(Base):
    __tablename__ = "movement_lots"

    id = Column(Integer, primary_key=True, index=True)
    movement_id = Column(Integer, nullable=False, index=True)
    lot_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)


# Reservas con vencimiento. Cada reserva es su propio registro:
#   SALE     -> apartado para una venta, se vence a las 48 horas
#   MANUAL   -> apartado por otro motivo, vencimiento obligatorio (máximo 7 días)
#   TRANSFER -> lo aparta la saga de transferencias; la saga la cierra. Si a las 24 horas
#               sigue abierta, solo se avisa (no se libera sola, porque la saga la maneja)
class ReservationEntity(Base):
    __tablename__ = "reservations"

    id = Column(String, primary_key=True)          # RES-XXXXXX
    product_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)          # SALE, MANUAL, TRANSFER
    status = Column(String, nullable=False, default="ACTIVE")  # ACTIVE, SOLD, RELEASED, EXPIRED, CONSUMED
    reason = Column(String)
    counterparty = Column(String)                  # cliente (reservas de venta)
    reference = Column(String)                     # id de la transferencia (TRANSFER)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    alerted = Column(Boolean, nullable=False, default=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=schemas_utils.current_utc_time)
    closed_by = Column(String)
    closed_at = Column(DateTime(timezone=True))
    close_note = Column(String)
