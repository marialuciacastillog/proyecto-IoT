# Vigilante de las DLQ. Cada minuto cuenta los mensajes de cada DLQ y, si hay más que la vez anterior,
# le manda una notificación al Administrador Global para que los revise.
import json
import os
import time
from datetime import datetime, timezone
import boto3
import psycopg2
from notify import createNotificationsSql, notify

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
intervalSeconds = int(os.environ.get("DLQ_CHECK_SECONDS", "60"))

createStateSql = """
    CREATE TABLE IF NOT EXISTS dlq_state (
        queue_name VARCHAR(100) PRIMARY KEY,
        last_count INTEGER NOT NULL,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
"""


def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": "dlq-monitor", "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False), flush=True)


def checkQueues(connection, sqsClient):
    with connection.cursor() as cursor:
        for url in sqsClient.list_queues(MaxResults=1000).get("QueueUrls", []):
            name = url.rsplit("/", 1)[-1]
            if name.endswith("-dlq"):
                attributes = sqsClient.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])
                count = int(attributes["Attributes"]["ApproximateNumberOfMessages"])
                cursor.execute("SELECT last_count FROM dlq_state WHERE queue_name = %s", (name,))
                row = cursor.fetchone()
                lastCount = row[0] if row else 0
                if count > lastCount:
                    newMessages = count - lastCount
                    notify(cursor, "DLQ", "ERROR", "Mensajes nuevos en " + name,
                           str(newMessages) + " mensaje(s) nuevo(s) en " + name + " (" + str(count) +
                           " en total). Revísalos en la vista DLQ: se pueden reprocesar o descartar.",
                           "ADMIN_GLOBAL", None, name)
                    logJson("WARNING", "dlq_new_messages", {"queue": name, "new": newMessages, "total": count})
                cursor.execute("""INSERT INTO dlq_state (queue_name, last_count) VALUES (%s, %s)
                                  ON CONFLICT (queue_name) DO UPDATE SET last_count = EXCLUDED.last_count,
                                  checked_at = clock_timestamp()""", (name, count))
    connection.commit()


def main():
    sqsClient = boto3.client("sqs", region_name=awsRegion)
    connection = None
    logJson("INFO", "monitor_started", {"interval_seconds": intervalSeconds})
    while True:
        try:
            if connection is None or connection.closed != 0:
                connection = psycopg2.connect(databaseUrl)
                with connection.cursor() as cursor:
                    cursor.execute(createNotificationsSql)
                    cursor.execute(createStateSql)
                connection.commit()
            checkQueues(connection, sqsClient)
        except Exception as error:
            logJson("ERROR", "check_failed", {"error": str(error)})
            if connection is not None and connection.closed == 0:
                connection.rollback()
        time.sleep(intervalSeconds)


if __name__ == "__main__":
    main()
