from fastapi import FastAPI
from app.database import engine, Base
from app.modules.health.health_controller import router as health_controller
from app.modules.products.products_controller import router as products_controller
from app.modules.inventory.inventory_controller import router as inventory_controller
from app.modules.cache.cache_controller import router as cache_controller
from app.modules.transfers.models import TransferCommandEntity  # Fase 6: crea la tabla transfer_commands

import socket
import time
from fastapi import Request
from app.modules.shared.correlation import currentCorrelationId, newCorrelationId


# Fase 5: varias copias de la API arrancan a la vez; si dos crean las tablas al mismo tiempo,
# una falla. Por eso se reintenta unas veces.
def createTables():
    created = False
    attempts = 0
    while not created and attempts < 10:
        try:
            Base.metadata.create_all(bind=engine)
            created = True
        except Exception as error:
            attempts = attempts + 1
            print("No se pudieron crear las tablas, se reintenta:", error)
            time.sleep(2)


createTables()
instanceName = socket.gethostname()

app = FastAPI(
    title="Warehouse Cloud Platform — Refactored Modular Monolith",
    description="Fase 1 Corregida: Arquitectura limpia sin fugas transaccionales ni acoplamiento cruzado.",
    version="1.1.0"
)

app.include_router(health_controller)
app.include_router(products_controller)
app.include_router(inventory_controller)
app.include_router(cache_controller)


# Fase 5: cada respuesta dice qué copia de la API la atendió (para ver el balanceo)
@app.middleware("http")
async def addInstanceHeader(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Instance"] = instanceName
    return response


# Fase 3: cada petición lleva un correlationId. Si el cliente lo manda (encabezado X-Correlation-ID)
# se usa ese; si no, se crea uno. Se devuelve en la respuesta para que el cliente lo vea.
@app.middleware("http")
async def addCorrelationId(request: Request, call_next):
    correlationId = request.headers.get("X-Correlation-ID", "")
    if correlationId == "":
        correlationId = newCorrelationId()
    currentCorrelationId.set(correlationId)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlationId
    return response
