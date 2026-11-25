# Fase 12 - Extensión IoT

## Arquitectura
Sensores simulados -> MQTT (Mosquitto, en cada bodega) -> Gateway / Edge (buffer en disco, SQLite)
-> HTTP por lotes -> Servicio de ingesta (central, puerto 8005) -> SNS iot-events
-> colas iot-storage, iot-alerts, iot-analytics -> 3 workers -> PostgreSQL central.

- 100 sensores en 5 bodegas (20 por bodega: temperatura, humedad, movimiento, apertura y energía).
  WH-001 y WH-002 en sus máquinas; WH-003, WH-004 y WH-005 en la máquina de wh-003.
- Evento: event_id, sequence_number, warehouse_id, sensor_id, sensor_type, value, timestamp.
- El simulador genera a propósito duplicados (2%) y lecturas fuera de orden (2%).

## Cómo se maneja cada problema

| Problema | Solución |
|---|---|
| Mensajes duplicados | event_id único: Storage, Alerts y Analytics ignoran el segundo (idempotencia) |
| Fuera de orden | sequence_number: el estado del sensor solo cambia si la lectura es más nueva |
| Pérdida de conexión | El gateway guarda todo en disco y solo borra lo que la central confirmó |
| Buffering | Buffer SQLite en un volumen: sobrevive a reinicios del gateway |
| Sequence numbers | Permiten detectar huecos (pérdidas) y desorden. El sensor guarda el suyo en disco |
| Consistencia eventual | Si una bodega se desconecta, la central se atrasa, pero al volver queda completa |

## Resultados

| Experimento | Resultado |
|---|---|
| 1. Normal (cada 2 s) | 49,9 lecturas/s, latencia promedio 1,2 s, P95 2,7 s, 0 perdidas de 33.295, 674 duplicados y 2.654 fuera de orden manejados, 100 de 100 sensores con su dato más nuevo |
| 2. Red de WH-001 caída 60 s | Buffer de 508 lecturas, WH-001 67 s sin datos en la central. Al volver: buffer en 0, 0 perdidas de 45.075, latencia máxima 79 s |
| 3. Doble carga (cada 1 s) | Llegan ~100/s y Storage guarda 91,3/s. La cola iot-storage acumuló 1.282 y la latencia subió a 10 s (P95 22 s) |
| Recuperación (vuelta a cada 2 s) | La cola bajó a ~0 y la latencia a 1,1 s. 1 lectura faltante de 54.528 (ver abajo) |
| 4. Reinicio de sensores | Antes del arreglo: el sensor volvía a contar desde 1, repetía event_id viejos y todo se descartaba como duplicado (11.169 duplicados, 0 lecturas nuevas guardadas). Después: siguió desde el 231 y WH-001 guardó lo mismo que las demás bodegas |

## La lectura faltante
Falta la lectura 437 del sensor ENER-003 de WH-001, justo cuando se reinició ese simulador para volver
a la carga normal. Para simular desorden, el sensor retiene una lectura en memoria y la manda después de
la siguiente. Al apagarse de golpe, esa lectura nunca salió: se perdió dentro del sensor, antes de entrar
al sistema. La tubería (MQTT, gateway, ingesta, SNS, colas y workers) no perdió ninguna lectura.
Se detectó gracias al sequence_number: sin él no se habría notado el hueco.

## Análisis
- Throughput: con 50 lecturas/s el sistema va al día; con 100/s el worker de Storage no alcanza (91/s).
- Latencia: normal 1,1 s. Sube cuando hay cola (Storage lento) o cuando el gateway estuvo sin red.
- Pérdida: 0 en la tubería. Durante la sobrecarga la cuenta de pérdidas sube por lecturas que todavía
  están en la cola; cuando la cola se vacía vuelve a 0. La única pérdida real fue en un sensor que se apagó.
- Duplicados: llegan (del simulador y de reenvíos del gateway) pero nunca se guardan dos veces.
- Caída de red: la bodega afectada se atrasa, las demás siguen normales y al volver se pone al día sola.
- Reinicio de sensores: si el sensor reinicia su contador, rompe la idempotencia por event_id.
  Por eso debe guardar su sequence_number en memoria persistente.

## Cuello de botella y mejoras
- El worker de Storage es uno solo y por cada lectura hace 3 consultas, un commit y un llamado a SQS.
- Mejoras:
  1. Varios workers de Storage leyendo la misma cola. Se puede porque el procesamiento es idempotente.
  2. Guardar las 10 lecturas de cada lote en una sola transacción y borrarlas con delete_message_batch.
  3. Que el sensor guarde en disco las lecturas pendientes de enviar, no solo el contador,
     para no perder lo que tenía en memoria si se apaga.
  4. Si el orden importara más, usar una cola FIFO por sensor. Aquí no hace falta: el sequence_number lo resuelve.
