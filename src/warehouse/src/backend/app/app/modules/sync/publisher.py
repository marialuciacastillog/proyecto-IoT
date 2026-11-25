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
        "eventType": event.event_type,
        "correlationId": event.correlation_id,
        "performedBy": event.performed_by,
        "warehouseId": event.warehouse_id,
        "productId": event.product_id,
        "quantity": event.quantity,
        "createdAt": event.created_at.isoformat()
    }
    return json.dumps(message)


# Fase 3: si hay tema SNS, se publica UNA vez en el tema y SNS lo copia a todas las colas
# suscritas (sync-queue y audit-queue). Si no hay tema, se manda directo a sync-queue como antes.
def sendMessage(awsClients, body):
    if settings.SYNC_TOPIC_ARN != "":
        awsClients["sns"].publish(TopicArn=settings.SYNC_TOPIC_ARN, Message=body)
    else:
        awsClients["sqs"].send_message(QueueUrl=settings.SYNC_QUEUE_URL, MessageBody=body)


# Envía hasta 10 pendientes. Solo marca SENT si AWS respondió bien.
# Si falla (sin internet, credenciales vencidas...), el evento sigue PENDING y se reintenta luego.
def sendPendingEvents(awsClients):
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
            sendMessage(awsClients, buildMessage(event))
            event.status = "SENT"
            event.sent_at = schemas_utils.current_utc_time()
            db.commit()
            sentCount = sentCount + 1
            print("Enviado:", event.event_type, event.event_id, "| correlationId", event.correlation_id, "| producto", event.product_id, "cantidad", event.quantity)
    except Exception as error:
        db.rollback()
        print("No se pudo enviar, se reintenta en 5 segundos:", error)
    finally:
        db.close()
    return sentCount


def main():
    awsClients = {
        "sqs": boto3.client("sqs", region_name=settings.AWS_REGION),
        "sns": boto3.client("sns", region_name=settings.AWS_REGION)
    }
    print("Publicador iniciado para", settings.WAREHOUSE_ID, "| tema SNS:", settings.SYNC_TOPIC_ARN or "(ninguno, va directo a SQS)")
    while True:
        sentCount = sendPendingEvents(awsClients)
        if sentCount == 0:
            time.sleep(5)


if __name__ == "__main__":
    main()
