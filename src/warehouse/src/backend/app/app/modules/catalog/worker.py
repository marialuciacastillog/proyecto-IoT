# Worker del catálogo de esta bodega.
# Lee su cola (catalog-wh-00X), que recibe por SNS los avisos del catálogo de la central:
#   PRODUCT_ASSIGNED -> el Administrador por Bodega eligió un producto para ESTA bodega:
#                       se crea el producto con su inventario y la cantidad inicial entra como RECEIPT.
#   PRODUCT_UPDATED  -> el Administrador Global cambió un producto: se actualiza la copia local.
# Así la bodega tiene su propia copia de los productos y sigue funcionando aunque la central se caiga.
import json
import time
from decimal import Decimal
import boto3
from app.config import settings
from app.database import SessionLocal, engine, Base
from app.modules.products.models import ProductEntity
from app.modules.inventory.models import InventoryEntity, InventoryMovementEntity
from app.modules.sync.models import PendingEventEntity
from app.modules.transfers.models import TransferCommandEntity
from app.modules.catalog.models import CatalogEventEntity
from app.modules.alerts.models import StockAlertEntity
from app.modules.inventory.schemas import MovementCreate
from app.modules.inventory.inventory_service import InventoryService
from app.modules.shared.correlation import currentCorrelationId


def findQueueUrl(sqsClient, queueName):
    queueUrl = None
    while queueUrl is None:
        try:
            queueUrl = sqsClient.get_queue_url(QueueName=queueName)["QueueUrl"]
        except Exception as error:
            print("Esperando la cola", queueName, ":", error, flush=True)
            time.sleep(5)
    return queueUrl


def copyProductFields(product, data):
    product.name = data["name"]
    product.category = data["category"]
    product.description = data.get("description")
    product.unit = data["unit"]
    product.supplier = data["supplier"]
    product.cost = Decimal(data["cost"])
    product.sale_price = Decimal(data["salePrice"])
    product.wholesale_price = Decimal(data["wholesalePrice"])


# Crea el producto (si no existe) y registra la cantidad inicial como primera entrada.
# La referencia del movimiento lleva el event_id: si el aviso se repite, no se suma dos veces.
def applyAssignment(db, event):
    data = event["product"]
    product = db.query(ProductEntity).filter(ProductEntity.sku == data["productCode"]).first()
    if product is None:
        product = ProductEntity(sku=data["productCode"])
        copyProductFields(product, data)
        db.add(product)
        db.flush()
        InventoryService(db).initialize_stock(product.id)
        db.commit()
    reference = "Cantidad inicial (catálogo " + event["eventId"] + ")"
    quantity = int(event.get("initialQuantity") or 0)
    alreadyCounted = db.query(InventoryMovementEntity).filter(InventoryMovementEntity.reference == reference).first() is not None
    if quantity > 0 and not alreadyCounted:
        movement = MovementCreate(product_id=product.id, type="RECEIPT", quantity=quantity, reference=reference,
                                  lot_code="INICIAL")
        InventoryService(db).process_movement(movement, event.get("performedBy", "catalogo"))
    return "PRODUCTO_CREADO " + data["productCode"] + " con " + str(quantity) + " unidades"


def applyUpdate(db, event):
    data = event["product"]
    product = db.query(ProductEntity).filter(ProductEntity.sku == data["productCode"]).first()
    result = "NO_LO_MANEJA " + data["productCode"]
    if product is not None:
        copyProductFields(product, data)
        db.commit()
        result = "PRODUCTO_ACTUALIZADO " + data["productCode"]
    return result


def applyEvent(event):
    db = SessionLocal()
    result = "IGNORADO"
    try:
        currentCorrelationId.set(event["eventId"])
        alreadyApplied = db.query(CatalogEventEntity).filter(CatalogEventEntity.event_id == event["eventId"]).first()
        isForMe = event["eventType"] != "PRODUCT_ASSIGNED" or event.get("warehouseId") == settings.WAREHOUSE_ID
        if alreadyApplied is not None:
            result = "REPETIDO " + alreadyApplied.result
        elif isForMe:
            if event["eventType"] == "PRODUCT_ASSIGNED":
                result = applyAssignment(db, event)
            elif event["eventType"] == "PRODUCT_UPDATED":
                result = applyUpdate(db, event)
            db.add(CatalogEventEntity(event_id=event["eventId"], event_type=event["eventType"], result=result))
            db.commit()
        else:
            result = "ES_DE_OTRA_BODEGA (" + str(event.get("warehouseId")) + ")"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return result


def createTables():
    ready = False
    while not ready:
        try:
            Base.metadata.create_all(bind=engine)
            ready = True
        except Exception as error:
            print("Esperando la base de datos:", error, flush=True)
            time.sleep(3)


def main():
    createTables()
    sqsClient = boto3.client("sqs", region_name=settings.AWS_REGION)
    queueUrl = findQueueUrl(sqsClient, "catalog-" + settings.WAREHOUSE_ID)
    print("Worker del catálogo iniciado para", settings.WAREHOUSE_ID, flush=True)
    while True:
        try:
            response = sqsClient.receive_message(QueueUrl=queueUrl, MaxNumberOfMessages=10, WaitTimeSeconds=20)
            for message in response.get("Messages", []):
                try:
                    event = json.loads(message["Body"])
                    result = applyEvent(event)
                    print(event["eventType"], event["eventId"], "->", result, flush=True)
                    sqsClient.delete_message(QueueUrl=queueUrl, ReceiptHandle=message["ReceiptHandle"])
                except Exception as error:
                    print("Error con un aviso del catálogo, SQS lo reintentará:", error, flush=True)
        except Exception as error:
            print("No se pudo leer la cola, se reintenta en 5 segundos:", error, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
