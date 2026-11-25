import json
import os
import time
import boto3
import psycopg2
from datetime import datetime, timezone

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
queueUrl = os.environ.get("SYNC_QUEUE_URL", "")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
# Fase 10: la DLQ se llama igual que la cola + "-dlq" (así se crea en Terraform)
dlqUrl = os.environ.get("DLQ_URL", queueUrl + "-dlq")
SERVICE = "sync-worker"
requiredFields = ["eventId", "warehouseId", "productId", "quantity", "createdAt"]

# Fase 11: logs estructurados (una línea JSON por evento)
def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": SERVICE, "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False), flush=True)


# Guarda la cantidad de la bodega. Si ya existe, solo la cambia si el evento es más nuevo
# (así un mensaje viejo que llegue tarde no pisa uno más reciente).
saveInventorySql = """
    INSERT INTO global_inventory (warehouse_id, product_id, quantity, updated_at)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (warehouse_id, product_id) DO UPDATE
    SET quantity = EXCLUDED.quantity, updated_at = EXCLUDED.updated_at
    WHERE global_inventory.updated_at < EXCLUDED.updated_at
"""

# Fase 8: historial de lo que llega de las bodegas. El proceso Batch lee de aquí.
createHistorySql = """
    CREATE TABLE IF NOT EXISTS sync_history (
        event_id     VARCHAR(64) PRIMARY KEY,
        warehouse_id VARCHAR(20) NOT NULL,
        product_id   VARCHAR(50) NOT NULL,
        quantity     INTEGER NOT NULL,
        created_at   TIMESTAMP NOT NULL
    )
"""

saveHistorySql = """
    INSERT INTO sync_history (event_id, warehouse_id, product_id, quantity, created_at)
    VALUES (%s, %s, %s, %s, %s)
"""

saveProcessedSql = """
    INSERT INTO processed_events (event_id) VALUES (%s)
    ON CONFLICT (event_id) DO NOTHING
"""


def connectDatabase():
    connection = None
    while connection is None:
        try:
            connection = psycopg2.connect(databaseUrl)
        except Exception as error:
            logJson("ERROR", "database_unavailable", {"message": "Esperando la base de datos central", "error": str(error)})
            time.sleep(3)
    return connection


# Devuelve qué pasó con el evento:
#   "GUARDADO" -> era nuevo y se actualizó global_inventory
#   "VIEJO"    -> era nuevo, pero ya había un dato más reciente, así que no se cambia la cantidad
#   "REPETIDO" -> ya se había procesado antes
# Todo se guarda en la misma transacción: o se guarda todo o nada.
def applyEvent(connection, event):
    result = "REPETIDO"
    with connection.cursor() as cursor:
        cursor.execute(saveProcessedSql, (event["eventId"],))
        if cursor.rowcount == 1:
            cursor.execute(saveHistorySql, (
                event["eventId"],
                event["warehouseId"],
                event["productId"],
                event["quantity"],
                event["createdAt"]
            ))
            cursor.execute(saveInventorySql, (
                event["warehouseId"],
                event["productId"],
                event["quantity"],
                event["createdAt"]
            ))
            if cursor.rowcount == 1:
                result = "GUARDADO"
            else:
                result = "VIEJO"
    connection.commit()
    return result


# Fase 10: revisa el mensaje ANTES de procesarlo.
# Devuelve el evento y un texto con el problema (vacío si está bien).
# Un mensaje mal formado nunca se va a arreglar reintentando, por eso no se reintenta.
def checkEvent(body):
    event = None
    problem = ""
    try:
        event = json.loads(body)
    except Exception:
        problem = "JSON inválido"
    if problem == "" and not isinstance(event, dict):
        problem = "el mensaje no es un objeto JSON"
    if problem == "":
        for field in requiredFields:
            if field not in event and problem == "":
                problem = "falta el campo " + field
    if problem == "" and not isinstance(event["quantity"], int):
        problem = "quantity no es un número entero"
    return event, problem


# Manda el mensaje a la DLQ con el motivo, para que alguien lo revise después
def sendToDlq(sqsClient, message, problem):
    sqsClient.send_message(
        QueueUrl=dlqUrl,
        MessageBody=message["Body"],
        MessageAttributes={"errorReason": {"DataType": "String", "StringValue": problem}}
    )


# Solo se borra el mensaje de la cola DESPUÉS de guardarlo (o de mandarlo a la DLQ si es inválido).
# Si algo falla (por ejemplo, la base caída), no se borra: SQS lo vuelve a mostrar en 30 s
# y después de 3 intentos lo manda solo a la DLQ.
def processMessage(connection, sqsClient, message):
    attempt = message.get("Attributes", {}).get("ApproximateReceiveCount", "1")
    event, problem = checkEvent(message["Body"])
    try:
        if problem != "":
            sendToDlq(sqsClient, message, problem)
            logJson("WARNING", "message_to_dlq", {"result": "INVÁLIDO", "reason": problem, "retry": False})
        else:
            result = applyEvent(connection, event)
            logJson("INFO", "inventory_synced", {
                "result": result,
                "event_id": event["eventId"],
                "event_type": event.get("eventType", "INVENTORY_UPDATED"),
                "warehouse_id": event["warehouseId"],
                "product_id": event["productId"],
                "quantity": event["quantity"],
                "correlation_id": event.get("correlationId")
            })
        sqsClient.delete_message(QueueUrl=queueUrl, ReceiptHandle=message["ReceiptHandle"])
    except Exception as error:
        if connection.closed == 0:
            connection.rollback()
        logJson("ERROR", "processing_failed", {"attempt": int(attempt), "max_attempts": 3, "error": str(error)})


def main():
    connection = connectDatabase()
    with connection.cursor() as cursor:
        cursor.execute(createHistorySql)
    connection.commit()
    sqsClient = boto3.client("sqs", region_name=awsRegion)
    logJson("INFO", "worker_started", {"queue": queueUrl})
    while True:
        if connection.closed != 0:
            connection = connectDatabase()
        try:
            response = sqsClient.receive_message(QueueUrl=queueUrl, MaxNumberOfMessages=10, WaitTimeSeconds=20,
                                                  AttributeNames=["ApproximateReceiveCount"])
            messages = response.get("Messages", [])
            for message in messages:
                processMessage(connection, sqsClient, message)
        except Exception as error:
            logJson("ERROR", "queue_unavailable", {"message": "No se pudo leer la cola, se reintenta en 5 segundos", "error": str(error)})
            time.sleep(5)


if __name__ == "__main__":
    main()
