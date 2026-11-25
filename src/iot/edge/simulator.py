# Fase 12: simulador de sensores. Cada bodega tiene 20 sensores (4 de cada tipo) que mandan
# una lectura cada SENSOR_INTERVAL segundos al broker MQTT de la bodega.
# A propósito simula dos problemas reales:
#   - duplicados: a veces el sensor reenvía la misma lectura (mismo event_id)
#   - fuera de orden: a veces una lectura se retrasa y llega después de la siguiente
import json
import os
import random
import time
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

brokerHost = os.environ.get("MQTT_HOST", "localhost")
warehouses = os.environ.get("IOT_WAREHOUSES", "WH-001").split(",")
interval = float(os.environ.get("SENSOR_INTERVAL", "2"))
duplicateRate = float(os.environ.get("DUPLICATE_RATE", "0.02"))
outOfOrderRate = float(os.environ.get("OUT_OF_ORDER_RATE", "0.02"))

sensorTypes = {"TEMP": "temperature", "HUM": "humidity", "MOV": "movement", "DOOR": "opening", "ENER": "energy"}
sensorsPerType = 4


def readValue(prefix):
    value = 0.0
    if prefix == "TEMP":
        value = round(random.gauss(22, 4), 1)          # grados; a veces pasa de 30 (alerta)
    elif prefix == "HUM":
        value = round(random.uniform(40, 90), 1)       # %
    elif prefix == "MOV":
        value = float(random.random() < 0.2)           # 1 = hubo movimiento
    elif prefix == "DOOR":
        value = float(random.random() < 0.1)           # 1 = puerta abierta
    else:
        value = round(random.uniform(1, 6), 2)         # kW; a veces pasa de 5.5 (alerta)
    return value


def buildSensors():
    sensors = []
    for warehouseId in warehouses:
        for prefix in sensorTypes:
            for number in range(1, sensorsPerType + 1):
                sensors.append({"warehouseId": warehouseId, "prefix": prefix,
                                "sensorId": prefix + "-" + str(number).zfill(3), "sequence": 0, "held": None})
    return sensors


def buildEvent(sensor):
    sensor["sequence"] = sensor["sequence"] + 1
    event = {
        "event_id": "EVT-" + sensor["warehouseId"] + "-" + sensor["sensorId"] + "-" + str(sensor["sequence"]),
        "sequence_number": sensor["sequence"],
        "warehouse_id": sensor["warehouseId"],
        "sensor_id": sensor["sensorId"],
        "sensor_type": sensorTypes[sensor["prefix"]],
        "value": readValue(sensor["prefix"]),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return event


def publish(client, event):
    topic = "warehouse/" + event["warehouse_id"] + "/sensors/" + event["sensor_id"]
    client.publish(topic, json.dumps(event), qos=1)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="simulator-" + "-".join(warehouses))
    connected = False
    while not connected:
        try:
            client.connect(brokerHost, 1883)
            connected = True
        except Exception as error:
            print("Esperando el broker MQTT:", error, flush=True)
            time.sleep(2)
    client.loop_start()
    sensors = buildSensors()
    print("Simulador iniciado:", len(sensors), "sensores en", warehouses, "| cada", interval, "s", flush=True)
    while True:
        for sensor in sensors:
            event = buildEvent(sensor)
            if sensor["held"] is None and random.random() < outOfOrderRate:
                sensor["held"] = event          # se guarda para mandarlo después del siguiente
            else:
                publish(client, event)
                if sensor["held"] is not None:
                    publish(client, sensor["held"])   # llega después de uno más nuevo: fuera de orden
                    sensor["held"] = None
                if random.random() < duplicateRate:
                    publish(client, event)            # el mismo evento otra vez: duplicado
        time.sleep(interval)


if __name__ == "__main__":
    main()
