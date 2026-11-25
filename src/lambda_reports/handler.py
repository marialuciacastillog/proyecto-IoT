# Fase 9: Lambda que se activa sola cuando el Batch deja un reporte nuevo en S3
# (warehouse-platform/reports/...). Hace 4 pasos:
#   1. validar     -> que el JSON esté bien y que los totales cuadren
#   2. transformar -> arma un resumen corto en warehouse-platform/exports/
#   3. registrar   -> deja constancia de cada ejecución en warehouse-platform/audit/lambda/
#   4. publicar    -> manda un evento a la cola report-events
import json
import os
from datetime import datetime, timezone
from urllib.parse import unquote_plus
import boto3

eventsQueueUrl = os.environ.get("EVENTS_QUEUE_URL", "")
s3Client = boto3.client("s3")
sqsClient = boto3.client("sqs")


# Devuelve la lista de problemas encontrados. Si está vacía, el reporte es válido.
def validateReport(report):
    problems = []
    for field in ["date", "totalUnits", "warehouses"]:
        if field not in report:
            problems.append("falta el campo " + field)
    if len(problems) == 0:
        warehouseSum = 0
        for warehouse in report["warehouses"]:
            warehouseSum = warehouseSum + warehouse.get("totalUnits", 0)
        if warehouseSum != report["totalUnits"]:
            problems.append("totalUnits es " + str(report["totalUnits"]) + " pero las bodegas suman " + str(warehouseSum))
        for warehouse in report["warehouses"]:
            if warehouse.get("totalUnits", 0) < 0:
                problems.append("la bodega " + str(warehouse.get("warehouseId")) + " tiene unidades negativas")
    return problems


# Resumen corto: lo que alguien quiere ver rápido del día
def buildSummary(report, sourceKey):
    biggest = None
    smallest = None
    for warehouse in report["warehouses"]:
        if biggest is None or warehouse["totalUnits"] > biggest["totalUnits"]:
            biggest = warehouse
        if smallest is None or warehouse["totalUnits"] < smallest["totalUnits"]:
            smallest = warehouse
    summary = {
        "date": report["date"],
        "source": sourceKey,
        "totalUnits": report["totalUnits"],
        "warehouseCount": len(report["warehouses"]),
        "warehouseWithMostUnits": biggest["warehouseId"] if biggest else None,
        "warehouseWithFewestUnits": smallest["warehouseId"] if smallest else None,
        "productCount": len(report.get("productTotals", {}))
    }
    return summary


def saveJson(bucket, key, content):
    s3Client.put_object(Bucket=bucket, Key=key, Body=json.dumps(content, indent=2), ContentType="application/json")
    print("Guardado en S3:", key)


def processRecord(record, requestId):
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])
    print("Llegó un reporte nuevo:", key)
    exportKey = None
    report = None
    problems = []

    # 1. validar
    try:
        body = s3Client.get_object(Bucket=bucket, Key=key)["Body"].read()
        report = json.loads(body)
        problems = validateReport(report)
    except Exception as error:
        problems = ["no se pudo leer el JSON: " + str(error)]

    status = "VALID"
    if len(problems) > 0:
        status = "REJECTED"
        print("Reporte RECHAZADO:", problems)
    else:
        # 2. transformar
        exportKey = key.replace("/reports/", "/exports/").replace("daily-", "summary-")
        saveJson(bucket, exportKey, buildSummary(report, key))
        print("Reporte VÁLIDO, total de unidades:", report["totalUnits"])

    # 3. registrar: un archivo por ejecución (el requestId lo da AWS y es distinto cada vez)
    processedAt = datetime.now(timezone.utc).isoformat()
    fileName = key.split("/")[-1].replace(".json", "")
    auditKey = "warehouse-platform/audit/lambda/" + fileName + "-" + requestId + ".json"
    saveJson(bucket, auditKey, {
        "requestId": requestId,
        "source": key,
        "status": status,
        "problems": problems,
        "export": exportKey,
        "processedAt": processedAt
    })

    # 4. publicar evento
    eventType = "REPORT_VALIDATED"
    if status == "REJECTED":
        eventType = "REPORT_REJECTED"
    sqsClient.send_message(QueueUrl=eventsQueueUrl, MessageBody=json.dumps({
        "eventType": eventType,
        "source": key,
        "export": exportKey,
        "problems": problems,
        "processedAt": processedAt
    }))
    print("Evento publicado:", eventType)
    return status


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        results.append(processRecord(record, context.aws_request_id))
    return {"processed": len(results), "results": results}
