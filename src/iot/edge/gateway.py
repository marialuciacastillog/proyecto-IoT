# Fase 12: gateway / edge de la bodega.
# 1. Escucha los sensores por MQTT.
# 2. Guarda cada lectura en un buffer local (SQLite en disco) ANTES de mandarla.
# 3. Manda el buffer a la nube en lotes por HTTP (servicio de ingesta de la central).
#    Solo borra del buffer lo que la nube confirmó. Si no hay red, las lecturas esperan en el buffer.
import json
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timezone
import boto3
import paho.mqtt.client as mqtt

brokerHost = os.environ.get("MQTT_HOST", "localhost")
ingestUrl = os.environ.get("INGEST_URL", "")
bufferPath = os.environ.get("BUFFER_PATH", "/data/buffer.db")
batchSize = int(os.environ.get("BATCH_SIZE", "200"))
gatewayName = os.environ.get("IOT_WAREHOUSES", "WH-001")
lock = threading.Lock()
stats = {"received": 0, "sent": 0, "failedSends": 0}

database = sqlite3.connect(bufferPath, check_same_thread=False)
database.execute("CREATE TABLE IF NOT EXISTS buffer (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)")
database.commit()


def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": "iot-gateway",
            "gateway": gatewayName, "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line), flush=True)


# La central no tiene una IP fija entre sesiones del lab, así que se busca por su nombre en AWS
def findIngestUrl():
    url = ingestUrl
    ec2Client = boto3.client("ec2", region_name="us-east-1")
    while url == "":
        try:
            response = ec2Client.describe_instances(Filters=[
                {"Name": "tag:Name", "Values": ["ec2-central"]},
                {"Name": "instance-state-name", "Values": ["running"]}
            ])
            for reservation in response["Reservations"]:
                for instance in reservation["Instances"]:
                    if "PublicIpAddress" in instance:
                        # Por HTTPS, por la puerta de entrada de la central (RNF-03)
                        url = "https://" + instance["PublicIpAddress"] + "/svc/iot/ingest"
        except Exception as error:
            logJson("ERROR", "central_lookup_failed", {"error": str(error)})
        if url == "":
            time.sleep(5)
    return url


def onMessage(client, userdata, message):
    with lock:
        database.execute("INSERT INTO buffer (body) VALUES (?)", (message.payload.decode(),))
        database.commit()
        stats["received"] = stats["received"] + 1


def bufferSize():
    with lock:
        size = database.execute("SELECT COUNT(*) FROM buffer").fetchone()[0]
    return size


# Manda un lote. Devuelve cuántos se enviaron (0 si falló: el lote sigue en el buffer)
def sendBatch(url):
    sentCount = 0
    with lock:
        rows = database.execute("SELECT id, body FROM buffer ORDER BY id LIMIT ?", (batchSize,)).fetchall()
    if len(rows) > 0:
        events = [json.loads(row[1]) for row in rows]
        request = urllib.request.Request(url, data=json.dumps({"events": events}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status == 200:
                    with lock:
                        database.execute("DELETE FROM buffer WHERE id <= ?", (rows[-1][0],))
                        database.commit()
                    sentCount = len(rows)
                    stats["sent"] = stats["sent"] + sentCount
        except Exception as error:
            stats["failedSends"] = stats["failedSends"] + 1
            logJson("WARNING", "send_failed", {"error": str(error), "buffered": bufferSize()})
    return sentCount


def main():
    url = findIngestUrl()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gateway-" + gatewayName)
    client.on_message = onMessage
    connected = False
    while not connected:
        try:
            client.connect(brokerHost, 1883)
            connected = True
        except Exception as error:
            logJson("ERROR", "mqtt_unavailable", {"error": str(error)})
            time.sleep(2)
    client.subscribe("warehouse/+/sensors/#", qos=1)
    client.loop_start()
    logJson("INFO", "gateway_started", {"ingest_url": url, "buffered": bufferSize()})
    lastReport = time.time()
    while True:
        sentCount = sendBatch(url)
        if sentCount == 0:
            time.sleep(1)
        # Cada 10 s deja una línea con cómo va el buffer
        if time.time() - lastReport >= 10:
            logJson("INFO", "gateway_stats", {"received": stats["received"], "sent": stats["sent"],
                                              "failed_sends": stats["failedSends"], "buffered": bufferSize()})
            lastReport = time.time()


if __name__ == "__main__":
    main()
