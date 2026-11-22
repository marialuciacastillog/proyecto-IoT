# Acuerdos del equipo (no cambiar sin hablarlo)

## Quién hace qué
- Persona 1 (sincronización): networking, ec2, sqs_sync, src/warehouse, src/central_sync_worker, sync.tf
- Persona 2 (reportes): sqs_reports, s3_reports, src/central_reports, reports.tf

## Nombres en AWS
- Cola de sincronización: sync-queue (y su DLQ: sync-queue-dlq)
- Cola de reportes: reports-queue (y su DLQ: reports-queue-dlq)
- Bucket de reportes: proyecto-iot-reportes-mlcg
- Región: us-east-1

## Mensaje de sincronización (bodega -> central)
{ "eventId": "uuid", "warehouseId": "wh-001", "productId": "P-001", "quantity": 90, "createdAt": "2026-11-22T10:00:00" }
- quantity = cantidad TOTAL después del movimiento

## Mensaje de reportes (API central -> worker de reportes)
{ "jobId": "uuid", "requestedAt": "2026-11-22T10:00:00" }

## Tabla compartida
- src/database/global_inventory.sql
