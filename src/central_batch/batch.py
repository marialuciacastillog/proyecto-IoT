# Fase 8: proceso Batch.
# Toma lo que llegó de TODAS las bodegas (tabla sync_history), arma un reporte del día
# y lo guarda en S3. Cada ejecución queda anotada en la tabla batch_runs.
#
# Uso:  python batch.py              -> procesa el día de hoy (UTC)
#       python batch.py 2026-11-25   -> procesa ese día (sirve para volver a correr un día)
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
import boto3
import psycopg2

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
bucketName = os.environ.get("REPORTS_BUCKET_NAME", "")
awsRegion = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
rootFolder = "warehouse-platform"

createRunsSql = """
    CREATE TABLE IF NOT EXISTS batch_runs (
        id          SERIAL PRIMARY KEY,
        run_date    DATE NOT NULL,
        status      VARCHAR(20) NOT NULL,
        started_at  TIMESTAMP NOT NULL DEFAULT clock_timestamp(),
        finished_at TIMESTAMP,
        warehouses  INTEGER,
        files       INTEGER,
        error       TEXT
    )
"""

# Cantidad de cada producto al FINAL del día: el último dato que llegó antes de medianoche.
# Como solo mira datos hasta esa hora, correrlo hoy o en una semana da el mismo resultado.
finalQuantitiesSql = """
    SELECT DISTINCT ON (warehouse_id, product_id) warehouse_id, product_id, quantity
    FROM sync_history
    WHERE created_at < %s
    ORDER BY warehouse_id, product_id, created_at DESC
"""

# Cuántos cambios tuvo cada producto DURANTE ese día
dayChangesSql = """
    SELECT warehouse_id, product_id, COUNT(*)
    FROM sync_history
    WHERE created_at >= %s AND created_at < %s
    GROUP BY warehouse_id, product_id
"""


def connectDatabase():
    connection = None
    attempts = 0
    while connection is None and attempts < 10:
        try:
            connection = psycopg2.connect(databaseUrl)
        except Exception as error:
            print("Esperando la base de datos central:", error)
            attempts = attempts + 1
            time.sleep(3)
    return connection


def startRun(connection, runDate):
    with connection.cursor() as cursor:
        cursor.execute(createRunsSql)
        cursor.execute("INSERT INTO batch_runs (run_date, status) VALUES (%s, 'RUNNING') RETURNING id", (runDate,))
        runId = cursor.fetchone()[0]
    connection.commit()
    return runId


def finishRun(connection, runId, status, warehouses, files, error):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE batch_runs SET status = %s, finished_at = clock_timestamp(), warehouses = %s, files = %s, error = %s WHERE id = %s",
            (status, warehouses, files, error, runId)
        )
    connection.commit()


# Arma un diccionario por bodega: { "WH-001": { "P-001": {"finalQuantity": 20, "changesToday": 2} } }
def readWarehouses(connection, runDate):
    nextDay = runDate + timedelta(days=1)
    warehouses = {}
    with connection.cursor() as cursor:
        cursor.execute(finalQuantitiesSql, (nextDay,))
        for warehouseId, productId, quantity in cursor.fetchall():
            if warehouseId not in warehouses:
                warehouses[warehouseId] = {}
            warehouses[warehouseId][productId] = {"finalQuantity": quantity, "changesToday": 0}
        cursor.execute(dayChangesSql, (runDate, nextDay))
        for warehouseId, productId, changes in cursor.fetchall():
            warehouses[warehouseId][productId]["changesToday"] = changes
    connection.commit()
    return warehouses


def buildWarehouseReport(warehouseId, products, runDate, generatedAt):
    productList = []
    totalUnits = 0
    totalChanges = 0
    for productId in sorted(products):
        item = products[productId]
        productList.append({"productId": productId, "finalQuantity": item["finalQuantity"], "changesToday": item["changesToday"]})
        totalUnits = totalUnits + item["finalQuantity"]
        totalChanges = totalChanges + item["changesToday"]
    report = {
        "warehouseId": warehouseId,
        "date": runDate.isoformat(),
        "generatedAt": generatedAt,
        "totalUnits": totalUnits,
        "changesToday": totalChanges,
        "products": productList
    }
    return report


def buildGlobalReport(warehouseReports, runDate, generatedAt, runId):
    summary = []
    productTotals = {}
    totalUnits = 0
    for report in warehouseReports:
        summary.append({"warehouseId": report["warehouseId"], "totalUnits": report["totalUnits"], "changesToday": report["changesToday"]})
        totalUnits = totalUnits + report["totalUnits"]
        for item in report["products"]:
            productTotals[item["productId"]] = productTotals.get(item["productId"], 0) + item["finalQuantity"]
    report = {
        "date": runDate.isoformat(),
        "generatedAt": generatedAt,
        "runId": runId,
        "totalUnits": totalUnits,
        "warehouses": summary,
        "productTotals": productTotals
    }
    return report


# Siempre el mismo nombre para el mismo día: si se vuelve a correr, se reemplaza el archivo y no se duplica
def saveToS3(s3Client, key, content):
    s3Client.put_object(Bucket=bucketName, Key=key, Body=json.dumps(content, indent=2), ContentType="application/json")
    print("Guardado en S3:", key)


def runBatch(connection, s3Client, runDate, runId):
    generatedAt = datetime.now(timezone.utc).isoformat()
    year = runDate.strftime("%Y")
    month = runDate.strftime("%m")
    warehouses = readWarehouses(connection, runDate)
    warehouseReports = []
    for warehouseId in sorted(warehouses):
        report = buildWarehouseReport(warehouseId, warehouses[warehouseId], runDate, generatedAt)
        warehouseReports.append(report)
        key = rootFolder + "/warehouses/" + warehouseId + "/" + year + "/" + month + "/inventory-" + runDate.isoformat() + ".json"
        saveToS3(s3Client, key, report)
    globalReport = buildGlobalReport(warehouseReports, runDate, generatedAt, runId)
    globalKey = rootFolder + "/reports/" + year + "/" + month + "/daily-" + runDate.isoformat() + ".json"
    saveToS3(s3Client, globalKey, globalReport)
    print("Bodegas:", len(warehouseReports), "| unidades en total:", globalReport["totalUnits"])
    return len(warehouseReports)


def main():
    runDate = date.today()
    if len(sys.argv) > 1:
        runDate = date.fromisoformat(sys.argv[1])
    exitCode = 1
    connection = connectDatabase()
    if connection is None:
        print("No hubo conexión con la base de datos")
    else:
        runId = startRun(connection, runDate)
        print("Ejecución", runId, "del día", runDate.isoformat(), "iniciada")
        try:
            s3Client = boto3.client("s3", region_name=awsRegion)
            warehouseCount = runBatch(connection, s3Client, runDate, runId)
            finishRun(connection, runId, "SUCCESS", warehouseCount, warehouseCount + 1, None)
            print("Ejecución", runId, "terminada: SUCCESS")
            exitCode = 0
        except Exception as error:
            connection.rollback()
            finishRun(connection, runId, "FAILED", None, None, str(error))
            print("Ejecución", runId, "terminada: FAILED ->", error)
        connection.close()
    sys.exit(exitCode)


if __name__ == "__main__":
    main()
