
import os
import re
import time
import uuid
from datetime import date
from typing import Optional

import psycopg

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import boto3

from worker.queries import REPORT_TYPES, FORMATS
from api.auth import requireRoles
from api.roles import ADMIN_GLOBAL, LIDER
from api import contracts, stats

# Los reportes los ven el Administrador Global y el Líder (RF-17, RF-30)
reportReaders = Depends(requireRoles(ADMIN_GLOBAL, LIDER))


app = FastAPI(title="IoT Reports API")
app.include_router(contracts.router)
app.include_router(stats.router)

DATABASE_URL = os.environ["DATABASE_URL"]
BUCKET_NAME = os.environ.get("REPORTS_BUCKET_NAME", "")
DOWNLOAD_SECONDS = 900  # el link de descarga dura 15 minutos

s3 = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))


class ReportRequest(BaseModel):
    report_type: str = "global_inventory"
    format: str = "csv"
    # Filtros de los reportes históricos (RF-29, RF-30). Los que no se mandan no filtran.
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    warehouse_id: Optional[str] = None
    product_id: Optional[str] = None


# Al arrancar: la columna de filtros de los reportes y la tabla de contratos
@app.on_event("startup")
def startup():
    ready = False
    attempts = 0
    while not ready and attempts < 20:
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                conn.execute("ALTER TABLE report_jobs ADD COLUMN IF NOT EXISTS params JSONB")
            contracts.createTables()
            ready = True
        except psycopg.Error as error:
            attempts = attempts + 1
            print("Esperando la base central:", error, flush=True)
            time.sleep(3)


# Revisa los filtros y devuelve solo los que el tipo de reporte acepta
def reportParams(request):
    allowed = REPORT_TYPES[request.report_type].get("filters", [])
    values = {
        "date_from": request.date_from.isoformat() if request.date_from else None,
        "date_to": request.date_to.isoformat() if request.date_to else None,
        "warehouse_id": (request.warehouse_id or "").strip().lower() or None,
        "product_id": (request.product_id or "").strip().upper() or None
    }
    params = {}
    for name, value in values.items():
        if value is not None and name not in allowed:
            raise HTTPException(status_code=400, detail="El reporte " + request.report_type + " no acepta el filtro " + name)
        if value is not None:
            params[name] = value
    if request.date_from and request.date_to and request.date_to < request.date_from:
        raise HTTPException(status_code=400, detail="La fecha 'hasta' no puede ser antes de la fecha 'desde'")
    if params.get("warehouse_id") and not re.match(r"^wh-[0-9]{3}$", params["warehouse_id"]):
        raise HTTPException(status_code=400, detail="La bodega debe tener la forma wh-001")
    return params


@app.get("/health")
def health():
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("SELECT 1")

        return {"status": "ok"}

    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )


@app.post("/reports", status_code=202)
def create_report(request: ReportRequest, user=reportReaders):
    if request.report_type not in REPORT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unknown report_type. Options: " + ", ".join(REPORT_TYPES)
        )

    if request.format not in FORMATS:
        raise HTTPException(
            status_code=400,
            detail="Unknown format. Options: " + ", ".join(FORMATS)
        )

    params = reportParams(request)
    job_id = uuid.uuid4()

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO report_jobs
                        (job_id, report_type, format, status, params)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        request.report_type,
                        request.format,
                        "PENDING",
                        Jsonb(params)
                    )
                )

                cursor.execute(
                    """
                    INSERT INTO report_outbox (job_id)
                    VALUES (%s)
                    """,
                    (job_id,)
                )

    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Could not save report request"
        )

    return {
        "job_id": str(job_id),
        "status": "PENDING",
        "params": params
    }


# Qué reportes y formatos se pueden pedir
@app.get("/reports/types")
def list_report_types(user=reportReaders):
    return {
        "formats": list(FORMATS),
        "report_types": [
            {"report_type": name, "description": info["description"], "filters": info.get("filters", [])}
            for name, info in REPORT_TYPES.items()
        ]
    }


# Los últimos reportes pedidos (se puede filtrar por estado: PENDING, QUEUED, PROCESSING, COMPLETED, FAILED)
@app.get("/reports/jobs")
def list_report_jobs(limit: int = 20, status: str | None = None, user=reportReaders):
    limit = max(1, min(limit, 100))
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            if status is None:
                jobs = conn.execute(
                    """
                    SELECT job_id, report_type, format, status, s3_key, params, created_at, updated_at
                    FROM report_jobs
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,)
                ).fetchall()
            else:
                jobs = conn.execute(
                    """
                    SELECT job_id, report_type, format, status, s3_key, params, created_at, updated_at
                    FROM report_jobs
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (status.upper(), limit)
                ).fetchall()

    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    return {"count": len(jobs), "jobs": jobs}


# Link temporal de S3 para descargar el reporte desde el navegador (sin la CLI)
@app.get("/reports/jobs/{job_id}/download")
def download_report(job_id: uuid.UUID, user=reportReaders):
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            job = conn.execute(
                "SELECT status, s3_key FROM report_jobs WHERE job_id = %s",
                (job_id,)
            ).fetchone()

    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    if job is None:
        raise HTTPException(status_code=404, detail="Report job not found")

    if job["status"] != "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail="Report is not ready yet, status: " + job["status"]
        )

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": job["s3_key"]},
        ExpiresIn=DOWNLOAD_SECONDS
    )

    return {"job_id": str(job_id), "download_url": url, "expires_in_seconds": DOWNLOAD_SECONDS}


@app.get("/reports/jobs/{job_id}")
def get_report_status(job_id: uuid.UUID, user=reportReaders):
    try:
        with psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row
        ) as conn:
            job = conn.execute(
                """
                SELECT
                    job_id,
                    report_type,
                    format,
                    status,
                    s3_key,
                    error_message,
                    created_at,
                    updated_at
                FROM report_jobs
                WHERE job_id = %s
                """,
                (job_id,)
            ).fetchone()

    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Report job not found"
        )

    return job



# Fase 7: inventario consolidado de un producto en todas las bodegas.
# Lee global_inventory, que llena el worker de sincronización.
@app.get("/global/inventory/{product_id}")
def get_global_inventory(product_id: str, user=reportReaders):
    try:
        with psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row
        ) as conn:
            rows = conn.execute(
                """
                SELECT warehouse_id, quantity, updated_at
                FROM global_inventory
                WHERE product_id = %s
                ORDER BY warehouse_id
                """,
                (product_id,)
            ).fetchall()

    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Product not found in any warehouse"
        )

    return {
        "product_id": product_id,
        "total_quantity": sum(row["quantity"] for row in rows),
        "warehouses": rows
    }
