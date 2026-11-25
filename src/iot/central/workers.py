# Fase 12: los tres consumidores de lecturas IoT. Cada uno lee SU cola (copia de SNS):
#   python workers.py storage    -> guarda cada lectura (sin duplicar) y el último estado de cada sensor
#   python workers.py alerts     -> revisa reglas y guarda alertas
#   python workers.py analytics  -> promedios, mínimos y máximos por minuto
# Todos son idempotentes: si llega el mismo event_id dos veces, la segunda no cambia nada.
import json
import os
import sys
import time
from datetime import datetime, timezone
import boto3
import psycopg2

role = sys.argv[1] if len(sys.argv) > 1 else "storage"
databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
queueName = "iot-" + role

# Reglas de alerta: tipo de sensor -> (límite, mensaje)
alertRules = {
    "TEMP": (30.0, "Temperatura alta"),
    "HUM": (85.0, "Humedad alta"),
    "ENER": (5.5, "Consumo de energía alto")
}

createSql = {
    "storage": """
        CREATE TABLE IF NOT EXISTS iot_events (
            event_id        VARCHAR(80) PRIMARY KEY,
            sequence_number INTEGER NOT NULL,
            warehouse_id    VARCHAR(20) NOT NULL,
            sensor_id       VARCHAR(20) NOT NULL,
            sensor_type     VARCHAR(20),
            value           DOUBLE PRECISION NOT NULL,
            event_time      TIMESTAMPTZ NOT NULL,
            ingested_at     TIMESTAMPTZ,
            stored_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            out_of_order    BOOLEAN NOT NULL DEFAULT FALSE
        );
        CREATE INDEX IF NOT EXISTS idx_iot_events_stored ON iot_events (stored_at);
        CREATE TABLE IF NOT EXISTS iot_sensor_state (
            warehouse_id  VARCHAR(20) NOT NULL,
            sensor_id     VARCHAR(20) NOT NULL,
            last_sequence INTEGER NOT NULL,
            last_value    DOUBLE PRECISION NOT NULL,
            last_time     TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (warehouse_id, sensor_id)
        );
        CREATE TABLE IF NOT EXISTS iot_counters (
            name  VARCHAR(40) PRIMARY KEY,
            value BIGINT NOT NULL DEFAULT 0
        );
    """,
    "alerts": """
        CREATE TABLE IF NOT EXISTS iot_alerts (
            event_id     VARCHAR(80) PRIMARY KEY,
            warehouse_id VARCHAR(20) NOT NULL,
            sensor_id    VARCHAR(20) NOT NULL,
            rule         VARCHAR(60) NOT NULL,
            value        DOUBLE PRECISION NOT NULL,
            event_time   TIMESTAMPTZ NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
    """,
    "analytics": """
        CREATE TABLE IF NOT EXISTS iot_analytics_seen (
            event_id VARCHAR(80) PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS iot_analytics (
            warehouse_id VARCHAR(20) NOT NULL,
            sensor_type  VARCHAR(20) NOT NULL,
            minute       TIMESTAMPTZ NOT NULL,
            readings     INTEGER NOT NULL,
            total        DOUBLE PRECISION NOT NULL,
            min_value    DOUBLE PRECISION NOT NULL,
            max_value    DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (warehouse_id, sensor_type, minute)
        );
    """
}


def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": "iot-" + role + "-worker", "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line), flush=True)


def connectDatabase():
    connection = None
    while connection is None:
        try:
            connection = psycopg2.connect(databaseUrl)
        except Exception as error:
            logJson("ERROR", "database_unavailable", {"error": str(error)})
            time.sleep(3)
    return connection


def findQueueUrl(sqsClient):
    queueUrl = None
    while queueUrl is None:
        try:
            queueUrl = sqsClient.get_queue_url(QueueName=queueName)["QueueUrl"]
        except Exception as error:
            logJson("ERROR", "queue_lookup_failed", {"queue": queueName, "error": str(error)})
            time.sleep(5)
    return queueUrl


def addCounter(cursor, name):
    cursor.execute("""INSERT INTO iot_counters (name, value) VALUES (%s, 1)
                      ON CONFLICT (name) DO UPDATE SET value = iot_counters.value + 1""", (name,))


# Storage: guarda la lectura una sola vez. El estado del sensor solo se cambia si la lectura
# es más nueva (sequence_number mayor), así una lectura atrasada no pisa a una reciente.
def handleStorage(cursor, event):
    result = "GUARDADO"
    cursor.execute("SELECT last_sequence FROM iot_sensor_state WHERE warehouse_id = %s AND sensor_id = %s",
                   (event["warehouse_id"], event["sensor_id"]))
    row = cursor.fetchone()
    outOfOrder = row is not None and event["sequence_number"] < row[0]
    cursor.execute("""
        INSERT INTO iot_events (event_id, sequence_number, warehouse_id, sensor_id, sensor_type, value,
                                event_time, ingested_at, out_of_order)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (event_id) DO NOTHING
    """, (event["event_id"], event["sequence_number"], event["warehouse_id"], event["sensor_id"],
          event.get("sensor_type"), event["value"], event["timestamp"], event.get("ingested_at"), outOfOrder))
    if cursor.rowcount == 0:
        result = "DUPLICADO"
        addCounter(cursor, "duplicates")
    else:
        if outOfOrder:
            result = "FUERA_DE_ORDEN"
            addCounter(cursor, "out_of_order")
        cursor.execute("""
            INSERT INTO iot_sensor_state (warehouse_id, sensor_id, last_sequence, last_value, last_time)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (warehouse_id, sensor_id) DO UPDATE
            SET last_sequence = EXCLUDED.last_sequence, last_value = EXCLUDED.last_value, last_time = EXCLUDED.last_time
            WHERE iot_sensor_state.last_sequence < EXCLUDED.last_sequence
        """, (event["warehouse_id"], event["sensor_id"], event["sequence_number"], event["value"], event["timestamp"]))
    return result


def handleAlerts(cursor, event):
    result = "SIN_ALERTA"
    prefix = event["sensor_id"].split("-")[0]
    if prefix in alertRules and event["value"] > alertRules[prefix][0]:
        cursor.execute("""
            INSERT INTO iot_alerts (event_id, warehouse_id, sensor_id, rule, value, event_time)
            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING
        """, (event["event_id"], event["warehouse_id"], event["sensor_id"], alertRules[prefix][1],
              event["value"], event["timestamp"]))
        result = "ALERTA" if cursor.rowcount == 1 else "DUPLICADO"
    return result


def handleAnalytics(cursor, event):
    result = "DUPLICADO"
    cursor.execute("INSERT INTO iot_analytics_seen (event_id) VALUES (%s) ON CONFLICT DO NOTHING", (event["event_id"],))
    if cursor.rowcount == 1:
        cursor.execute("""
            INSERT INTO iot_analytics (warehouse_id, sensor_type, minute, readings, total, min_value, max_value)
            VALUES (%s, %s, date_trunc('minute', %s::timestamptz), 1, %s, %s, %s)
            ON CONFLICT (warehouse_id, sensor_type, minute) DO UPDATE
            SET readings = iot_analytics.readings + 1,
                total = iot_analytics.total + EXCLUDED.total,
                min_value = LEAST(iot_analytics.min_value, EXCLUDED.min_value),
                max_value = GREATEST(iot_analytics.max_value, EXCLUDED.max_value)
        """, (event["warehouse_id"], event.get("sensor_type") or "unknown", event["timestamp"],
              event["value"], event["value"], event["value"]))
        result = "AGREGADO"
    return result


handlers = {"storage": handleStorage, "alerts": handleAlerts, "analytics": handleAnalytics}


def main():
    connection = connectDatabase()
    with connection.cursor() as cursor:
        cursor.execute(createSql[role])
    connection.commit()
    sqsClient = boto3.client("sqs", region_name=awsRegion)
    queueUrl = findQueueUrl(sqsClient)
    logJson("INFO", "worker_started", {"queue": queueUrl})
    counts = {}
    lastReport = time.time()
    while True:
        if connection.closed != 0:
            connection = connectDatabase()
        try:
            response = sqsClient.receive_message(QueueUrl=queueUrl, MaxNumberOfMessages=10, WaitTimeSeconds=20)
            for message in response.get("Messages", []):
                try:
                    event = json.loads(message["Body"])
                    with connection.cursor() as cursor:
                        result = handlers[role](cursor, event)
                    connection.commit()
                    counts[result] = counts.get(result, 0) + 1
                    sqsClient.delete_message(QueueUrl=queueUrl, ReceiptHandle=message["ReceiptHandle"])
                except Exception as error:
                    if connection.closed == 0:
                        connection.rollback()
                    logJson("ERROR", "processing_failed", {"error": str(error)})
        except Exception as error:
            logJson("ERROR", "queue_unavailable", {"error": str(error)})
            time.sleep(5)
        # Cada 30 s deja una línea con lo que lleva procesado (en vez de una línea por lectura)
        if time.time() - lastReport >= 30:
            logJson("INFO", "worker_stats", counts)
            lastReport = time.time()


if __name__ == "__main__":
    main()
