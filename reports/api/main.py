
import os
import uuid

import psycopg

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from psycopg.rows import dict_row


app = FastAPI(title="IoT Reports API")

DATABASE_URL = os.environ["DATABASE_URL"]


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
def create_report(request: ReportRequest):
    if (request.report_type != "global_inventory"
            or request.format != "csv"):
        raise HTTPException(
            status_code=400,
            detail="Only global_inventory in csv format is supported"
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


@app.get("/reports/jobs/{job_id}")
def get_report_status(job_id: uuid.UUID):
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
def get_global_inventory(product_id: str):
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
