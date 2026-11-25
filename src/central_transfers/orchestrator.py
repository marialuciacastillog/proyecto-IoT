import json
import os
import time
import datetime
import boto3
from psycopg2.extras import RealDictCursor
from db import connectDatabase, changeStatus

# Fase 6 - Orquestador de la saga.
# Manda órdenes a las bodegas por SQS y avanza el estado de cada transferencia según sus respuestas.

awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
retrySeconds = int(os.environ.get("RETRY_SECONDS", "20"))       # cada cuánto se reenvía una orden sin respuesta
commandTtlSeconds = int(os.environ.get("COMMAND_TTL_SECONDS", "60"))  # vencimiento de RESERVE y RECEIVE
stepTimeoutSeconds = int(os.environ.get("STEP_TIMEOUT_SECONDS", "90"))  # cuánto se espera antes de rendirse

# (orden, salió bien, estado actual) -> estado siguiente
transitions = {
    ("RESERVE", True, "RESERVING_SOURCE"): "SOURCE_RESERVED",
    ("RESERVE", False, "RESERVING_SOURCE"): "FAILED",
    ("RESERVE", True, "FAILED"): "COMPENSATING",          # la reserva llegó tarde, después del timeout
    ("RECEIVE", True, "IN_TRANSIT"): "DESTINATION_RECEIVED",
    ("RECEIVE", False, "IN_TRANSIT"): "COMPENSATING",
    ("COMMIT", True, "DESTINATION_RECEIVED"): "COMPLETED",
    ("RELEASE", True, "COMPENSATING"): "RESERVATION_RELEASED"
}

queueUrls = {}


def getQueueUrl(sqsClient, queueName):
    if queueName not in queueUrls:
        queueUrls[queueName] = sqsClient.get_queue_url(QueueName=queueName)["QueueUrl"]
    return queueUrls[queueName]


def nowUtc():
    now = datetime.datetime.now(datetime.timezone.utc)
    return now


def sendCommand(sqsClient, warehouseId, transfer, command, expiresAt):
    message = {
        "transferId": transfer["transfer_id"],
        "command": command,
        "productId": transfer["product_id"],
        "quantity": transfer["quantity"],
        "expiresAt": expiresAt.isoformat() if expiresAt else None
    }
    queueUrl = getQueueUrl(sqsClient, "transfer-commands-" + warehouseId)
    sqsClient.send_message(QueueUrl=queueUrl, MessageBody=json.dumps(message))
    print("Orden", command, "enviada a", warehouseId, "para", transfer["transfer_id"])


# Una respuesta de una bodega: solo cambia el estado si es la respuesta que se estaba esperando
def handleEvent(connection, event):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT * FROM transfers WHERE transfer_id = %s FOR UPDATE", (event["transferId"],))
        transfer = cursor.fetchone()
        if transfer is None:
            print("Respuesta de una transferencia desconocida, se ignora:", event)
        else:
            key = (event["command"], event["ok"], transfer["status"])
            if key in transitions:
                # Si la respuesta no trae motivo, se conserva el de antes (por qué se compensó)
                reason = event["reason"] or transfer["reason"] or ""
                changeStatus(cursor, transfer["transfer_id"], transitions[key], reason)
            elif event["command"] == "RECEIVE" and event["ok"] and transfer["status"] in ("COMPENSATING", "RESERVATION_RELEASED"):
                print("ALERTA: el destino recibió después del timeout. Revisar a mano", transfer["transfer_id"])
            else:
                print("Respuesta repetida o tardía, se ignora:", event["transferId"], event["command"],
                      "estado actual", transfer["status"])
    connection.commit()


def isStale(transfer):
    stale = transfer["step_sent_at"] is None
    if not stale:
        stale = (nowUtc() - transfer["step_sent_at"]).total_seconds() > retrySeconds
    return stale


def isTimedOut(transfer):
    timedOut = (nowUtc() - transfer["step_started_at"]).total_seconds() > stepTimeoutSeconds
    return timedOut


def markSent(cursor, transfer, expiresAt):
    cursor.execute("UPDATE transfers SET step_sent_at = clock_timestamp(), expires_at = %s WHERE transfer_id = %s",
                   (expiresAt, transfer["transfer_id"]))


# Manda la orden. Si no se puede (por ejemplo, la bodega está registrada pero todavía no tiene su cola),
# el cambio de estado igual se guarda y la orden queda sin enviar: se reintenta en la próxima vuelta y,
# si pasa el tiempo límite, la transferencia falla o se compensa. Así unas unidades apartadas
# nunca quedan atrapadas porque el destino no existe.
def trySend(cursor, sqsClient, warehouseId, transfer, command, expiresAt):
    try:
        sendCommand(sqsClient, warehouseId, transfer, command, expiresAt)
        markSent(cursor, transfer, expiresAt)
    except Exception as error:
        cursor.execute("UPDATE transfers SET expires_at = %s WHERE transfer_id = %s", (expiresAt, transfer["transfer_id"]))
        print("No se pudo mandar", command, "a", warehouseId, "para", transfer["transfer_id"], ", se reintenta:", error)


# Mira cada transferencia sin terminar y manda la orden que le toca
def driveTransfer(cursor, sqsClient, transfer):
    status = transfer["status"]
    source = transfer["source_warehouse"]
    destination = transfer["destination_warehouse"]
    if status == "REQUESTED":
        expiresAt = nowUtc() + datetime.timedelta(seconds=commandTtlSeconds)
        changeStatus(cursor, transfer["transfer_id"], "RESERVING_SOURCE")
        trySend(cursor, sqsClient, source, transfer, "RESERVE", expiresAt)
    elif status == "RESERVING_SOURCE":
        if isTimedOut(transfer):
            changeStatus(cursor, transfer["transfer_id"], "FAILED", "Timeout: la bodega origen no respondió")
        elif isStale(transfer):
            trySend(cursor, sqsClient, source, transfer, "RESERVE", transfer["expires_at"])
    elif status == "SOURCE_RESERVED":
        expiresAt = nowUtc() + datetime.timedelta(seconds=commandTtlSeconds)
        changeStatus(cursor, transfer["transfer_id"], "IN_TRANSIT")
        trySend(cursor, sqsClient, destination, transfer, "RECEIVE", expiresAt)
    elif status == "IN_TRANSIT":
        if isTimedOut(transfer):
            changeStatus(cursor, transfer["transfer_id"], "COMPENSATING", "Timeout: la bodega destino no respondió")
        elif isStale(transfer):
            trySend(cursor, sqsClient, destination, transfer, "RECEIVE", transfer["expires_at"])
    elif status == "DESTINATION_RECEIVED" and isStale(transfer):
        trySend(cursor, sqsClient, source, transfer, "COMMIT", None)
    elif status == "COMPENSATING" and isStale(transfer):
        trySend(cursor, sqsClient, source, transfer, "RELEASE", None)


def driveTransfers(connection, sqsClient):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """SELECT * FROM transfers WHERE status IN ('REQUESTED', 'RESERVING_SOURCE', 'SOURCE_RESERVED',
               'IN_TRANSIT', 'DESTINATION_RECEIVED', 'COMPENSATING') ORDER BY created_at""")
        pending = cursor.fetchall()
    # Se cierra la consulta para no dejar una transacción abierta
    connection.commit()
    for transfer in pending:
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM transfers WHERE transfer_id = %s FOR UPDATE",
                               (transfer["transfer_id"],))
                current = cursor.fetchone()
                driveTransfer(cursor, sqsClient, current)
            connection.commit()
        except Exception as error:
            connection.rollback()
            print("No se pudo avanzar", transfer["transfer_id"], ", se reintenta:", error)


def readEvents(connection, sqsClient):
    eventsUrl = getQueueUrl(sqsClient, "transfer-events")
    response = sqsClient.receive_message(QueueUrl=eventsUrl, MaxNumberOfMessages=10, WaitTimeSeconds=5)
    for message in response.get("Messages", []):
        try:
            handleEvent(connection, json.loads(message["Body"]))
            sqsClient.delete_message(QueueUrl=eventsUrl, ReceiptHandle=message["ReceiptHandle"])
        except Exception as error:
            connection.rollback()
            print("Error con una respuesta, SQS la reintentará:", error)


def main():
    connection = connectDatabase()
    sqsClient = boto3.client("sqs", region_name=awsRegion)
    print("Orquestador de transferencias iniciado")
    while True:
        if connection.closed != 0:
            connection = connectDatabase()
        try:
            readEvents(connection, sqsClient)
            driveTransfers(connection, sqsClient)
        except Exception as error:
            print("Error en el ciclo del orquestador, se reintenta en 5 segundos:", error)
            time.sleep(5)


if __name__ == "__main__":
    main()
