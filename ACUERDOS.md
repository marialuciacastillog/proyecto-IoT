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
- Pendiente: roles y credenciales. Cuando exista el login, cada botón del menú (data-view) se muestra según el rol.
