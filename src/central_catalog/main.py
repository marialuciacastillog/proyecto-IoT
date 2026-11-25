# Catálogo de la central (puerto 8007).
# - Registro de bodegas con NIT, nombre, ubicación, identificador, descripción y encargado (RF-02).
# - Borrado de bodegas cuidando su historia (RF-03): si tiene historia se archiva; si no, se borra.
# - Catálogo global de productos con todos sus datos (RF-04). Solo el Administrador Global lo modifica.
# - Cada Administrador por Bodega elige qué productos maneja su bodega (RF-08).
# - El Administrador por Bodega pide productos nuevos o cambios (RF-05, RF-06) y el Administrador Global
#   los aprueba o los rechaza (RF-07). Cada paso deja una notificación (RF-16).
# Los cambios que deben llegar a las bodegas se guardan en catalog_outbox (misma transacción)
# y el publicador los manda al tema SNS catalog-events. Así no se pierde ningún aviso.
import json
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError
from auth import checkWarehouse, requireRoles
from roles import ADMIN_BODEGA, ADMIN_GLOBAL, ALL_ROLES
from nit import normalizeNit
from notify import createNotificationsSql, notify

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
app = FastAPI(title="Catálogo: bodegas y productos")

createTablesSql = """
    CREATE TABLE IF NOT EXISTS warehouses (
        warehouse_id     VARCHAR(20) PRIMARY KEY,
        nit              VARCHAR(20) UNIQUE NOT NULL,
        name             VARCHAR(120) NOT NULL,
        location         VARCHAR(200) NOT NULL,
        description      TEXT,
        manager_name     VARCHAR(120) NOT NULL,
        manager_username VARCHAR(50),
        status           VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
        created_by       VARCHAR(50),
        created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        archived_by      VARCHAR(50),
        archived_at      TIMESTAMPTZ
    );
    CREATE TABLE IF NOT EXISTS products (
        product_code     VARCHAR(20) PRIMARY KEY,
        name             VARCHAR(120) NOT NULL,
        category         VARCHAR(80) NOT NULL,
        description      TEXT,
        supplier         VARCHAR(120) NOT NULL,
        unit             VARCHAR(30) NOT NULL DEFAULT 'UNIDADES',
        cost             NUMERIC(14, 2) NOT NULL,
        sale_price       NUMERIC(14, 2) NOT NULL,
        wholesale_price  NUMERIC(14, 2) NOT NULL,
        initial_quantity INTEGER NOT NULL DEFAULT 0,
        created_by       VARCHAR(50),
        created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_by       VARCHAR(50),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE IF NOT EXISTS warehouse_products (
        warehouse_id     VARCHAR(20) NOT NULL REFERENCES warehouses (warehouse_id),
        product_code     VARCHAR(20) NOT NULL REFERENCES products (product_code),
        initial_quantity INTEGER NOT NULL,
        assigned_by      VARCHAR(50),
        assigned_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (warehouse_id, product_code)
    );
    CREATE TABLE IF NOT EXISTS product_requests (
        id             SERIAL PRIMARY KEY,
        request_type   VARCHAR(20) NOT NULL,
        warehouse_id   VARCHAR(20) NOT NULL,
        product_code   VARCHAR(20) NOT NULL,
        data           JSONB NOT NULL,
        reason         TEXT NOT NULL,
        status         VARCHAR(20) NOT NULL DEFAULT 'PENDING',
        requested_by   VARCHAR(50) NOT NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        decided_by     VARCHAR(50),
        decided_at     TIMESTAMPTZ,
        decision_note  TEXT
    );
    CREATE TABLE IF NOT EXISTS catalog_outbox (
        event_id     VARCHAR(40) PRIMARY KEY,
        event_type   VARCHAR(40) NOT NULL,
        payload      JSONB NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        published_at TIMESTAMPTZ
    );
"""

# Las 3 bodegas que ya existen en la infraestructura. El Administrador Global corrige sus datos después.
seedWarehouses = [
    ("wh-001", "900123456-8", "Bodega Norte", "Cali, Valle del Cauca - Zona Norte", "Bodega principal de recepción"),
    ("wh-002", "900234567-3", "Bodega Centro", "Cali, Valle del Cauca - Centro", "Bodega de mayor movimiento"),
    ("wh-003", "900345678-9", "Bodega Sur", "Cali, Valle del Cauca - Zona Sur", "Bodega de despacho al sur"),
]

warehouseColumns = """warehouse_id, nit, name, location, description, manager_name, manager_username, status,
                      created_by, created_at, updated_at, archived_by, archived_at"""
productColumns = """product_code, name, category, description, supplier, unit, cost, sale_price, wholesale_price,
                    initial_quantity, created_by, created_at, updated_by, updated_at"""


class WarehouseCreate(BaseModel):
    warehouse_id: str
    nit: str
    name: str = Field(..., min_length=3, max_length=120)
    location: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = Field(None, max_length=500)
    manager_name: str = Field(..., min_length=3, max_length=120)
    manager_username: Optional[str] = None


class WarehouseUpdate(BaseModel):
    nit: Optional[str] = None
    name: Optional[str] = Field(None, min_length=3, max_length=120)
    location: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = Field(None, max_length=500)
    manager_name: Optional[str] = Field(None, min_length=3, max_length=120)
    manager_username: Optional[str] = None
    status: Optional[str] = None


class ProductCreate(BaseModel):
    product_code: str
    name: str = Field(..., min_length=2, max_length=120)
    category: str = Field(..., min_length=2, max_length=80)
    description: Optional[str] = Field(None, max_length=500)
    supplier: str = Field(..., min_length=2, max_length=120)
    unit: str = Field("UNIDADES", min_length=2, max_length=30)
    cost: Decimal = Field(..., ge=0)
    sale_price: Decimal = Field(..., ge=0)
    wholesale_price: Decimal = Field(..., ge=0)
    initial_quantity: int = Field(0, ge=0)


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    category: Optional[str] = Field(None, min_length=2, max_length=80)
    description: Optional[str] = Field(None, max_length=500)
    supplier: Optional[str] = Field(None, min_length=2, max_length=120)
    unit: Optional[str] = Field(None, min_length=2, max_length=30)
    cost: Optional[Decimal] = Field(None, ge=0)
    sale_price: Optional[Decimal] = Field(None, ge=0)
    wholesale_price: Optional[Decimal] = Field(None, ge=0)
    initial_quantity: Optional[int] = Field(None, ge=0)


class ProductRequest(BaseModel):
    request_type: str                     # NEW_PRODUCT o UPDATE_PRODUCT
    product_code: str
    data: dict                            # NEW_PRODUCT: todos los datos del producto; UPDATE_PRODUCT: solo lo que cambia
    reason: str = Field(..., min_length=5, max_length=500)


class Decision(BaseModel):
    note: str = Field("", max_length=500)


class Assignment(BaseModel):
    product_code: str
    initial_quantity: Optional[int] = Field(None, ge=0)   # si no se manda, se usa la del catálogo


def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": "catalog", "level": level, "event": event}
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


# Guarda un aviso para las bodegas. Va en la MISMA transacción del cambio: o se guardan los dos o ninguno
def addOutbox(cursor, eventType, payload):
    eventId = "CAT-" + uuid.uuid4().hex[:12].upper()
    payload["eventId"] = eventId
    payload["eventType"] = eventType
    payload["createdAt"] = datetime.now(timezone.utc).isoformat()
    cursor.execute("INSERT INTO catalog_outbox (event_id, event_type, payload) VALUES (%s, %s, %s)",
                   (eventId, eventType, Json(payload, dumps=lambda value: json.dumps(value, default=str))))
    return eventId


def productForEvent(product):
    return {
        "productCode": product["product_code"], "name": product["name"], "category": product["category"],
        "description": product["description"], "supplier": product["supplier"], "unit": product["unit"],
        "cost": str(product["cost"]), "salePrice": str(product["sale_price"]),
        "wholesalePrice": str(product["wholesale_price"])
    }


def validateProductCode(code):
    clean = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,19}", clean):
        raise HTTPException(status_code=400, detail="Código inválido: letras, números y guiones (por ejemplo P-001)")
    return clean


# El precio mayorista es el de comprar al por mayor: no puede ser más caro que el precio de venta
def validatePrices(salePrice, wholesalePrice):
    if wholesalePrice > salePrice:
        raise HTTPException(status_code=400, detail="El precio mayorista no puede ser mayor que el precio de venta")


def getWarehouse(cursor, warehouseId):
    cursor.execute("SELECT " + warehouseColumns + " FROM warehouses WHERE warehouse_id = %s", (warehouseId.lower(),))
    warehouse = cursor.fetchone()
    if warehouse is None:
        raise HTTPException(status_code=404, detail="Bodega no encontrada")
    return warehouse


def getProduct(cursor, productCode):
    cursor.execute("SELECT " + productColumns + " FROM products WHERE product_code = %s", (productCode.upper(),))
    product = cursor.fetchone()
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado en el catálogo")
    return product


# Cuenta la historia de una bodega en las tablas de la central (solo las que ya existan)
def countHistory(cursor, warehouseId):
    checks = {
        "productos asignados": "SELECT COUNT(*) FROM warehouse_products WHERE warehouse_id = %s",
        "datos de inventario sincronizados": "SELECT COUNT(*) FROM global_inventory WHERE warehouse_id = %s",
        "movimientos auditados": "SELECT COUNT(*) FROM audit_log WHERE warehouse_id = %s",
        "transferencias": "SELECT COUNT(*) FROM transfers WHERE source_warehouse = %s OR destination_warehouse = %s",
    }
    tables = {"productos asignados": "warehouse_products", "datos de inventario sincronizados": "global_inventory",
              "movimientos auditados": "audit_log", "transferencias": "transfers"}
    history = {}
    for label, sql in checks.items():
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL AS present", ("public." + tables[label],))
        if cursor.fetchone()["present"]:
            params = (warehouseId, warehouseId) if "OR" in sql else (warehouseId,)
            cursor.execute(sql, params)
            count = cursor.fetchone()["count"]
            if count > 0:
                history[label] = count
    return history


def countActiveUsers(cursor, warehouseId):
    count = 0
    cursor.execute("SELECT to_regclass('public.users') IS NOT NULL AS present")
    if cursor.fetchone()["present"]:
        cursor.execute("SELECT COUNT(*) FROM users WHERE warehouse_id = %s AND active", (warehouseId,))
        count = cursor.fetchone()["count"]
    return count


@app.on_event("startup")
def startup():
    connection = connectDatabase()
    with connection.cursor() as cursor:
        cursor.execute(createTablesSql)
        cursor.execute(createNotificationsSql)
        cursor.execute("SELECT COUNT(*) FROM warehouses")
        if cursor.fetchone()[0] == 0:
            for warehouseId, nit, name, location, description in seedWarehouses:
                cursor.execute("""INSERT INTO warehouses (warehouse_id, nit, name, location, description, manager_name, created_by)
                                  VALUES (%s, %s, %s, %s, %s, 'Por asignar', 'sistema')""",
                               (warehouseId, nit, name, location, description))
            logJson("INFO", "warehouses_seeded", {"count": len(seedWarehouses)})
    connection.commit()
    connection.close()


@app.get("/health")
def health():
    connection = connectDatabase()
    connection.close()
    return {"status": "ok"}


# ---------------- Bodegas ----------------

@app.get("/warehouses")
def listWarehouses(include_archived: bool = False, user=Depends(requireRoles(*ALL_ROLES))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT " + warehouseColumns + ", (SELECT COUNT(*) FROM warehouse_products wp "
                           "WHERE wp.warehouse_id = w.warehouse_id) AS products FROM warehouses w "
                           "WHERE %s OR status = 'ACTIVE' ORDER BY warehouse_id", (include_archived,))
            warehouses = cursor.fetchall()
    finally:
        connection.close()
    return warehouses


@app.get("/warehouses/{warehouse_id}")
def readWarehouse(warehouse_id: str, user=Depends(requireRoles(*ALL_ROLES))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            warehouse = getWarehouse(cursor, warehouse_id)
    finally:
        connection.close()
    return warehouse


@app.post("/warehouses", status_code=201)
def createWarehouse(request: WarehouseCreate, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    warehouseId = request.warehouse_id.strip().lower()
    if not re.fullmatch(r"wh-\d{3}", warehouseId):
        raise HTTPException(status_code=400, detail="Identificador inválido. Formato: wh-004")
    nit = normalizeNit(request.nit)
    if nit == "":
        raise HTTPException(status_code=400, detail="NIT inválido: deben ser 9 dígitos, guion y el dígito de verificación correcto")
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT warehouse_id, nit FROM warehouses WHERE warehouse_id = %s OR nit = %s", (warehouseId, nit))
            existing = cursor.fetchone()
            if existing is not None:
                field = "identificador" if existing["warehouse_id"] == warehouseId else "NIT"
                raise HTTPException(status_code=409, detail="Ya existe una bodega con ese " + field)
            cursor.execute("INSERT INTO warehouses (warehouse_id, nit, name, location, description, manager_name, "
                           "manager_username, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING " + warehouseColumns,
                           (warehouseId, nit, request.name, request.location, request.description,
                            request.manager_name, request.manager_username, admin["username"]))
            warehouse = cursor.fetchone()
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "warehouse_created", {"warehouse_id": warehouseId, "by": admin["username"]})
    return warehouse


@app.patch("/warehouses/{warehouse_id}")
def updateWarehouse(warehouse_id: str, request: WarehouseUpdate, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    changes = request.model_dump(exclude_none=True)
    if "nit" in changes:
        changes["nit"] = normalizeNit(changes["nit"])
        if changes["nit"] == "":
            raise HTTPException(status_code=400, detail="NIT inválido: deben ser 9 dígitos, guion y el dígito de verificación correcto")
    if "status" in changes and changes["status"] not in ("ACTIVE", "ARCHIVED"):
        raise HTTPException(status_code=400, detail="status debe ser ACTIVE o ARCHIVED")
    if len(changes) == 0:
        raise HTTPException(status_code=400, detail="No hay cambios")
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            getWarehouse(cursor, warehouse_id)
            assignments = ", ".join(column + " = %s" for column in changes)
            cursor.execute("UPDATE warehouses SET " + assignments + ", updated_at = clock_timestamp() "
                           "WHERE warehouse_id = %s RETURNING " + warehouseColumns,
                           list(changes.values()) + [warehouse_id.lower()])
            warehouse = cursor.fetchone()
        connection.commit()
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Ya existe otra bodega con ese NIT")
    finally:
        connection.close()
    logJson("INFO", "warehouse_updated", {"warehouse_id": warehouse_id, "by": admin["username"], "fields": list(changes)})
    return warehouse


# RF-03: si la bodega tiene historia no se borra, se archiva (deja de aceptar usuarios, productos y
# transferencias nuevas, pero su historia se puede seguir consultando). Si no tiene nada, se borra.
@app.delete("/warehouses/{warehouse_id}")
def deleteWarehouse(warehouse_id: str, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    warehouseId = warehouse_id.lower()
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            getWarehouse(cursor, warehouseId)
            activeUsers = countActiveUsers(cursor, warehouseId)
            if activeUsers > 0:
                raise HTTPException(status_code=409, detail="La bodega tiene " + str(activeUsers) +
                                    " usuario(s) activo(s). Desactívalos o cámbialos de bodega primero")
            history = countHistory(cursor, warehouseId)
            if len(history) > 0:
                cursor.execute("UPDATE warehouses SET status = 'ARCHIVED', archived_by = %s, archived_at = clock_timestamp(), "
                               "updated_at = clock_timestamp() WHERE warehouse_id = %s", (admin["username"], warehouseId))
                result = {"warehouse_id": warehouseId, "result": "ARCHIVED", "history": history,
                          "message": "La bodega tiene historia, así que se archivó en vez de borrarse"}
            else:
                cursor.execute("DELETE FROM warehouses WHERE warehouse_id = %s", (warehouseId,))
                result = {"warehouse_id": warehouseId, "result": "DELETED", "history": {},
                          "message": "La bodega no tenía historia y se borró"}
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "warehouse_removed", {"warehouse_id": warehouseId, "by": admin["username"], "result": result["result"]})
    return result


# ---------------- Catálogo global de productos ----------------

@app.get("/products")
def listProducts(search: Optional[str] = None, user=Depends(requireRoles(*ALL_ROLES))):
    pattern = "%" + (search or "").strip() + "%"
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT " + productColumns + ", COALESCE((SELECT array_agg(wp.warehouse_id ORDER BY wp.warehouse_id) "
                           "FROM warehouse_products wp WHERE wp.product_code = p.product_code), '{}') AS warehouses "
                           "FROM products p WHERE product_code ILIKE %s OR name ILIKE %s OR category ILIKE %s "
                           "ORDER BY product_code", (pattern, pattern, pattern))
            products = cursor.fetchall()
    finally:
        connection.close()
    return products


@app.get("/products/{product_code}")
def readProduct(product_code: str, user=Depends(requireRoles(*ALL_ROLES))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            product = getProduct(cursor, product_code)
    finally:
        connection.close()
    return product


# Para comparar nombres sin que importen las mayúsculas, las tildes ni los espacios de más
def normalizeText(text):
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(plain.lower().split())


# Productos con el mismo nombre. Con proveedor, solo los del mismo nombre Y el mismo proveedor.
def findSameName(cursor, name, supplier=None, exceptCode=None):
    cursor.execute("SELECT product_code, name, supplier FROM products")
    found = []
    for row in cursor.fetchall():
        sameName = normalizeText(row["name"]) == normalizeText(name)
        sameSupplier = supplier is None or normalizeText(row["supplier"]) == normalizeText(supplier)
        if sameName and sameSupplier and row["product_code"] != exceptCode:
            found.append(row)
    return found


# Dos productos con el mismo nombre y el mismo proveedor casi seguro son el mismo producto
# (el inventario quedaría partido en dos). Si son distintos, el nombre debe decir en qué.
def checkNotDuplicated(cursor, name, supplier, exceptCode=None):
    duplicated = findSameName(cursor, name, supplier, exceptCode)
    if len(duplicated) > 0:
        other = duplicated[0]
        raise HTTPException(status_code=409, detail="Ya existe " + other["product_code"] + " · " + other["name"] + " de " +
                            other["supplier"] + ". Si es otro producto, cambia el nombre (por ejemplo, " +
                            other["name"] + " 20 oz)")


# Crea el producto. La usan el endpoint del Administrador Global y la aprobación de una solicitud.
def insertProduct(cursor, request, username):
    code = validateProductCode(request.product_code)
    validatePrices(request.sale_price, request.wholesale_price)
    cursor.execute("SELECT 1 FROM products WHERE product_code = %s", (code,))
    if cursor.fetchone() is not None:
        raise HTTPException(status_code=409, detail="Ya existe un producto con ese código")
    checkNotDuplicated(cursor, request.name, request.supplier)
    cursor.execute("INSERT INTO products (product_code, name, category, description, supplier, unit, cost, "
                   "sale_price, wholesale_price, initial_quantity, created_by, updated_by) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING " + productColumns,
                   (code, request.name, request.category, request.description, request.supplier,
                    request.unit.upper(), request.cost, request.sale_price, request.wholesale_price,
                    request.initial_quantity, username, username))
    return cursor.fetchone()


# Cambia el producto y deja el aviso PRODUCT_UPDATED para las bodegas que lo manejan
def changeProduct(cursor, productCode, request, username):
    changes = request.model_dump(exclude_none=True)
    if "unit" in changes:
        changes["unit"] = changes["unit"].upper()
    if len(changes) == 0:
        raise HTTPException(status_code=400, detail="No hay cambios")
    current = getProduct(cursor, productCode)
    validatePrices(changes.get("sale_price", current["sale_price"]), changes.get("wholesale_price", current["wholesale_price"]))
    if "name" in changes or "supplier" in changes:
        checkNotDuplicated(cursor, changes.get("name", current["name"]), changes.get("supplier", current["supplier"]),
                           current["product_code"])
    assignments = ", ".join(column + " = %s" for column in changes)
    cursor.execute("UPDATE products SET " + assignments + ", updated_by = %s, updated_at = clock_timestamp() "
                   "WHERE product_code = %s RETURNING " + productColumns,
                   list(changes.values()) + [username, current["product_code"]])
    product = cursor.fetchone()
    addOutbox(cursor, "PRODUCT_UPDATED", {"product": productForEvent(product), "performedBy": username})
    return product


# Agrega el producto a la bodega y deja el aviso PRODUCT_ASSIGNED para esa bodega
def addToWarehouse(cursor, warehouseId, product, initialQuantity, username):
    cursor.execute("SELECT 1 FROM warehouse_products WHERE warehouse_id = %s AND product_code = %s",
                   (warehouseId, product["product_code"]))
    if cursor.fetchone() is not None:
        raise HTTPException(status_code=409, detail="La bodega ya maneja ese producto")
    cursor.execute("INSERT INTO warehouse_products (warehouse_id, product_code, initial_quantity, assigned_by) "
                   "VALUES (%s, %s, %s, %s)", (warehouseId, product["product_code"], initialQuantity, username))
    return addOutbox(cursor, "PRODUCT_ASSIGNED", {
        "warehouseId": warehouseId, "product": productForEvent(product),
        "initialQuantity": initialQuantity, "performedBy": username
    })


@app.post("/products", status_code=201)
def createProduct(request: ProductCreate, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            product = insertProduct(cursor, request, admin["username"])
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "product_created", {"product_code": product["product_code"], "by": admin["username"]})
    return product


# Si cambia un producto, el aviso PRODUCT_UPDATED llega a todas las bodegas que lo manejan
@app.patch("/products/{product_code}")
def updateProduct(product_code: str, request: ProductUpdate, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            product = changeProduct(cursor, product_code, request, admin["username"])
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "product_updated", {"product_code": product_code, "by": admin["username"]})
    return product


# ---------------- Productos de cada bodega (RF-08) ----------------

@app.get("/warehouses/{warehouse_id}/products")
def listWarehouseProducts(warehouse_id: str, user=Depends(requireRoles(*ALL_ROLES))):
    checkWarehouse(user, warehouse_id)
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            getWarehouse(cursor, warehouse_id)
            cursor.execute("""SELECT wp.product_code, p.name, p.category, wp.initial_quantity, wp.assigned_by, wp.assigned_at
                              FROM warehouse_products wp JOIN products p USING (product_code)
                              WHERE wp.warehouse_id = %s ORDER BY wp.product_code""", (warehouse_id.lower(),))
            products = cursor.fetchall()
    finally:
        connection.close()
    return products


# El Administrador por Bodega agrega un producto del catálogo a SU bodega. La bodega lo crea
# cuando le llega el aviso PRODUCT_ASSIGNED (con su cantidad inicial como primera entrada).
@app.post("/warehouses/{warehouse_id}/products", status_code=202)
def assignProduct(warehouse_id: str, request: Assignment, user=Depends(requireRoles(ADMIN_BODEGA))):
    warehouseId = warehouse_id.lower()
    checkWarehouse(user, warehouseId)
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            warehouse = getWarehouse(cursor, warehouseId)
            if warehouse["status"] != "ACTIVE":
                raise HTTPException(status_code=409, detail="La bodega está archivada")
            product = getProduct(cursor, request.product_code)
            initialQuantity = product["initial_quantity"] if request.initial_quantity is None else request.initial_quantity
            eventId = addToWarehouse(cursor, warehouseId, product, initialQuantity, user["username"])
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "product_assigned", {"warehouse_id": warehouseId, "product_code": product["product_code"],
                                         "initial_quantity": initialQuantity, "by": user["username"], "event_id": eventId})
    return {"warehouse_id": warehouseId, "product_code": product["product_code"], "initial_quantity": initialQuantity,
            "event_id": eventId, "status": "SENT_TO_WAREHOUSE",
            "message": "La bodega creará el producto en unos segundos"}


# ---------------- Solicitudes de productos (RF-05, RF-06, RF-07) ----------------

requestColumns = """id, request_type, warehouse_id, product_code, data, reason, status, requested_by, created_at,
                    decided_by, decided_at, decision_note"""
requestTypes = {"NEW_PRODUCT": "producto nuevo", "UPDATE_PRODUCT": "cambio de producto"}
fieldNames = {"name": "nombre", "category": "categoría", "supplier": "proveedor", "unit": "unidad", "cost": "costo",
              "sale_price": "precio de venta", "wholesale_price": "precio mayorista", "initial_quantity": "cantidad inicial",
              "description": "descripción"}


# Explica en español los errores de validación más comunes
def explainError(item):
    context = item.get("ctx", {})
    messages = {
        "missing": "es obligatorio",
        "string_too_short": "debe tener al menos " + str(context.get("min_length", "")) + " caracteres",
        "string_too_long": "puede tener máximo " + str(context.get("max_length", "")) + " caracteres",
        "greater_than_equal": "debe ser mayor o igual a " + str(context.get("ge", "")),
        "decimal_parsing": "debe ser un número",
        "int_parsing": "debe ser un número entero",
        "string_type": "debe ser un texto",
    }
    return messages.get(item["type"], "no es válido")


def requestLabel(row):
    return "SOL-" + str(row["id"]).zfill(4)


# Revisa los datos de la solicitud con las mismas reglas que usa el Administrador Global al crear o editar
def validateRequestData(requestType, productCode, data):
    try:
        if requestType == "NEW_PRODUCT":
            validated = ProductCreate(product_code=productCode, **data)
            validatePrices(validated.sale_price, validated.wholesale_price)
        else:
            validated = ProductUpdate(**data)
            if len(validated.model_dump(exclude_none=True)) == 0:
                raise HTTPException(status_code=400, detail="La solicitud de cambio no trae ningún cambio")
    except HTTPException:
        raise
    except ValidationError as error:
        problems = []
        for item in error.errors():
            field = str(item["loc"][-1]) if item["loc"] else "datos"
            problems.append(fieldNames.get(field, field) + " " + explainError(item))
        raise HTTPException(status_code=400, detail="Datos inválidos. " + "; ".join(problems))
    return validated


def getRequest(cursor, requestId):
    cursor.execute("SELECT " + requestColumns + " FROM product_requests WHERE id = %s FOR UPDATE", (requestId,))
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return row


def withLabel(row):
    row["label"] = requestLabel(row)
    return row


@app.post("/requests", status_code=201)
def createRequest(request: ProductRequest, user=Depends(requireRoles(ADMIN_BODEGA))):
    if request.request_type not in requestTypes:
        raise HTTPException(status_code=400, detail="request_type debe ser NEW_PRODUCT o UPDATE_PRODUCT")
    code = validateProductCode(request.product_code)
    validated = validateRequestData(request.request_type, code, request.data)
    warehouseId = user["warehouse_id"]
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT 1 FROM products WHERE product_code = %s", (code,))
            exists = cursor.fetchone() is not None
            if request.request_type == "NEW_PRODUCT" and exists:
                raise HTTPException(status_code=409, detail="Ese producto ya está en el catálogo: agrégalo a tu bodega desde el catálogo")
            if request.request_type == "UPDATE_PRODUCT" and not exists:
                raise HTTPException(status_code=404, detail="Producto no encontrado en el catálogo")
            if request.request_type == "NEW_PRODUCT":
                checkNotDuplicated(cursor, validated.name, validated.supplier)
            elif validated.name is not None or validated.supplier is not None:
                current = getProduct(cursor, code)
                checkNotDuplicated(cursor, validated.name or current["name"], validated.supplier or current["supplier"], code)
            cursor.execute("SELECT id FROM product_requests WHERE product_code = %s AND status = 'PENDING' AND warehouse_id = %s",
                           (code, warehouseId))
            if cursor.fetchone() is not None:
                raise HTTPException(status_code=409, detail="Ya tienes una solicitud pendiente para ese producto")
            data = validated.model_dump(exclude_none=True, exclude={"product_code"})
            cursor.execute("INSERT INTO product_requests (request_type, warehouse_id, product_code, data, reason, requested_by) "
                           "VALUES (%s, %s, %s, %s, %s, %s) RETURNING " + requestColumns,
                           (request.request_type, warehouseId, code, Json(data, dumps=lambda value: json.dumps(value, default=str)),
                            request.reason, user["username"]))
            row = withLabel(cursor.fetchone())
            notify(cursor, "SOLICITUD", "INFO", "Nueva solicitud " + row["label"] + ": " + requestTypes[request.request_type] + " " + code,
                   user["username"] + " (" + warehouseId + ") pide " + requestTypes[request.request_type] + " " + code +
                   ". Motivo: " + request.reason, ADMIN_GLOBAL, None, row["label"])
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "request_created", {"request": row["label"], "type": request.request_type, "product_code": code,
                                        "warehouse_id": warehouseId, "by": user["username"]})
    return row


@app.get("/requests")
def listRequests(status: Optional[str] = None, user=Depends(requireRoles(ADMIN_GLOBAL, ADMIN_BODEGA))):
    warehouseId = user["warehouse_id"] if user["role"] == ADMIN_BODEGA else None
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT " + requestColumns + " FROM product_requests "
                           "WHERE (%s IS NULL OR warehouse_id = %s) AND (%s IS NULL OR status = %s) "
                           "ORDER BY (status = 'PENDING') DESC, created_at DESC LIMIT 200",
                           (warehouseId, warehouseId, status, status.upper() if status else None))
            rows = [withLabel(row) for row in cursor.fetchall()]
            # Para que el Admin Global lo vea al revisar: productos del catálogo con el mismo nombre
            for row in rows:
                row["similar"] = []
                name = (row["data"] or {}).get("name")
                if row["status"] == "PENDING" and name:
                    row["similar"] = [item["product_code"] + " · " + item["name"] + " (" + item["supplier"] + ")"
                                      for item in findSameName(cursor, name, None, row["product_code"])]
    finally:
        connection.close()
    return rows


# Aprobar: si es un producto nuevo, se crea en el catálogo y se agrega a la bodega que lo pidió
# (con la cantidad inicial de la solicitud). Si es un cambio, se aplica y llega a todas las bodegas.
@app.post("/requests/{request_id}/approve")
def approveRequest(request_id: int, decision: Decision, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            row = getRequest(cursor, request_id)
            if row["status"] != "PENDING":
                raise HTTPException(status_code=409, detail="La solicitud ya fue " + ("aprobada" if row["status"] == "APPROVED" else "rechazada"))
            if row["request_type"] == "NEW_PRODUCT":
                product = insertProduct(cursor, ProductCreate(product_code=row["product_code"], **row["data"]), admin["username"])
                cursor.execute("SELECT status FROM warehouses WHERE warehouse_id = %s", (row["warehouse_id"],))
                warehouse = cursor.fetchone()
                if warehouse is not None and warehouse["status"] == "ACTIVE":
                    addToWarehouse(cursor, row["warehouse_id"], product, product["initial_quantity"], row["requested_by"])
                result = "El producto " + product["product_code"] + " se creó en el catálogo y se agregó a " + row["warehouse_id"]
            else:
                changeProduct(cursor, row["product_code"], ProductUpdate(**row["data"]), admin["username"])
                result = "El cambio de " + row["product_code"] + " se aplicó y llega a las bodegas que lo manejan"
            cursor.execute("UPDATE product_requests SET status = 'APPROVED', decided_by = %s, decided_at = clock_timestamp(), "
                           "decision_note = %s WHERE id = %s RETURNING " + requestColumns,
                           (admin["username"], decision.note, request_id))
            row = withLabel(cursor.fetchone())
            notify(cursor, "SOLICITUD", "INFO", "Solicitud " + row["label"] + " aprobada",
                   result + "." + (" Nota: " + decision.note if decision.note else ""),
                   ADMIN_BODEGA, row["warehouse_id"], row["label"])
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "request_approved", {"request": row["label"], "by": admin["username"]})
    row["message"] = result
    return row


@app.post("/requests/{request_id}/reject")
def rejectRequest(request_id: int, decision: Decision, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    if len(decision.note.strip()) < 5:
        raise HTTPException(status_code=400, detail="Para rechazar escribe el motivo (mínimo 5 caracteres)")
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            row = getRequest(cursor, request_id)
            if row["status"] != "PENDING":
                raise HTTPException(status_code=409, detail="La solicitud ya fue " + ("aprobada" if row["status"] == "APPROVED" else "rechazada"))
            cursor.execute("UPDATE product_requests SET status = 'REJECTED', decided_by = %s, decided_at = clock_timestamp(), "
                           "decision_note = %s WHERE id = %s RETURNING " + requestColumns,
                           (admin["username"], decision.note.strip(), request_id))
            row = withLabel(cursor.fetchone())
            notify(cursor, "SOLICITUD", "WARNING", "Solicitud " + row["label"] + " rechazada",
                   "Tu solicitud de " + requestTypes[row["request_type"]] + " " + row["product_code"] +
                   " fue rechazada. Motivo: " + decision.note.strip(), ADMIN_BODEGA, row["warehouse_id"], row["label"])
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "request_rejected", {"request": row["label"], "by": admin["username"]})
    return row
