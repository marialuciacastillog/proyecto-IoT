# Etapa B: publicador del catálogo. Cada 2 segundos manda a SNS (catalog-events) los avisos guardados
# en catalog_outbox y los marca como publicados. Si SNS falla, el aviso queda y se reintenta.
import json
import os
import time
from datetime import datetime, timezone
import boto3
import psycopg2

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": "catalog-publisher", "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False), flush=True)


def findTopicArn(snsClient):
    topicArn = ""
    while topicArn == "":
        try:
            for topic in snsClient.list_topics()["Topics"]:
                if topic["TopicArn"].endswith(":catalog-events"):
                    topicArn = topic["TopicArn"]
        except Exception as error:
            logJson("ERROR", "topic_lookup_failed", {"error": str(error)})
        if topicArn == "":
            time.sleep(5)
    return topicArn


def publishPending(connection, snsClient, topicArn):
    published = 0
    with connection.cursor() as cursor:
        cursor.execute("""SELECT event_id, event_type, payload FROM catalog_outbox WHERE published_at IS NULL
                          ORDER BY created_at LIMIT 10 FOR UPDATE SKIP LOCKED""")
        for eventId, eventType, payload in cursor.fetchall():
            snsClient.publish(TopicArn=topicArn, Message=json.dumps(payload),
                              MessageAttributes={"eventType": {"DataType": "String", "StringValue": eventType}})
            cursor.execute("UPDATE catalog_outbox SET published_at = clock_timestamp() WHERE event_id = %s", (eventId,))
            logJson("INFO", "event_published", {"event_id": eventId, "event_type": eventType,
                                                "warehouse_id": payload.get("warehouseId")})
            published = published + 1
    connection.commit()
    return published


def main():
    snsClient = boto3.client("sns", region_name=awsRegion)
    topicArn = findTopicArn(snsClient)
    connection = None
    logJson("INFO", "publisher_started", {"topic": topicArn})
    while True:
        try:
            if connection is None or connection.closed != 0:
                connection = psycopg2.connect(databaseUrl)
            publishPending(connection, snsClient, topicArn)
        except Exception as error:
            logJson("ERROR", "publish_failed", {"error": str(error)})
            if connection is not None and connection.closed == 0:
                connection.rollback()
        time.sleep(2)


if __name__ == "__main__":
    main()
