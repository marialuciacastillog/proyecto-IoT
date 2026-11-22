import json
import time
import boto3
from app.config import settings
from app.database import SessionLocal
from app.modules.sync.models import PendingEventEntity
from app.modules.shared.utils import schemas_utils


def buildMessage(event):
    message = {
        "eventId": event.event_id,
        "warehouseId": event.warehouse_id,
        "productId": event.product_id,
        "quantity": event.quantity,
        "createdAt": event.created_at.isoformat()
    }
    return json.dumps(message)


# Envía hasta 10 pendientes. Solo marca SENT si SQS respondió bien.
# Si falla (sin internet, credenciales vencidas...), el evento sigue PENDING y se reintenta luego.
def sendPendingEvents(sqsClient):
    db = SessionLocal()
    sentCount = 0
    try:
        pendingEvents = (
            db.query(PendingEventEntity)
            .filter(PendingEventEntity.status == "PENDING")
            .order_by(PendingEventEntity.id)
            .limit(10)
            .all()
        )
        for event in pendingEvents:
            sqsClient.send_message(QueueUrl=settings.SYNC_QUEUE_URL, MessageBody=buildMessage(event))
            event.status = "SENT"
            event.sent_at = schemas_utils.current_utc_time()
            db.commit()
            sentCount = sentCount + 1
            print("Enviado:", event.event_id, "producto", event.product_id, "cantidad", event.quantity)
    except Exception as error:
        db.rollback()
        print("No se pudo enviar, se reintenta en 5 segundos:", error)
    finally:
        db.close()
    return sentCount


def main():
    sqsClient = boto3.client("sqs", region_name=settings.AWS_REGION)
    print("Publicador iniciado para", settings.WAREHOUSE_ID)
    while True:
        sentCount = sendPendingEvents(sqsClient)
        if sentCount == 0:
            time.sleep(5)


if __name__ == "__main__":
    main()
