# Fase 3 - Mensajería y workers (SNS + SQS)

Las bodegas publican cada cambio de inventario UNA vez en el tema SNS inventory-events.
SNS entrega una copia a cada cola suscrita (como un exchange fanout de RabbitMQ):
- sync-queue -> worker de sincronización (actualiza global_inventory)
- audit-queue -> worker de auditoría (guarda audit_log)

Cada evento lleva eventType (INVENTORY_UPDATED) y correlationId. El correlationId sale del
encabezado X-Correlation-ID (o se crea uno). En transferencias es el id de la transferencia.

## Resultados

| Experimento | Resultado |
|---|---|
| 1. Un movimiento con COR-PRUEBA-1 | Lo recibieron los dos workers: GUARDADO y AUDITADO con el mismo correlationId |
| 2. Auditoría apagada + 3 movimientos | La sincronización siguió (P-001 = 30). Al prender auditoría se puso al día: 4 registros, nada perdido |
| 3. Transferencia TR-96D37AD9 | Los 3 movimientos (wh-001 reserva, wh-002 recibe, wh-001 confirma) quedaron con el mismo correlationId |

Los dos workers son independientes: cada uno tiene su cola, así que si uno se cae el otro sigue,
y los mensajes esperan en la cola hasta que vuelva.
