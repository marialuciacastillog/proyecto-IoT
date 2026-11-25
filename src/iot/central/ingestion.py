# Fase 12: servicio de ingesta. Recibe lotes de lecturas de los gateways, revisa que estén bien
# y las publica en el tema SNS iot-events, que las copia a las colas de storage, alerts y analytics.
# Si SNS falla responde 503: el gateway no borra su buffer y lo vuelve a intentar (no se pierde nada).
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import boto3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
snsClient = boto3.client("sns", region_name=awsRegion)
requiredFields = ["event_id", "sequence_number", "warehouse_id", "sensor_id", "value", "timestamp"]
app = FastAPI(title="IoT Ingestion")
executor = ThreadPoolExecutor(max_workers=10)
state = {"topicArn": ""}


class Batch(BaseModel):
    events: list


def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": "iot-ingestion", "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line), flush=True)


def findTopicArn():
    topicArn = state["topicArn"]
    while topicArn == "":
        try:
            for topic in snsClient.list_topics()["Topics"]:
                if topic["TopicArn"].endswith(":iot-events"):
                    topicArn = topic["TopicArn"]
        except Exception as error:
            logJson("ERROR", "topic_lookup_failed", {"error": str(error)})
        if topicArn == "":
            time.sleep(5)
    state["topicArn"] = topicArn
    return topicArn


def isValid(event):
    valid = isinstance(event, dict)
    if valid:
        for field in requiredFields:
            if field not in event:
                valid = False
    if valid:
        valid = isinstance(event["sequence_number"], int) and isinstance(event["value"], (int, float))
    return valid


# Publica un grupo de hasta 10 lecturas. Devuelve cuántas fallaron.
def publishChunk(topicArn, chunk):
    entries = [{"Id": uuid.uuid4().hex, "Message": json.dumps(item)} for item in chunk]
    response = snsClient.publish_batch(TopicArn=topicArn, PublishBatchRequestEntries=entries)
    return len(response.get("Failed", []))


@app.get("/health")
def health():
    return {"status": "ok", "topic": state["topicArn"]}


@app.post("/ingest")
def ingest(batch: Batch):
    topicArn = findTopicArn()
    ingestedAt = datetime.now(timezone.utc).isoformat()
    validEvents = []
    rejected = 0
    for event in batch.events:
        if isValid(event):
            event["ingested_at"] = ingestedAt
            validEvents.append(event)
        else:
            rejected = rejected + 1
    try:
        # SNS acepta hasta 10 mensajes por llamada; los grupos de 10 se mandan en paralelo
        chunks = [validEvents[start:start + 10] for start in range(0, len(validEvents), 10)]
        failed = sum(executor.map(lambda chunk: publishChunk(topicArn, chunk), chunks))
        if failed > 0:
            raise RuntimeError("SNS no aceptó " + str(failed) + " mensajes")
    except Exception as error:
        logJson("ERROR", "publish_failed", {"error": str(error), "events": len(validEvents)})
        raise HTTPException(status_code=503, detail="No se pudo publicar, reintente")
    logJson("INFO", "batch_ingested", {"accepted": len(validEvents), "rejected": rejected})
    return {"accepted": len(validEvents), "rejected": rejected}
