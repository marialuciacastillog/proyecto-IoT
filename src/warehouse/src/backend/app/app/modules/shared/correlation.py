# Fase 3: correlationId. Es un código que acompaña a una operación por todo el sistema
# (API -> pending_events -> SNS -> colas -> workers), para poder seguirle la pista en los logs.
import uuid
from contextvars import ContextVar

currentCorrelationId = ContextVar("currentCorrelationId", default="")


def newCorrelationId():
    return "COR-" + uuid.uuid4().hex[:8].upper()


# Si la petición no trae uno (por ejemplo, un movimiento hecho por un worker), se crea uno nuevo
def getCorrelationId():
    correlationId = currentCorrelationId.get()
    if correlationId == "":
        correlationId = newCorrelationId()
    return correlationId
