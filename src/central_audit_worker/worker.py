# Fase 3: worker de auditoría. Lee su PROPIA cola (audit-queue), que recibe una copia de cada
# evento de inventario gracias al tema SNS. Guarda todo en la tabla audit_log.
# No depende del worker de sincronización: si uno se cae, el otro sigue.
import json
import os
import time
import boto3
import psycopg2
from datetime import datetime, timezone

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
queueUrl = os.environ.get("AUDIT_QUEUE_URL", "")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
# Fase 10: la DLQ se llama igual que la cola + "-dlq" (así se crea en Terraform)
dlqUrl = os.environ.get("DLQ_URL", queueUrl + "-dlq")
SERVICE = "audit-worker"
requiredFields = ["eventId", "warehouseId", "productId", "quantity", "createdAt"]

# Fase 11: logs estructurados (una línea JSON por evento)
def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": SERVICE, "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False), flush=True)


createAuditSql = """
    CREATE TABLE IF NOT EXISTS audit_log (
        event_id       VARCHAR(64) PRIMARY KEY,
        event_type     VARCHAR(40),
        correlation_id VARCHAR(64),
        warehouse_id   VARCHAR(20),
        product_id     VARCHAR(50),
        quantity       INTEGER,
        created_at     TIMESTAMP,
        received_at    TIMESTAMP DEFAULT clock_timestamp()
    );
    -- Usuario que hizo el movimiento (RF-28)
    ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS performed_by VARCHAR(80)
"""

# Si el mismo evento llega dos veces, no se duplica (event_id es la llave)
saveAuditSql = """
    INSERT INTO audit_log (event_id, event_type, correlation_id, warehouse_id, product_id, quantity, created_at, performed_by)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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


def saveEvent(connection, event):
    with connection.cursor() as cursor:
        cursor.execute(saveAuditSql, (
            event["eventId"],
            event.get("eventType", "INVENTORY_UPDATED"),
            event.get("correlationId"),
            event["warehouseId"],
            event["productId"],
            event["quantity"],
            event["createdAt"],
            event.get("performedBy", "sistema")
        ))
        saved = cursor.rowcount == 1
    connection.commit()
    return saved


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


# Igual que en sincronización: el mensaje se borra solo DESPUÉS de guardarlo
# (o de mandarlo a la DLQ si es inválido). Si falla otra cosa, SQS lo reintenta.
def processMessage(connection, sqsClient, message):
    attempt = message.get("Attributes", {}).get("ApproximateReceiveCount", "1")
    event, problem = checkEvent(message["Body"])
    try:
        if problem != "":
            sendToDlq(sqsClient, message, problem)
            logJson("WARNING", "message_to_dlq", {"result": "INVÁLIDO", "reason": problem, "retry": False})
        else:
            saved = saveEvent(connection, event)
            result = "AUDITADO" if saved else "REPETIDO"
            logJson("INFO", "audit_saved", {
                "result": result,
                "event_id": event["eventId"],
                "event_type": event.get("eventType"),
                "warehouse_id": event["warehouseId"],
                "product_id": event["productId"],
                "quantity": event["quantity"],
                "correlation_id": event.get("correlationId"),
                "performed_by": event.get("performedBy", "sistema")
            })
        sqsClient.delete_message(QueueUrl=queueUrl, ReceiptHandle=message["ReceiptHandle"])
    except Exception as error:
        if connection.closed == 0:
            connection.rollback()
        logJson("ERROR", "processing_failed", {"attempt": int(attempt), "max_attempts": 3, "error": str(error)})


def main():
    connection = connectDatabase()
    with connection.cursor() as cursor:
        cursor.execute(createAuditSql)
    connection.commit()
    sqsClient = boto3.client("sqs", region_name=awsRegion)
    logJson("INFO", "worker_started", {"queue": queueUrl})
    while True:
        if connection.closed != 0:
            connection = connectDatabase()
        try:
            response = sqsClient.receive_message(QueueUrl=queueUrl, MaxNumberOfMessages=10, WaitTimeSeconds=20,
                                                  AttributeNames=["ApproximateReceiveCount"])
            for message in response.get("Messages", []):
                processMessage(connection, sqsClient, message)
        except Exception as error:
            logJson("ERROR", "queue_unavailable", {"message": "No se pudo leer la cola, se reintenta en 5 segundos", "error": str(error)})
            time.sleep(5)


if __name__ == "__main__":
    main()
