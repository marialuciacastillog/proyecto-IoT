import json
import time
import datetime
import boto3
from fastapi import HTTPException
from app.config import settings
from app.database import SessionLocal, engine, Base
from app.modules.products.models import ProductEntity
from app.modules.inventory.models import InventoryEntity, InventoryMovementEntity
from app.modules.sync.models import PendingEventEntity
from app.modules.inventory.schemas import MovementCreate
from app.modules.inventory.inventory_service import InventoryService
from app.modules.transfers.models import TransferCommandEntity

# Fase 6 - Worker de transferencias de esta bodega.
# Recibe órdenes de la central por su cola (transfer-commands-wh-00X),
# las aplica en su propia base y responde por la cola transfer-events.

# Qué movimiento de inventario hace cada orden
movementTypes = {
    "RESERVE": "RESERVATION",           # origen: aparta las unidades (ya no se pueden vender)
    "RECEIVE": "TRANSFER_IN",           # destino: suma las unidades
    "COMMIT": "RESERVATION_CONFIRM",    # origen: las apartadas se van definitivamente
    "RELEASE": "RELEASE"                # origen: compensación, devuelve las apartadas
}


def findQueueUrl(sqsClient, queueName):
    queueUrl = None
    while queueUrl is None:
        try:
            queueUrl = sqsClient.get_queue_url(QueueName=queueName)["QueueUrl"]
        except Exception as error:
            print("Esperando la cola", queueName, ":", error)
            time.sleep(5)
    return queueUrl


# RESERVE y RECEIVE traen fecha límite: si llegan tarde (bodega caída mucho tiempo), no se aplican
def isExpired(command):
    expired = False
    expiresAt = command.get("expiresAt")
    if expiresAt and command["command"] in ("RESERVE", "RECEIVE"):
        now = datetime.datetime.now(datetime.timezone.utc)
        expired = now > datetime.datetime.fromisoformat(expiresAt)
    return expired


def saveResult(db, key, command, ok, reason):
    db.add(TransferCommandEntity(id=key, transfer_id=command["transferId"],
                                 command=command["command"], ok=ok, reason=reason))
    db.commit()


def applyCommand(command):
    db = SessionLocal()
    key = command["transferId"] + ":" + command["command"]
    result = {"ok": False, "reason": "", "repeated": False}
    try:
        saved = db.get(TransferCommandEntity, key)
        if saved is not None:
            # Orden repetida: no se vuelve a aplicar, se responde lo mismo de la primera vez
            result = {"ok": saved.ok, "reason": saved.reason or "", "repeated": True}
        elif isExpired(command):
            result["reason"] = "Orden vencida: llegó después del tiempo límite"
            saveResult(db, key, command, False, result["reason"])
        else:
            product = db.query(ProductEntity).filter(ProductEntity.sku == command["productId"]).first()
            if product is None:
                result["reason"] = "El producto " + command["productId"] + " no existe en " + settings.WAREHOUSE_ID
                saveResult(db, key, command, False, result["reason"])
            else:
                # La orden procesada y el movimiento se guardan en la MISMA transacción
                db.add(TransferCommandEntity(id=key, transfer_id=command["transferId"],
                                             command=command["command"], ok=True, reason=""))
                movement = MovementCreate(product_id=product.id, type=movementTypes[command["command"]],
                                          quantity=command["quantity"], reference=command["transferId"])
                try:
                    InventoryService(db).process_movement(movement)
                    result["ok"] = True
                except HTTPException as error:
                    if error.status_code >= 500:
                        raise
                    # Error de negocio (por ejemplo, stock insuficiente): se guarda como fallo
                    result["reason"] = str(error.detail)
                    saveResult(db, key, command, False, result["reason"])
    finally:
        db.close()
    return result


# Solo se borra la orden de la cola DESPUÉS de aplicarla y responder
def processMessage(sqsClient, eventsUrl, commandsUrl, message):
    try:
        command = json.loads(message["Body"])
        result = applyCommand(command)
        label = "REPETIDA" if result["repeated"] else ("OK" if result["ok"] else "FALLÓ")
        print(label, "|", command["command"], command["transferId"], command["productId"],
              command["quantity"], result["reason"])
        event = {
            "transferId": command["transferId"],
            "command": command["command"],
            "warehouseId": settings.WAREHOUSE_ID,
            "ok": result["ok"],
            "reason": result["reason"]
        }
        sqsClient.send_message(QueueUrl=eventsUrl, MessageBody=json.dumps(event))
        sqsClient.delete_message(QueueUrl=commandsUrl, ReceiptHandle=message["ReceiptHandle"])
    except Exception as error:
        print("Error con una orden, SQS la reintentará:", error)


def createTables():
    ready = False
    while not ready:
        try:
            Base.metadata.create_all(bind=engine)
            ready = True
        except Exception as error:
            print("Esperando la base de datos:", error)
            time.sleep(3)


def main():
    createTables()
    sqsClient = boto3.client("sqs", region_name=settings.AWS_REGION)
    commandsUrl = findQueueUrl(sqsClient, "transfer-commands-" + settings.WAREHOUSE_ID)
    eventsUrl = findQueueUrl(sqsClient, "transfer-events")
    print("Worker de transferencias iniciado para", settings.WAREHOUSE_ID)
    while True:
        try:
            response = sqsClient.receive_message(QueueUrl=commandsUrl, MaxNumberOfMessages=10, WaitTimeSeconds=20)
            for message in response.get("Messages", []):
                processMessage(sqsClient, eventsUrl, commandsUrl, message)
        except Exception as error:
            print("No se pudo leer la cola, se reintenta en 5 segundos:", error)
            time.sleep(5)


if __name__ == "__main__":
    main()
