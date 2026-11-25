from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from app.database import get_db
from sqlalchemy.orm import Session
from app.config import settings
from app.modules.inventory.schemas import MovementCreate, MovementResponse, InventoryResponse
from app.modules.inventory.inventory_service import InventoryService
from app.modules.auth.auth import requireRoles, checkWarehouse
from app.modules.auth.roles import ADMIN_BODEGA, ENCARGADO, ALL_ROLES

router = APIRouter(prefix="/inventory", tags=["Inventory"])

# Los tipos que una persona puede registrar a mano. TRANSFER_IN, TRANSFER_OUT y
# RESERVATION_CONFIRM solo los hace la saga de transferencias, nunca un usuario directo.
manualTypes = ["RECEIPT", "SHIPMENT", "RETURN", "RESERVATION", "RELEASE"]


@router.get("/{product_id}", response_model=InventoryResponse)
def get_inventory(product_id: int, db: Session = Depends(get_db), user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    service = InventoryService(db)
    return service.get_stock(product_id)


# Registran movimientos el Encargado de recepción y envío y el Administrador por Bodega (RF-09)
@router.post("/movements", response_model=MovementResponse, status_code=status.HTTP_201_CREATED)
def create_movement(payload: MovementCreate, db: Session = Depends(get_db),
                    user=Depends(requireRoles(ADMIN_BODEGA, ENCARGADO))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    if payload.type not in manualTypes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Tipo no permitido. Opciones: " + ", ".join(manualTypes))
    service = InventoryService(db)
    # El controlador solo delega y responde. No maneja commits ni fallas transaccionales de DB.
    return service.process_movement(payload, user["username"])


@router.get("/{product_id}/movements", response_model=List[MovementResponse])
def get_movements(product_id: int, db: Session = Depends(get_db), user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    service = InventoryService(db)
    return service.get_movements(product_id)
