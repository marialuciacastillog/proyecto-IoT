# Fase 3: worker de auditoría. Lee su PROPIA cola (audit-queue), que recibe una copia de cada
# evento de inventario gracias al tema SNS. Guarda todo en la tabla audit_log.
# No depende del worker de sincronización: si uno se cae, el otro sigue.
import json
import os
import time
import boto3
import psycopg2

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
queueUrl = os.environ.get("AUDIT_QUEUE_URL", "")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

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
    )
"""

# Si el mismo evento llega dos veces, no se duplica (event_id es la llave)
saveAuditSql = """
    INSERT INTO audit_log (event_id, event_type, correlation_id, warehouse_id, product_id, quantity, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (event_id) DO NOTHING
"""


def connectDatabase():
    connection = None
    while connection is None:
        try:
            connection = psycopg2.connect(databaseUrl)
        except Exception as error:
            print("Esperando la base de datos central:", error)
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
            event["createdAt"]
        ))
        saved = cursor.rowcount == 1
    connection.commit()
    return saved


# Igual que en sincronización: el mensaje se borra solo DESPUÉS de guardarlo
def processMessage(connection, sqsClient, message):
    try:
        event = json.loads(message["Body"])
        saved = saveEvent(connection, event)
        result = "AUDITADO" if saved else "REPETIDO"
        print(result, "|", event.get("eventType"), "|", event["warehouseId"], event["productId"], "cantidad", event["quantity"], "| correlationId", event.get("correlationId"))
        sqsClient.delete_message(QueueUrl=queueUrl, ReceiptHandle=message["ReceiptHandle"])
    except Exception as error:
        if connection.closed == 0:
            connection.rollback()
        print("Error con un mensaje, SQS lo reintentará:", error)


def main():
    connection = connectDatabase()
    with connection.cursor() as cursor:
        cursor.execute(createAuditSql)
    connection.commit()
    sqsClient = boto3.client("sqs", region_name=awsRegion)
    print("Worker de auditoría iniciado")
    while True:
        if connection.closed != 0:
            connection = connectDatabase()
        try:
            response = sqsClient.receive_message(QueueUrl=queueUrl, MaxNumberOfMessages=10, WaitTimeSeconds=20)
            for message in response.get("Messages", []):
                processMessage(connection, sqsClient, message)
        except Exception as error:
            print("No se pudo leer la cola, se reintenta en 5 segundos:", error)
            time.sleep(5)


if __name__ == "__main__":
    main()
