import os
import time
import json
import datetime
import psycopg2
from notify import createNotificationsSql, notify

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")

# Fase 6: tablas de la saga en la base central
createTablesSql = """
    CREATE TABLE IF NOT EXISTS transfers (
        transfer_id           VARCHAR(40) PRIMARY KEY,
        source_warehouse      VARCHAR(10) NOT NULL,
        destination_warehouse VARCHAR(10) NOT NULL,
        product_id            VARCHAR(20) NOT NULL,
        quantity              INT NOT NULL,
        status                VARCHAR(30) NOT NULL,
        reason                TEXT,
        step_started_at       TIMESTAMPTZ DEFAULT NOW(),
        step_sent_at          TIMESTAMPTZ,
        expires_at            TIMESTAMPTZ,
        created_at            TIMESTAMPTZ DEFAULT NOW(),
        updated_at            TIMESTAMPTZ DEFAULT NOW()
    );
    -- Quién pidió la transferencia y por qué (RF-23, RF-24)
    ALTER TABLE transfers ADD COLUMN IF NOT EXISTS requested_by VARCHAR(50);
    ALTER TABLE transfers ADD COLUMN IF NOT EXISTS justification TEXT;
    CREATE TABLE IF NOT EXISTS transfer_history (
        id          SERIAL PRIMARY KEY,
        transfer_id VARCHAR(40) NOT NULL,
        status      VARCHAR(30) NOT NULL,
        reason      TEXT,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    );
"""


def connectDatabase():
    connection = None
    while connection is None:
        try:
            connection = psycopg2.connect(databaseUrl)
            with connection.cursor() as cursor:
                cursor.execute(createTablesSql)
                cursor.execute(createNotificationsSql)
            connection.commit()
        except Exception as error:
            print("Esperando la base de datos central:", error)
            connection = None
            time.sleep(3)
    return connection


# Cambia el estado de la transferencia y lo anota en el historial.
# clock_timestamp() es la hora real; NOW() sería la hora en que empezó la transacción.
def changeStatus(cursor, transferId, newStatus, reason=""):
    cursor.execute(
        """UPDATE transfers SET status = %s, reason = %s, step_started_at = clock_timestamp(),
           step_sent_at = NULL, updated_at = clock_timestamp() WHERE transfer_id = %s""",
        (newStatus, reason, transferId))
    cursor.execute(
        "INSERT INTO transfer_history (transfer_id, status, reason, created_at) VALUES (%s, %s, %s, clock_timestamp())",
        (transferId, newStatus, reason))
    # Fase 11: log estructurado, como el ejemplo de la fase (transfer_completed, transfer_failed, ...)
    level = "INFO"
    if newStatus in ("FAILED", "COMPENSATING"):
        level = "WARNING"
    line = {"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "service": "transfer-orchestrator",
            "level": level, "event": "transfer_" + newStatus.lower(), "transfer_id": transferId,
            "correlation_id": transferId, "reason": reason}
    print(json.dumps(line, ensure_ascii=False), flush=True)
    # Notificación (RF-16) cuando una transferencia no se pudo hacer: al Administrador Global
    # y a los Administradores por Bodega de las dos bodegas
    if newStatus in ("FAILED", "RESERVATION_RELEASED"):
        cursor.execute("SELECT source_warehouse, destination_warehouse, product_id, quantity FROM transfers WHERE transfer_id = %s",
                       (transferId,))
        row = cursor.fetchone()
        values = list(row.values()) if isinstance(row, dict) else list(row)
        source, destination, productId, quantity = values[0], values[1], values[2], values[3]
        title = "Transferencia " + transferId + (" fallida" if newStatus == "FAILED" else " compensada")
        message = (source + " -> " + destination + ": " + productId + " x " + str(quantity) + ". " +
                   ("No se pudo hacer" if newStatus == "FAILED" else "Se devolvió lo apartado en " + source) +
                   (". Motivo: " + reason if reason else ""))
        notify(cursor, "TRANSFERENCIA", "WARNING", title, message, "ADMIN_GLOBAL", None, transferId)
        notify(cursor, "TRANSFERENCIA", "WARNING", title, message, "ADMIN_BODEGA", source, transferId)
        notify(cursor, "TRANSFERENCIA", "WARNING", title, message, "ADMIN_BODEGA", destination, transferId)
