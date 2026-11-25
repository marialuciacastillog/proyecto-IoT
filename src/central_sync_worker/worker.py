import json
import os
import time
import boto3
import psycopg2

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
queueUrl = os.environ.get("SYNC_QUEUE_URL", "")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

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
            print("Esperando la base de datos central:", error)
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


# Solo se borra el mensaje de la cola DESPUÉS de guardarlo.
# Si algo falla, no se borra: SQS lo vuelve a mostrar en 30 s, y tras 3 fallos lo manda a la DLQ.
def processMessage(connection, sqsClient, message):
    try:
        event = json.loads(message["Body"])
        result = applyEvent(connection, event)
        print(result, "|", event["warehouseId"], "producto", event["productId"], "cantidad", event["quantity"], "| evento", event["eventId"])
        sqsClient.delete_message(QueueUrl=queueUrl, ReceiptHandle=message["ReceiptHandle"])
    except Exception as error:
        if connection.closed == 0:
            connection.rollback()
        print("Error con un mensaje, SQS lo reintentará:", error)


def main():
    connection = connectDatabase()
    with connection.cursor() as cursor:
        cursor.execute(createHistorySql)
    connection.commit()
    sqsClient = boto3.client("sqs", region_name=awsRegion)
    print("Worker de sincronización iniciado")
    while True:
        if connection.closed != 0:
            connection = connectDatabase()
        try:
            response = sqsClient.receive_message(QueueUrl=queueUrl, MaxNumberOfMessages=10, WaitTimeSeconds=20)
            messages = response.get("Messages", [])
            for message in messages:
                processMessage(connection, sqsClient, message)
        except Exception as error:
            print("No se pudo leer la cola, se reintenta en 5 segundos:", error)
            time.sleep(5)


if __name__ == "__main__":
    main()
