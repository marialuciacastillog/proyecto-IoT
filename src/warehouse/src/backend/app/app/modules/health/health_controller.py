import socket
from fastapi import APIRouter

router = APIRouter(tags=["System"])


# El balanceador usa /health para saber si esta copia está viva
@router.get("/health")
def health_check():
    return {"status": "ok", "instance": socket.gethostname()}
