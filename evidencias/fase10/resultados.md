# Fase 10 - Fallos, idempotencia y DLQ

Los workers revisan cada mensaje antes de procesarlo. Si está mal formado (JSON inválido o le falta
un campo) se manda directo a la DLQ con el motivo, sin reintentar. Si falla por otra cosa, no se borra:
SQS lo vuelve a entregar a los 30 s y después de 3 intentos lo pasa solo a la DLQ.

## Resultados

| Experimento | Resultado |
|---|---|
| 1. Mismo evento dos veces | GUARDADO y luego REPETIDO: no se aplicó dos veces |
| 2. JSON inválido y mensaje sin quantity | Directo a la DLQ con su motivo, un solo intento |
| 3. Error que no se arregla (nombre de bodega muy largo) | Intento 1, 2 y 3, luego SQS lo pasó a la DLQ |
| 4. Base central apagada 20 s | El worker esperó la base y luego guardó COR-DB-CAIDA (36). Nada perdido |
| 5. Redis apagado | HTTP 200, la API leyó directo de PostgreSQL |
| 6. Transferencia de P-999 | FAILED de una: el producto no existe en wh-001, sin reintentos |

## Matriz

| Error | ¿Retry? | ¿DLQ? | ¿Compensación? | Por qué |
|---|---|---|---|---|
| Timeout | Sí | Después de 3 intentos | Depende | Puede ser pasajero; si ya se apartó algo en una transferencia, la saga lo devuelve |
| JSON inválido | No | Sí, de una | No | Reintentar da el mismo error; va a la DLQ con el motivo para revisarlo |
| DB temporalmente caída | Sí | Después del límite | Depende | Cuando la base vuelve el mensaje se guarda; si nunca se arregla, a los 3 intentos va a la DLQ |
| Producto inexistente | No | Sí | No | Reintentar no hace aparecer el producto; no se apartó nada, no hay qué compensar |
| Mensaje duplicado | No hace falta | No | No | event_id + processed_events: la segunda vez se ignora |
| Redis caído | No | No | No | Se lee de PostgreSQL; Redis solo acelera |
