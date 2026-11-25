

import csv
import io
import json
import logging
import os
import time
import uuid

import boto3
import psycopg
from botocore.exceptions import ClientError
from psycopg.rows import dict_row

from worker.queries import REPORT_TYPES, FORMATS


DATABASE_URL = os.environ["DATABASE_URL"]
QUEUE_URL = os.environ["REPORTS_QUEUE_URL"]
BUCKET_NAME = os.environ["REPORTS_BUCKET_NAME"]

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

# Debe coincidir con el visibility timeout que
# usamos al recibir mensajes de SQS.
VISIBILITY_SECONDS = int(
    os.getenv("REPORTS_VISIBILITY_SECONDS", "120")
)

if VISIBILITY_SECONDS < 1 or VISIBILITY_SECONDS > 43200:
    raise ValueError("Invalid SQS visibility timeout")

sqs = boto3.client("sqs", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logger = logging.getLogger("reports.worker")


# Corre la consulta del tipo de reporte y arma el archivo en el formato pedido
def generate_report(report_type, report_format):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute(REPORT_TYPES[report_type]["sql"])
            columns = [column.name for column in cursor.description]
            rows = cursor.fetchall()

    if report_format == "json":
        records = [dict(zip(columns, row)) for row in rows]
        content = json.dumps(records, indent=2, default=str, ensure_ascii=False)
    else:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)
        content = output.getvalue()

    return content, len(rows)


def get_job(job_id):
    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    ) as conn:
        return conn.execute(
            """
            SELECT job_id, report_type, format,
                   status, s3_key, lease_until
            FROM report_jobs
            WHERE job_id = %s
            """,
            (job_id,)
        ).fetchone()


def claim_job(job_id):
    token = uuid.uuid4()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE report_jobs
                SET status = 'PROCESSING',
                    processing_token = %s,
                    lease_until =
                        NOW() + (%s * INTERVAL '1 second'),
                    attempt_count = attempt_count + 1,
                    updated_at = NOW()
                WHERE job_id = %s
                  AND (
                    status IN ('PENDING', 'QUEUED', 'FAILED')
                    OR (
                      status = 'PROCESSING'
                      AND lease_until < NOW()
                    )
                  )
                RETURNING job_id
                """,
                (token, VISIBILITY_SECONDS, job_id)
            )

            claimed = cursor.fetchone() is not None

    return token if claimed else None


def mark_completed(job_id, token, s3_key):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE report_jobs
                SET status = 'COMPLETED',
                    s3_key = %s,
                    processing_token = NULL,
                    lease_until = NULL,
                    error_message = NULL,
                    updated_at = NOW()
                WHERE job_id = %s
                  AND processing_token = %s
                  AND status = 'PROCESSING'
                  AND lease_until > NOW()
                RETURNING job_id
                """,
                (s3_key, job_id, token)
            )

            if cursor.fetchone() is None:
                raise RuntimeError(
                    "Processing lease expired or ownership lost"
                )


def record_failure(job_id, token, error):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE report_jobs
                SET error_message = %s,
                    last_error_at = NOW(),
                    status = 'QUEUED',
                    processing_token = NULL,
                    lease_until = NULL,
                    updated_at = NOW()
                WHERE job_id = %s
                  AND processing_token = %s
                  AND status = 'PROCESSING'
                """,
                (str(error)[:2000], job_id, token)
            )


def delete_message(receipt_handle):
    sqs.delete_message(
        QueueUrl=QUEUE_URL,
        ReceiptHandle=receipt_handle
    )


def process_message(message):
    receipt_handle = message["ReceiptHandle"]
    body = json.loads(message["Body"])

    job_id = uuid.UUID(body["job_id"])
    report_type = body["report_type"]
    report_format = body["format"]

    job = get_job(job_id)

    if job is None:
        raise ValueError(f"Unknown job: {job_id}")

    if (
        job["report_type"] != report_type
        or job["format"] != report_format
    ):
        raise ValueError("Message does not match saved job")

    if job["status"] == "COMPLETED":
        s3_key = job["s3_key"]

        if not s3_key:
            raise ValueError("Completed job has no S3 key")

        s3.head_object(
            Bucket=BUCKET_NAME,
            Key=s3_key
        )

        logger.info("Already completed: %s", job_id)
        delete_message(receipt_handle)
        return

    if report_type not in REPORT_TYPES or report_format not in FORMATS:
        raise ValueError("Unsupported report type or format")

    token = claim_job(job_id)

    if token is None:
        logger.info(
            "Job %s is owned by another worker",
            job_id
        )

        # No borramos el mensaje.
        # Volverá a estar disponible.
        return

    try:
        logger.info("Processing job %s", job_id)

        content, row_count = generate_report(report_type, report_format)
        s3_key = f"reports/{report_type}/{job_id}.{report_format}"

        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=content.encode("utf-8"),
            ContentType=FORMATS[report_format]
        )

        logger.info("Report %s has %s rows", job_id, row_count)

        mark_completed(job_id, token, s3_key)

        logger.info(
            "Completed job %s: s3://%s/%s",
            job_id, BUCKET_NAME, s3_key
        )

    except Exception as error:
        logger.exception("Job failed: %s", job_id)

        try:
            record_failure(job_id, token, error)
        except Exception:
            logger.exception(
                "Could not record failure for %s",
                job_id
            )

        raise

    # Solo confirmamos el mensaje después de que
    # S3 y PostgreSQL completaron el trabajo.
    delete_message(receipt_handle)


def main():
    logger.info("Report worker started")

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=VISIBILITY_SECONDS,
                MessageSystemAttributeNames=[
                    "ApproximateReceiveCount"
                ]
            )

            for message in response.get("Messages", []):
                receive_count = message.get(
                    "Attributes", {}
                ).get("ApproximateReceiveCount", "?")

                logger.info(
                    "SQS message received, attempt=%s",
                    receive_count
                )

                try:
                    process_message(message)

                except Exception:
                    logger.exception(
                        "Message not acknowledged; SQS may retry"
                    )

        except KeyboardInterrupt:
            logger.info("Worker stopped")
            return

        except Exception:
            logger.exception("Worker polling error")
            time.sleep(5)


if __name__ == "__main__":
    main()

