
import os
import uuid

import psycopg

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from psycopg.rows import dict_row
import boto3

from worker.queries import REPORT_TYPES, FORMATS
from api.auth import requireRoles
from api.roles import ADMIN_GLOBAL, LIDER

# Los reportes los ven el Administrador Global y el Líder (RF-17, RF-30)
reportReaders = Depends(requireRoles(ADMIN_GLOBAL, LIDER))


app = FastAPI(title="IoT Reports API")

DATABASE_URL = os.environ["DATABASE_URL"]
BUCKET_NAME = os.environ.get("REPORTS_BUCKET_NAME", "")
DOWNLOAD_SECONDS = 900  # el link de descarga dura 15 minutos

s3 = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))


class ReportRequest(BaseModel):
    report_type: str = "global_inventory"
    format: str = "csv"


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

    job_id = uuid.uuid4()

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO report_jobs
                        (job_id, report_type, format, status)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        request.report_type,
                        request.format,
                        "PENDING"
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
        "status": "PENDING"
    }


# Qué reportes y formatos se pueden pedir
@app.get("/reports/types")
def list_report_types(user=reportReaders):
    return {
        "formats": list(FORMATS),
        "report_types": [
            {"report_type": name, "description": info["description"]}
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
                    SELECT job_id, report_type, format, status, s3_key, created_at, updated_at
                    FROM report_jobs
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,)
                ).fetchall()
            else:
                jobs = conn.execute(
                    """
                    SELECT job_id, report_type, format, status, s3_key, created_at, updated_at
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
