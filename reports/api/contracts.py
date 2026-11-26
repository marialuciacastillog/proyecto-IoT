# Contratos digitales (RF-19, RF-20).
# - El Líder los carga: el archivo (PDF, Word o imagen, máximo 10 MB) se guarda en S3, en el mismo bucket
#   de los reportes pero en la carpeta contratos/, cifrado (SSE-S3). En la base central queda la ficha.
# - Cada contrato queda asociado a la información que le corresponde: tipo, contraparte (proveedor o cliente),
#   y opcionalmente una bodega y un producto del catálogo. Se valida que existan.
# - Se consultan con filtros y se descargan con un link temporal de S3 (15 minutos).
# - No se borran: un contrato es un documento legal. Si se cargó mal, el Líder lo archiva con un motivo.
# - Se guarda la huella SHA-256 del archivo para poder comprobar después que nadie lo cambió.
import hashlib
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

import boto3
import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from psycopg.rows import dict_row

from api.auth import requireRoles
from api.roles import ADMIN_GLOBAL, LIDER

router = APIRouter(prefix="/contracts", tags=["Contratos"])

DATABASE_URL = os.environ["DATABASE_URL"]
BUCKET_NAME = os.environ.get("REPORTS_BUCKET_NAME", "")
DOWNLOAD_SECONDS = 900
MAX_BYTES = 10 * 1024 * 1024
s3 = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))

contractTypes = {
    "PROVEEDOR": "Proveedor",
    "CLIENTE": "Cliente",
    "ARRENDAMIENTO": "Arrendamiento de bodega",
    "SERVICIO": "Servicio (transporte, vigilancia, aseo...)",
    "OTRO": "Otro"
}

# Tipos de archivo permitidos y cómo empiezan por dentro (no basta con la extensión)
fileKinds = {
    ".pdf": ("application/pdf", [b"%PDF"]),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", [b"PK\x03\x04"]),
    ".png": ("image/png", [b"\x89PNG"]),
    ".jpg": ("image/jpeg", [b"\xff\xd8\xff"]),
    ".jpeg": ("image/jpeg", [b"\xff\xd8\xff"])
}

createContractsSql = """
    CREATE TABLE IF NOT EXISTS contracts (
        id             SERIAL PRIMARY KEY,
        title          VARCHAR(150) NOT NULL,
        contract_type  VARCHAR(20) NOT NULL,
        counterparty   VARCHAR(150) NOT NULL,
        warehouse_id   VARCHAR(20),
        product_code   VARCHAR(20),
        start_date     DATE NOT NULL,
        end_date       DATE,
        value          NUMERIC(16, 2),
        notes          TEXT,
        file_name      VARCHAR(200) NOT NULL,
        content_type   VARCHAR(120) NOT NULL,
        size_bytes     INTEGER NOT NULL,
        sha256         VARCHAR(64) NOT NULL,
        s3_key         TEXT NOT NULL,
        status         VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
        uploaded_by    VARCHAR(50) NOT NULL,
        uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        archived_by    VARCHAR(50),
        archived_at    TIMESTAMPTZ,
        archive_reason TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_contracts_warehouse ON contracts (warehouse_id);
"""

contractColumns = """id, title, contract_type, counterparty, warehouse_id, product_code, start_date, end_date, value,
                     notes, file_name, content_type, size_bytes, sha256, status, uploaded_by, uploaded_at,
                     archived_by, archived_at, archive_reason"""


def createTables():
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(createContractsSql)


def label(contractId):
    return "CT-" + str(contractId).zfill(4)


# Vigente, por vencer (menos de 30 días) o vencido, según la fecha de hoy
def validity(row):
    today = date.today()
    result = "VIGENTE"
    if row["status"] == "ARCHIVED":
        result = "ARCHIVADO"
    elif row["start_date"] > today:
        result = "FUTURO"
    elif row["end_date"] is not None and row["end_date"] < today:
        result = "VENCIDO"
    elif row["end_date"] is not None and (row["end_date"] - today).days <= 30:
        result = "POR_VENCER"
    return result


def withExtras(row):
    row["label"] = label(row["id"])
    row["validity"] = validity(row)
    row["type_label"] = contractTypes.get(row["contract_type"], row["contract_type"])
    return row


def badRequest(message):
    return HTTPException(status_code=400, detail=message)


def parseDate(text, name):
    value = None
    if text:
        try:
            value = date.fromisoformat(text)
        except ValueError:
            raise badRequest(name + " debe ser una fecha AAAA-MM-DD")
    return value


def parseValue(text):
    value = None
    if text:
        try:
            value = Decimal(text)
        except InvalidOperation:
            raise badRequest("El valor del contrato debe ser un número")
        if value < 0:
            raise badRequest("El valor del contrato no puede ser negativo")
    return value


def checkFile(fileName, content):
    extension = os.path.splitext(fileName.lower())[1]
    if extension not in fileKinds:
        raise badRequest("Solo se aceptan PDF, Word (.docx) o imágenes (.png, .jpg)")
    contentType, signatures = fileKinds[extension]
    if len(content) == 0:
        raise badRequest("El archivo está vacío")
    if len(content) > MAX_BYTES:
        raise badRequest("El archivo pesa más de 10 MB")
    if not any(content.startswith(signature) for signature in signatures):
        raise badRequest("El archivo no es un " + extension[1:].upper() + " de verdad (su contenido no corresponde a la extensión)")
    return contentType


# Nombre seguro para S3: sin rutas ni caracteres raros
def safeName(fileName):
    base = os.path.basename(fileName.replace("\\", "/"))
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[-120:] or "contrato"


def checkAssociations(cursor, warehouseId, productCode):
    if warehouseId:
        cursor.execute("SELECT 1 FROM warehouses WHERE warehouse_id = %s", (warehouseId,))
        if cursor.fetchone() is None:
            raise badRequest("La bodega " + warehouseId + " no está registrada")
    if productCode:
        cursor.execute("SELECT 1 FROM products WHERE product_code = %s", (productCode,))
        if cursor.fetchone() is None:
            raise badRequest("El producto " + productCode + " no está en el catálogo")


@router.get("/types")
def listTypes(user=Depends(requireRoles(ADMIN_GLOBAL, LIDER))):
    return [{"contract_type": key, "label": value} for key, value in contractTypes.items()]


# El Líder carga un contrato (RF-19). Va como formulario con el archivo (multipart/form-data).
@router.post("", status_code=201)
async def uploadContract(
    file: UploadFile = File(...),
    title: str = Form(...),
    contract_type: str = Form(...),
    counterparty: str = Form(...),
    start_date: str = Form(...),
    end_date: Optional[str] = Form(None),
    warehouse_id: Optional[str] = Form(None),
    product_code: Optional[str] = Form(None),
    value: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    user=Depends(requireRoles(LIDER))
):
    title = title.strip()
    counterparty = counterparty.strip()
    contractType = contract_type.strip().upper()
    warehouseId = (warehouse_id or "").strip().lower() or None
    productCode = (product_code or "").strip().upper() or None
    if len(title) < 3 or len(title) > 150:
        raise badRequest("El título debe tener entre 3 y 150 caracteres")
    if len(counterparty) < 2 or len(counterparty) > 150:
        raise badRequest("La contraparte (con quién es el contrato) debe tener entre 2 y 150 caracteres")
    if contractType not in contractTypes:
        raise badRequest("Tipo de contrato inválido. Opciones: " + ", ".join(contractTypes))
    startDate = parseDate(start_date, "La fecha de inicio")
    endDate = parseDate(end_date, "La fecha de fin")
    if startDate is None:
        raise badRequest("La fecha de inicio es obligatoria")
    if endDate is not None and endDate < startDate:
        raise badRequest("La fecha de fin no puede ser antes de la de inicio")
    contractValue = parseValue(value)
    content = await file.read()
    contentType = checkFile(file.filename or "", content)
    sha256 = hashlib.sha256(content).hexdigest()

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            checkAssociations(cursor, warehouseId, productCode)
            cursor.execute("SELECT id FROM contracts WHERE sha256 = %s AND status = 'ACTIVE'", (sha256,))
            repeated = cursor.fetchone()
            if repeated is not None:
                raise HTTPException(status_code=409, detail="Ese mismo archivo ya está cargado como " + label(repeated["id"]))
            cursor.execute(
                "INSERT INTO contracts (title, contract_type, counterparty, warehouse_id, product_code, start_date, end_date, "
                "value, notes, file_name, content_type, size_bytes, sha256, s3_key, uploaded_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', %s) RETURNING id",
                (title, contractType, counterparty, warehouseId, productCode, startDate, endDate, contractValue,
                 (notes or "").strip() or None, safeName(file.filename or ""), contentType, len(content), sha256,
                 user["username"]))
            contractId = cursor.fetchone()["id"]
            s3Key = "contratos/" + label(contractId) + "/" + safeName(file.filename or "")
            # Primero S3 y después el commit: si S3 falla, la ficha no queda guardada sin su archivo
            s3.put_object(Bucket=BUCKET_NAME, Key=s3Key, Body=content, ContentType=contentType,
                          ServerSideEncryption="AES256",
                          Metadata={"contract": label(contractId), "sha256": sha256, "uploaded-by": user["username"]})
            cursor.execute("UPDATE contracts SET s3_key = %s WHERE id = %s RETURNING " + contractColumns, (s3Key, contractId))
            row = cursor.fetchone()
    return withExtras(row)


# Consultar contratos (RF-20) con filtros
@router.get("")
def listContracts(warehouse_id: Optional[str] = None, contract_type: Optional[str] = None, search: Optional[str] = None,
                  include_archived: bool = False, user=Depends(requireRoles(ADMIN_GLOBAL, LIDER))):
    pattern = "%" + (search or "").strip() + "%"
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT " + contractColumns + " FROM contracts "
            "WHERE (%(warehouse)s::text IS NULL OR warehouse_id = %(warehouse)s) "
            "AND (%(type)s::text IS NULL OR contract_type = %(type)s) "
            "AND (title ILIKE %(pattern)s OR counterparty ILIKE %(pattern)s OR COALESCE(product_code, '') ILIKE %(pattern)s) "
            "AND (%(archived)s OR status = 'ACTIVE') ORDER BY uploaded_at DESC LIMIT 500",
            {"warehouse": (warehouse_id or "").lower() or None, "type": (contract_type or "").upper() or None,
             "pattern": pattern, "archived": include_archived}).fetchall()
    return [withExtras(row) for row in rows]


def getContract(conn, contractId):
    row = conn.execute("SELECT " + contractColumns + ", s3_key FROM contracts WHERE id = %s", (contractId,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")
    return row


@router.get("/{contract_id}")
def readContract(contract_id: int, user=Depends(requireRoles(ADMIN_GLOBAL, LIDER))):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        row = getContract(conn, contract_id)
    row.pop("s3_key")
    return withExtras(row)


# Link temporal para descargar el archivo desde el navegador
@router.get("/{contract_id}/download")
def downloadContract(contract_id: int, user=Depends(requireRoles(ADMIN_GLOBAL, LIDER))):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        row = getContract(conn, contract_id)
    url = s3.generate_presigned_url("get_object", ExpiresIn=DOWNLOAD_SECONDS, Params={
        "Bucket": BUCKET_NAME, "Key": row["s3_key"],
        "ResponseContentDisposition": 'attachment; filename="' + row["file_name"] + '"'})
    return {"contract": label(contract_id), "download_url": url, "expires_in_seconds": DOWNLOAD_SECONDS}


# Comprueba que el archivo que está en S3 es el mismo que se cargó (misma huella SHA-256)
@router.get("/{contract_id}/verify")
def verifyContract(contract_id: int, user=Depends(requireRoles(ADMIN_GLOBAL, LIDER))):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        row = getContract(conn, contract_id)
    content = s3.get_object(Bucket=BUCKET_NAME, Key=row["s3_key"])["Body"].read()
    current = hashlib.sha256(content).hexdigest()
    return {"contract": label(contract_id), "intact": current == row["sha256"], "sha256_saved": row["sha256"], "sha256_now": current}


class ArchiveRequest(BaseModel):
    reason: str = ""


@router.post("/{contract_id}/archive")
def archiveContract(contract_id: int, request: ArchiveRequest, user=Depends(requireRoles(LIDER))):
    reason = request.reason.strip()
    if len(reason) < 5:
        raise badRequest("Para archivar escribe el motivo (mínimo 5 caracteres)")
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        row = getContract(conn, contract_id)
        if row["status"] == "ARCHIVED":
            raise HTTPException(status_code=409, detail="El contrato ya está archivado")
        row = conn.execute("UPDATE contracts SET status = 'ARCHIVED', archived_by = %s, archived_at = clock_timestamp(), "
                           "archive_reason = %s WHERE id = %s RETURNING " + contractColumns,
                           (user["username"], reason, contract_id)).fetchone()
    return withExtras(row)
