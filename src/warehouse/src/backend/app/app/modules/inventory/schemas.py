from typing import Optional
from datetime import datetime, date
from decimal import Decimal
from pydantic import BaseModel, Field

class MovementCreate(BaseModel):
    product_id: int
    type: str = Field(..., description="PURCHASE, SALE, RECEIPT, SHIPMENT, RETURN (a mano); RESERVATION, RELEASE, RESERVATION_CONFIRM, TRANSFER_IN (sistema)")
    quantity: int = Field(..., gt=0)
    reference: Optional[str] = Field(None, max_length=120)
    # Lote (RF-11): en las entradas es el lote al que entra; en las salidas, el lote del que sale (opcional)
    lot_code: Optional[str] = None
    expiry_date: Optional[date] = None
    # Compras y ventas (RF-13). Si no se manda el precio, se usa el del catálogo
    unit_price: Optional[Decimal] = Field(None, ge=0)
    price_type: Optional[str] = None      # ventas: DETAL o MAYORISTA
    counterparty: Optional[str] = Field(None, max_length=120)  # proveedor o cliente

class MovementResponse(BaseModel):
    id: int
    product_id: int
    warehouse_id: str
    type: str
    quantity: int
    reference: Optional[str]
    created_by: str
    created_at: datetime
    unit_price: Optional[Decimal] = None
    total: Optional[Decimal] = None
    price_type: Optional[str] = None
    counterparty: Optional[str] = None
    lot_code: Optional[str] = None
    reservation_id: Optional[str] = None

    class Config:
        from_attributes = True

class InventoryResponse(BaseModel):
    product_id: int
    warehouse_id: str
    quantity: int
    reserved_quantity: int
    updated_at: datetime

    class Config:
        from_attributes = True
