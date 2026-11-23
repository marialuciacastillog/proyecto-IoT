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
{ "eventId": "uuid", "warehouseId": "wh-001", "productId": "P-001", "quantity": 90, "createdAt": "2026-11-22T10:00:00" }
- productId = código del producto (sku), igual en todas las bodegas
- quantity = cantidad TOTAL después del movimiento

## Mensaje de reportes (API de reportes -> worker de reportes)
{ "job_id": "uuid", "report_type": "global_inventory", "format": "csv" }

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
