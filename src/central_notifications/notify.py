# Notificaciones (RF-16). Este archivo se copia igual en cada servicio de la central que avisa algo
# (catálogo, transferencias, notificaciones y sincronización). Todos escriben en la misma tabla de la base central.
#
# Cada notificación va para un ROL y, si es de una bodega, solo para esa bodega:
#   recipient_role = ADMIN_GLOBAL, recipient_warehouse = NULL   -> todos los Administradores Globales
#   recipient_role = ADMIN_BODEGA, recipient_warehouse = wh-001 -> el Administrador por Bodega de wh-001

createNotificationsSql = """
    CREATE TABLE IF NOT EXISTS notifications (
        id                  SERIAL PRIMARY KEY,
        category            VARCHAR(30) NOT NULL,
        level               VARCHAR(10) NOT NULL DEFAULT 'INFO',
        title               VARCHAR(200) NOT NULL,
        message             TEXT NOT NULL,
        recipient_role      VARCHAR(20) NOT NULL,
        recipient_warehouse VARCHAR(20),
        reference           VARCHAR(100),
        created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE IF NOT EXISTS notification_reads (
        notification_id INTEGER NOT NULL REFERENCES notifications (id) ON DELETE CASCADE,
        username        VARCHAR(50) NOT NULL,
        read_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (notification_id, username)
    );
"""

# Categorías: SOLICITUD, TRANSFERENCIA, DLQ, ALERTA. Niveles: INFO, WARNING, ERROR.
# No hace commit: va en la misma transacción de lo que se está avisando.
def notify(cursor, category, level, title, message, role, warehouse=None, reference=None):
    cursor.execute(
        """INSERT INTO notifications (category, level, title, message, recipient_role, recipient_warehouse, reference)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (category, level, title, message, role, warehouse, reference))
