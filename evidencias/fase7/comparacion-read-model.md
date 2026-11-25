# Fase 7 - Alternativa A vs Alternativa B

A: la central les pregunta en vivo a las 3 bodegas (GET /inventory/1 en cada una) y suma.
B: la central responde desde su Read Model (GET /global/inventory/P-001).
Pruebas hechas en AWS (EC2 por bodega y central), desde Git Bash.

| | A: preguntar a todas | B: Read Model |
|---|---|---|
| Latencia | 2751 ms (3 viajes) | 612 ms (1 viaje) |
| Disponibilidad (WH-002 caída) | total = 90 (incorrecto), 3981 ms | total = 150 (último dato conocido), 634 ms |
| Consistencia | Siempre el dato actual | Eventual: unos segundos de atraso |
| Complejidad | Baja: sin colas ni workers | Mayor: publicador, cola SQS, worker, Read Model |
| Dependencia | De todas las bodegas | Solo de la base central |

Conclusión: B es mejor para consultas globales porque es más rápida y sigue respondiendo
si una bodega se cae. A cambio, el dato puede llegar unos segundos tarde (consistencia eventual).
