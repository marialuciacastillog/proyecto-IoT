# Fase 9 - AWS Lambda

La Lambda report-processor se activa sola cuando llega un .json a warehouse-platform/reports/ en S3.
Hace 4 pasos: valida (JSON legible y totales que cuadran), transforma (resumen en exports/),
registra (un archivo por ejecución en audit/lambda/) y publica un evento en la cola report-events.

## Resultados

| Experimento | Resultado |
|---|---|
| 1. El Batch deja el reporte del día | S3 activó la Lambda sola: resumen en exports/, registro en audit/, evento REPORT_VALIDATED |
| 2. Reporte con totales malos (500 vs 98) | REPORT_REJECTED, quedó registrado y no se generó resumen |
| 3. 10 reportes a la vez | 10 de 10 procesados en 10 copias de la Lambda, todos en el mismo segundo |

## Comparación

| Característica | Worker (sincronización) | Batch (reporte diario) | Lambda (report-processor) |
|---|---|---|---|
| Ejecución continua | Sí, siempre leyendo SQS | No, corre y se apaga | No, solo cuando llega un archivo |
| Activación por evento | Sí, cada mensaje | No, cron a las 23:00 | Sí, cada archivo nuevo en S3 |
| Escalamiento | Lo decidimos nosotros | Lo decidimos nosotros | Lo hace AWS (abrió 10 copias) |
| Estado | PostgreSQL y SQS | PostgreSQL (batch_runs) y S3 | S3 (audit, exports) y SQS |
| Uso | Mensajes continuos | Muchos datos una vez al día | Tareas cortas cuando pasa algo |

La tabla no es una regla fija. La Lambda sirvió aquí porque cada reporte se revisa en milisegundos,
pero tiene un tiempo máximo, así que no serviría para un Batch con muchísimos datos. Y si los mensajes
llegan todo el tiempo, un worker prendido es más simple. Depende de cuánto dura la tarea,
cada cuánto pasa y cuánto dato mueve.
