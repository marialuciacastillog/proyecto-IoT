# Acuerdos del equipo (no cambiar sin hablarlo)

## Dónde está cada cosa
- Sincronización: infrastructure/sync.tf, modules/networking, modules/ec2, modules/sqs_sync,
  src/warehouse (pendientes y publicador) y src/central_sync_worker (base central + worker)
- Reportes: infrastructure/reports.tf, modules/sqs_reports, modules/s3_reports y la carpeta reports/

## Nombres en AWS
- Cola de sincronización: sync-queue (y su DLQ: sync-queue-dlq)
- Cola de reportes: reports-queue (y su DLQ: reports-queue-dlq)
- Bucket de reportes: proyecto-iot-reportes-mlcg-2
- Región: us-east-1

## Mensaje de sincronización (bodega -> central)
{ "eventId": "uuid", "eventType": "INVENTORY_UPDATED", "correlationId": "COR-1A2B3C4D", "warehouseId": "wh-001", "productId": "P-001", "quantity": 90, "createdAt": "2026-11-22T10:00:00" }
- Se publica en el tema SNS inventory-events, que copia el mensaje a sync-queue y audit-queue (Fase 3)
- correlationId = sale del encabezado X-Correlation-ID de la petición (o se crea uno); en transferencias es el id de la transferencia
- productId = código del producto (sku), igual en todas las bodegas
- quantity = cantidad TOTAL después del movimiento

## Mensaje de reportes (API de reportes -> worker de reportes)
{ "job_id": "uuid", "report_type": "global_inventory", "format": "csv" }
- report_type: global_inventory, inventory_by_warehouse, low_stock, transfers, audit (lista en reports/worker/queries.py)
- format: csv o json

## Base de datos central (central_db, puerto 5440)
- global_inventory y processed_events: sincronización (src/database y src/central_sync_worker)
- report_jobs y report_outbox: reportes (reports/db)

## Variables de los reportes
- REPORTS_QUEUE_URL, REPORTS_BUCKET_NAME, DATABASE_URL
- Salen de: terraform output -raw reports_queue_url / reports_bucket_name

## Orden para prender
1. src/central_sync_worker (crea la red iot-central-network y el volumen)
2. reports
3. src/warehouse

## Transferencias entre bodegas (Fase 6)
- API: src/central_transfers (puerto 8010). POST /transfers, GET /transfers/{id}
- Colas: transfer-commands-wh-001/002/003 (central -> bodega) y transfer-events (bodega -> central)
- Orden: { "transferId": "TR-XXXX", "command": "RESERVE|RECEIVE|COMMIT|RELEASE", "productId": "P-001", "quantity": 30, "expiresAt": "fecha o null" }
- Respuesta: { "transferId": "TR-XXXX", "command": "RESERVE", "warehouseId": "wh-001", "ok": true, "reason": "" }
- Orden para prender en la central: src/central_sync_worker -> reports -> src/central_transfers

## Fronts
- Bodega: src/warehouse/src/frontend/app. La misma imagen en las 3 bodegas (puertos 8081, 8082, 8083).
  Nginx manda /api/... al balanceador de la bodega y entrega /config.js con WAREHOUSE_ID (del compose).
  Vistas: Resumen (stock y caché), Productos, Inventario (movimientos con X-Correlation-ID) e Historial.
- Central: src/central_frontend (puerto 8080). Se prende después de src/central_sync_worker (usa iot-central-network).
  Nginx reenvía /svc/reports/ -> iot-reports-api, /svc/transfers/ -> central_transfers_api y /svc/iot/ -> iot_ingestion.
  Vistas: Resumen, Inventario global (Read Model), Transferencias (saga) y Reportes (S3).
- Login (Etapa A): los dos fronts usan session.js (mismo archivo). Cada elemento con data-roles="ROL1,ROL2" solo se ve para esos roles.
## Etapa A - Usuarios, login y roles

- Servicio de identidad en la central, puerto **8008**.
- Login: `POST /auth/login` con `{"username": "...", "password": "..."}` -> devuelve `access_token` (dura 2 horas).
- Todas las APIs (bodegas, reportes, transferencias) piden el encabezado `Authorization: Bearer <access_token>`.
  Sin token responden 401; con un rol sin permiso responden 403. `/health` no pide token.
- Roles: `ADMIN_GLOBAL`, `ADMIN_BODEGA`, `ENCARGADO`, `LIDER`. `ADMIN_BODEGA` y `ENCARGADO` llevan `warehouse_id` y solo pueden usar su bodega.
- Solo `ADMIN_GLOBAL` crea usuarios: `POST /users`, `GET /users`, `PATCH /users/{id}`, `POST /users/{id}/reset-password`.
- Cada quien cambia su contraseña con `POST /auth/change-password`. Reglas: mínimo 8 caracteres, con letras y números. 5 intentos fallidos bloquean 5 minutos.
- El primer admin es `admin`; su contraseña inicial está en la central, en `/opt/proyecto/secrets/admin_password.txt` (solo con sudo).
- Los movimientos guardan `created_by` y el evento de sincronización lleva `performedBy`. Las transferencias guardan `requested_by` y `justification`.

## Etapa B - Bodegas y catálogo global

- Servicio de catálogo en la central, puerto **8007** (`src/central_catalog`). El front central lo usa por `/svc/catalog/` y el de bodega por `/catalog/`.
- Bodegas (RF-02): `GET/POST /warehouses`, `GET/PATCH/DELETE /warehouses/{id}`. Campos: `warehouse_id` (wh-004), `nit` (con dígito de verificación DIAN), `name`, `location`, `description`, `manager_name`, `manager_username`.
  Solo el Admin Global crea, edita y elimina. Al arrancar se crean wh-001, wh-002 y wh-003 con datos de ejemplo.
- Eliminar (RF-03): si la bodega tiene usuarios activos -> 409. Si tiene historia (productos, inventario, auditoría o transferencias) -> se archiva (`status = ARCHIVED`). Si no tiene nada -> se borra.
  Una bodega archivada no acepta usuarios, productos ni transferencias nuevas. Se reactiva con `PATCH {"status": "ACTIVE"}`.
- Catálogo (RF-04): `GET/POST /products`, `GET/PATCH /products/{code}`. Campos: `product_code`, `name`, `category`, `description`, `supplier`, `unit`, `cost`, `sale_price`, `wholesale_price`, `initial_quantity`.
  Regla: `wholesale_price <= sale_price`. Solo el Admin Global crea y edita; todos los roles consultan.
- Productos de cada bodega (RF-08): el Admin por Bodega hace `POST /warehouses/{su bodega}/products` con `{"product_code", "initial_quantity"}` (202).
  La central guarda el aviso en `catalog_outbox`, el publicador lo manda a SNS `catalog-events` y cada bodega lo recibe en su cola `catalog-wh-00X`.
  El worker de la bodega crea el producto y registra la cantidad inicial como un RECEIPT a nombre de quien la pidió.
- Avisos: `PRODUCT_ASSIGNED` (para una bodega) y `PRODUCT_UPDATED` (cambio en el catálogo, llega a todas las bodegas que tienen el producto). Son idempotentes por `eventId`.
- La bodega ya no crea productos sueltos (se quitó `POST /products` de la API de bodega).
