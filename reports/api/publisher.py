
import json
import os
import time

import boto3
import psycopg


DATABASE_URL = os.environ["DATABASE_URL"]
QUEUE_URL = os.environ["REPORTS_QUEUE_URL"]

sqs = boto3.client(
    "sqs",
    region_name="us-east-1"
)


def publish_pending_reports():
    published = 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    j.job_id,
                    j.report_type,
                    j.format
                FROM report_outbox o
                JOIN report_jobs j
                    ON j.job_id = o.job_id
                WHERE o.published_at IS NULL
                ORDER BY o.created_at
                FOR UPDATE OF o SKIP LOCKED
                LIMIT 10
                """
            )

            jobs = cursor.fetchall()

            for job_id, report_type, report_format in jobs:
                message = {
                    "job_id": str(job_id),
                    "report_type": report_type,
                    "format": report_format
                }

                sqs.send_message(
                    QueueUrl=QUEUE_URL,
                    MessageBody=json.dumps(message)
                )

                cursor.execute(
                    """
                    UPDATE report_outbox
                    SET published_at = NOW()
                    WHERE job_id = %s
                    """,
                    (job_id,)
                )

                cursor.execute(
                    """
                    UPDATE report_jobs
                    SET status = 'QUEUED',
                        updated_at = NOW()
                    WHERE job_id = %s
                    """,
                    (job_id,)
                )

                published += 1

    return published


def main():
    while True:
        try:
            total = publish_pending_reports()

            if total > 0:
                print(
                    f"Published {total} report request(s)",
                    flush=True
                )

        except Exception as error:
            print(
                f"Publisher error: {error}",
                flush=True
            )

        time.sleep(5)


if __name__ == "__main__":
    main()

