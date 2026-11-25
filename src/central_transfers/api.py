import uuid
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor
from db import connectDatabase, changeStatus
from auth import requireRoles, checkWarehouse
from roles import ADMIN_GLOBAL, ADMIN_BODEGA, ALL_ROLES, WAREHOUSE_ROLES

app = FastAPI(title="Transferencias entre bodegas (Fase 6)")
defaultWarehouses = ["wh-001", "wh-002", "wh-003"]


# Etapa B: las bodegas válidas son las ACTIVAS del registro de la central (catálogo).
# Si el catálogo todavía no ha creado su tabla, se usan las 3 de siempre.
def activeWarehouses(connection):
    warehouses = defaultWarehouses
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.warehouses') IS NOT NULL")
        if cursor.fetchone()[0]:
            cursor.execute("SELECT warehouse_id FROM warehouses WHERE status = 'ACTIVE'")
            warehouses = [row[0] for row in cursor.fetchall()]
    return warehouses


class TransferRequest(BaseModel):
    source_warehouse: str
    destination_warehouse: str
    product_id: str           # código del producto, por ejemplo P-001
    quantity: int = Field(..., gt=0)
    justification: Optional[str] = Field(None, max_length=300)   # RF-24: motivo de la transferencia


@app.get("/health")
def health():
    connection = connectDatabase()
    connection.close()
    return {"status": "ok"}


# Responde enseguida con 202: la transferencia la hace el orquestador por detrás
# Etapa A: la piden el Administrador Global o el Administrador por Bodega de la bodega ORIGEN
@app.post("/transfers", status_code=202)
def createTransfer(request: TransferRequest, user=Depends(requireRoles(ADMIN_GLOBAL, ADMIN_BODEGA))):
    checkWarehouse(user, request.source_warehouse)
    if request.source_warehouse == request.destination_warehouse:
        raise HTTPException(status_code=400, detail="El origen y el destino deben ser distintos")
    transferId = "TR-" + uuid.uuid4().hex[:8].upper()
    connection = connectDatabase()
    try:
        warehouses = activeWarehouses(connection)
        if request.source_warehouse not in warehouses or request.destination_warehouse not in warehouses:
            raise HTTPException(status_code=400, detail="Bodega inválida o archivada. Activas: " + ", ".join(warehouses))
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO transfers (transfer_id, source_warehouse, destination_warehouse,
                   product_id, quantity, status, requested_by, justification)
                   VALUES (%s, %s, %s, %s, %s, 'REQUESTED', %s, %s)""",
                (transferId, request.source_warehouse, request.destination_warehouse,
                 request.product_id, request.quantity, user["username"], request.justification or ""))
            cursor.execute(
                "INSERT INTO transfer_history (transfer_id, status, reason) VALUES (%s, 'REQUESTED', '')",
                (transferId,))
        connection.commit()
    finally:
        connection.close()
    return {"transfer_id": transferId, "status": "PROCESSING"}


# Los roles de bodega solo ven las transferencias donde su bodega es origen o destino
def canSee(user, transfer):
    allowed = user["role"] not in WAREHOUSE_ROLES
    if not allowed:
        myWarehouse = (user["warehouse_id"] or "").lower()
        allowed = myWarehouse in (transfer["source_warehouse"], transfer["destination_warehouse"])
    return allowed


@app.get("/transfers/{transfer_id}")
def getTransfer(transfer_id: str, user=Depends(requireRoles(*ALL_ROLES))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT transfer_id, source_warehouse, destination_warehouse, product_id,
                   quantity, status, reason, requested_by, justification, created_at, updated_at
                   FROM transfers WHERE transfer_id = %s""", (transfer_id,))
            transfer = cursor.fetchone()
            cursor.execute(
                """SELECT status, reason, created_at FROM transfer_history
                   WHERE transfer_id = %s ORDER BY id""", (transfer_id,))
            history = cursor.fetchall()
    finally:
        connection.close()
    if transfer is None or not canSee(user, transfer):
        raise HTTPException(status_code=404, detail="Transferencia no encontrada")
    transfer["history"] = history
    return transfer


@app.get("/transfers")
def listTransfers(user=Depends(requireRoles(*ALL_ROLES))):
    myWarehouse = None
    if user["role"] in WAREHOUSE_ROLES:
        myWarehouse = (user["warehouse_id"] or "").lower()
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT transfer_id, source_warehouse, destination_warehouse, product_id,
                   quantity, status, reason, requested_by, justification, updated_at FROM transfers
                   WHERE %s IS NULL OR source_warehouse = %s OR destination_warehouse = %s
                   ORDER BY created_at DESC LIMIT 20""", (myWarehouse, myWarehouse, myWarehouse))
            transfers = cursor.fetchall()
    finally:
        connection.close()
    return transfers
