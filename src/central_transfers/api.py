import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor
from db import connectDatabase, changeStatus

app = FastAPI(title="Transferencias entre bodegas (Fase 6)")
warehouses = ["wh-001", "wh-002", "wh-003"]


class TransferRequest(BaseModel):
    source_warehouse: str
    destination_warehouse: str
    product_id: str           # código del producto, por ejemplo P-001
    quantity: int = Field(..., gt=0)


@app.get("/health")
def health():
    connection = connectDatabase()
    connection.close()
    return {"status": "ok"}


# Responde enseguida con 202: la transferencia la hace el orquestador por detrás
@app.post("/transfers", status_code=202)
def createTransfer(request: TransferRequest):
    if request.source_warehouse not in warehouses or request.destination_warehouse not in warehouses:
        raise HTTPException(status_code=400, detail="Bodega inválida. Usa wh-001, wh-002 o wh-003")
    if request.source_warehouse == request.destination_warehouse:
        raise HTTPException(status_code=400, detail="El origen y el destino deben ser distintos")
    transferId = "TR-" + uuid.uuid4().hex[:8].upper()
    connection = connectDatabase()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO transfers (transfer_id, source_warehouse, destination_warehouse,
                   product_id, quantity, status) VALUES (%s, %s, %s, %s, %s, 'REQUESTED')""",
                (transferId, request.source_warehouse, request.destination_warehouse,
                 request.product_id, request.quantity))
            cursor.execute(
                "INSERT INTO transfer_history (transfer_id, status, reason) VALUES (%s, 'REQUESTED', '')",
                (transferId,))
        connection.commit()
    finally:
        connection.close()
    return {"transfer_id": transferId, "status": "PROCESSING"}


@app.get("/transfers/{transfer_id}")
def getTransfer(transfer_id: str):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT transfer_id, source_warehouse, destination_warehouse, product_id,
                   quantity, status, reason, created_at, updated_at
                   FROM transfers WHERE transfer_id = %s""", (transfer_id,))
            transfer = cursor.fetchone()
            cursor.execute(
                """SELECT status, reason, created_at FROM transfer_history
                   WHERE transfer_id = %s ORDER BY id""", (transfer_id,))
            history = cursor.fetchall()
    finally:
        connection.close()
    if transfer is None:
        raise HTTPException(status_code=404, detail="Transferencia no encontrada")
    transfer["history"] = history
    return transfer


@app.get("/transfers")
def listTransfers():
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT transfer_id, source_warehouse, destination_warehouse, product_id,
                   quantity, status, reason, updated_at FROM transfers ORDER BY created_at DESC LIMIT 20""")
            transfers = cursor.fetchall()
    finally:
        connection.close()
    return transfers
