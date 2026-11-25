from typing import List
from fastapi import APIRouter, Depends
from app.database import get_db
from sqlalchemy.orm import Session
from app.config import settings
from app.modules.products.schemas import ProductResponse
from app.modules.products.products_service import ProductService
from app.modules.auth.auth import requireRoles, checkWarehouse
from app.modules.auth.roles import ALL_ROLES

router = APIRouter(prefix="/products", tags=["Products"])


# Etapa B: los productos ya no se crean aquí. Los crea el Administrador Global en el catálogo de la
# central (RF-04) y el Administrador por Bodega elige cuáles maneja su bodega (RF-08). La bodega los
# recibe por el aviso PRODUCT_ASSIGNED (ver app/modules/catalog/worker.py).


@router.get("", response_model=List[ProductResponse])
def get_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db),
                 user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    service = ProductService(db)
    return service.list_products(skip, limit)


@router.get("/{id}", response_model=ProductResponse)
def get_product(id: int, db: Session = Depends(get_db), user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, settings.WAREHOUSE_ID)
    service = ProductService(db)
    return service.get_product(id)
