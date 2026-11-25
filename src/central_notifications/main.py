# Servicio de notificaciones de la central (puerto 8006).
# - Cada usuario ve las notificaciones de su rol (y de su bodega, si es un rol de bodega) (RF-16).
# - El Administrador Global atiende las DLQ: ve los mensajes que fallaron, los reprocesa o los descarta.
#   Reprocesar es seguro porque los workers son idempotentes: si el mensaje ya se había aplicado, se ignora.
#   Descartar pide una justificación y queda registrado quién lo hizo y por qué (dlq_actions).
import json
import os
import time
from datetime import datetime, timezone
import boto3
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from auth import currentUser, requireRoles
from roles import ADMIN_GLOBAL, WAREHOUSE_ROLES
from notify import createNotificationsSql

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
sqsClient = boto3.client("sqs", region_name=awsRegion)
app = FastAPI(title="Notificaciones y DLQ")

createDlqSql = """
    CREATE TABLE IF NOT EXISTS dlq_actions (
        id            SERIAL PRIMARY KEY,
        queue_name    VARCHAR(100) NOT NULL,
        message_id    VARCHAR(100) NOT NULL,
        action        VARCHAR(20) NOT NULL,
        justification TEXT,
        body          TEXT,
        performed_by  VARCHAR(50) NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
"""


class DlqAction(BaseModel):
    justification: str = Field("", max_length=500)


def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": "notifications", "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False, default=str), flush=True)


def connectDatabase():
    connection = None
    while connection is None:
        try:
            connection = psycopg2.connect(databaseUrl)
        except Exception as error:
            logJson("ERROR", "database_unavailable", {"error": str(error)})
            time.sleep(3)
    return connection


@app.on_event("startup")
def startup():
    connection = connectDatabase()
    with connection.cursor() as cursor:
        cursor.execute(createNotificationsSql)
        cursor.execute(createDlqSql)
    connection.commit()
    connection.close()


@app.get("/health")
def health():
    connection = connectDatabase()
    connection.close()
    return {"status": "ok"}


# ---------------- Notificaciones de cada usuario ----------------

# Las de su rol; los roles de bodega solo ven las de su bodega o las que son para todo el rol
visibleSql = """
    FROM notifications n
    LEFT JOIN notification_reads r ON r.notification_id = n.id AND r.username = %(username)s
    WHERE n.recipient_role = %(role)s
      AND (n.recipient_warehouse IS NULL OR n.recipient_warehouse = %(warehouse)s)
"""


def userParams(user):
    warehouse = user["warehouse_id"] if user["role"] in WAREHOUSE_ROLES else None
    return {"username": user["username"], "role": user["role"], "warehouse": warehouse}


@app.get("/notifications")
def listNotifications(unread_only: bool = False, limit: int = 50, user=Depends(currentUser)):
    params = userParams(user)
    params["limit"] = max(1, min(limit, 200))
    onlyUnread = " AND r.notification_id IS NULL" if unread_only else ""
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT n.*, r.read_at IS NOT NULL AS read " + visibleSql + onlyUnread +
                           " ORDER BY n.created_at DESC LIMIT %(limit)s", params)
            notifications = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) AS unread " + visibleSql + " AND r.notification_id IS NULL", params)
            unread = cursor.fetchone()["unread"]
    finally:
        connection.close()
    return {"unread": unread, "notifications": notifications}


@app.get("/notifications/unread-count")
def unreadCount(user=Depends(currentUser)):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT COUNT(*) AS unread " + visibleSql + " AND r.notification_id IS NULL", userParams(user))
            unread = cursor.fetchone()["unread"]
    finally:
        connection.close()
    return {"unread": unread}


@app.post("/notifications/{notification_id}/read")
def markRead(notification_id: int, user=Depends(currentUser)):
    params = userParams(user)
    params["id"] = notification_id
    connection = connectDatabase()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT n.id " + visibleSql + " AND n.id = %(id)s", params)
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Notificación no encontrada")
            cursor.execute("INSERT INTO notification_reads (notification_id, username) VALUES (%s, %s) "
                           "ON CONFLICT DO NOTHING", (notification_id, user["username"]))
        connection.commit()
    finally:
        connection.close()
    return {"id": notification_id, "read": True}


@app.post("/notifications/read-all")
def markAllRead(user=Depends(currentUser)):
    params = userParams(user)
    connection = connectDatabase()
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO notification_reads (notification_id, username) SELECT n.id, %(username)s " +
                           visibleSql + " AND r.notification_id IS NULL", params)
            marked = cursor.rowcount
        connection.commit()
    finally:
        connection.close()
    return {"marked": marked}


# ---------------- DLQ: solo el Administrador Global ----------------

def dlqUrls():
    urls = {}
    for url in sqsClient.list_queues(MaxResults=1000).get("QueueUrls", []):
        name = url.rsplit("/", 1)[-1]
        if name.endswith("-dlq"):
            urls[name] = url
    return urls


def getDlqUrl(queueName):
    urls = dlqUrls()
    if queueName not in urls:
        raise HTTPException(status_code=404, detail="No existe la DLQ " + queueName)
    return urls[queueName]


@app.get("/dlq")
def listDlqs(admin=Depends(requireRoles(ADMIN_GLOBAL))):
    queues = []
    for name, url in sorted(dlqUrls().items()):
        attributes = sqsClient.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])["Attributes"]
        queues.append({"queue": name, "source_queue": name[:-4], "messages": int(attributes["ApproximateNumberOfMessages"])})
    return queues


# Recibe hasta 50 mensajes de la DLQ (los esconde un momento) y devuelve la lista con sus recibos
def receiveAll(url, visibilitySeconds):
    messages = {}
    attempts = 0
    while attempts < 5:
        response = sqsClient.receive_message(QueueUrl=url, MaxNumberOfMessages=10, VisibilityTimeout=visibilitySeconds,
                                             MessageAttributeNames=["All"], AttributeNames=["All"], WaitTimeSeconds=1)
        batch = response.get("Messages", [])
        for message in batch:
            messages[message["MessageId"]] = message
        attempts = attempts + 1
        if len(batch) == 0:
            attempts = 5
    return list(messages.values())


# Vuelve a dejar visibles los mensajes que no se van a tocar
def releaseAll(url, messages):
    for message in messages:
        sqsClient.change_message_visibility(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"], VisibilityTimeout=0)


def describeMessage(message):
    reason = message.get("MessageAttributes", {}).get("errorReason", {}).get("StringValue")
    sentAt = int(message.get("Attributes", {}).get("SentTimestamp", "0")) / 1000
    return {
        "message_id": message["MessageId"],
        "body": message["Body"],
        "reason": reason or "Falló 3 veces en su cola (lo pasó SQS solo)",
        "sent_at": datetime.fromtimestamp(sentAt, timezone.utc).isoformat() if sentAt else None
    }


@app.get("/dlq/{queue_name}/messages")
def peekDlq(queue_name: str, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    url = getDlqUrl(queue_name)
    messages = receiveAll(url, 20)
    releaseAll(url, messages)
    return {"queue": queue_name, "source_queue": queue_name[:-4], "messages": [describeMessage(m) for m in messages]}


def actOnMessage(queueName, messageId, action, justification, admin):
    url = getDlqUrl(queueName)
    messages = receiveAll(url, 30)
    target = None
    others = []
    for message in messages:
        if message["MessageId"] == messageId:
            target = message
        else:
            others.append(message)
    releaseAll(url, others)
    if target is None:
        raise HTTPException(status_code=404, detail="El mensaje ya no está en la DLQ (quizá otro administrador lo atendió)")
    if action == "REDRIVE":
        sourceUrl = sqsClient.get_queue_url(QueueName=queueName[:-4])["QueueUrl"]
        sqsClient.send_message(QueueUrl=sourceUrl, MessageBody=target["Body"])
    sqsClient.delete_message(QueueUrl=url, ReceiptHandle=target["ReceiptHandle"])
    connection = connectDatabase()
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO dlq_actions (queue_name, message_id, action, justification, body, performed_by) "
                           "VALUES (%s, %s, %s, %s, %s, %s)",
                           (queueName, messageId, action, justification, target["Body"], admin["username"]))
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "dlq_" + action.lower(), {"queue": queueName, "message_id": messageId, "by": admin["username"]})
    return {"queue": queueName, "message_id": messageId, "action": action}


@app.post("/dlq/{queue_name}/messages/{message_id}/redrive")
def redriveMessage(queue_name: str, message_id: str, request: DlqAction, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    result = actOnMessage(queue_name, message_id, "REDRIVE", request.justification, admin)
    result["message"] = "El mensaje volvió a " + queue_name[:-4] + ". Si ya se había aplicado, el worker lo ignora"
    return result


@app.post("/dlq/{queue_name}/messages/{message_id}/discard")
def discardMessage(queue_name: str, message_id: str, request: DlqAction, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    if len(request.justification.strip()) < 5:
        raise HTTPException(status_code=400, detail="Para descartar un mensaje escribe por qué (mínimo 5 caracteres)")
    result = actOnMessage(queue_name, message_id, "DISCARD", request.justification.strip(), admin)
    result["message"] = "Mensaje descartado y registrado"
    return result


@app.get("/dlq/actions")
def listDlqActions(admin=Depends(requireRoles(ADMIN_GLOBAL))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM dlq_actions ORDER BY created_at DESC LIMIT 100")
            actions = cursor.fetchall()
    finally:
        connection.close()
    return actions
