# Fase 11 - Observabilidad y Load Testing

## 1. Escenario
Consultas de inventario (GET /inventory/1) a la bodega WH-002 a través de su balanceador Nginx.
La carga se genera con hey desde la EC2 central, en la misma región de AWS, durante 15 s por prueba.

## 2. Carga
100, 500, 1000 y 5000 conexiones simultáneas, en tres configuraciones:
3 APIs + Redis ON, 1 API + Redis ON y 3 APIs + Redis OFF (12 pruebas).

## 3. Arquitectura
hey (central) -> Nginx (lb_WH-002) -> 1 o 3 copias de la API (FastAPI, 1 proceso cada una)
-> Redis (Cache Aside, TTL 30 s) -> PostgreSQL. Todo en una EC2 t3.small (2 vCPU, 2 GB).
Los movimientos siguen el camino API -> pending_events -> SNS -> sync-queue / audit-queue -> workers -> DB central.

## 4. Métricas
- API: req/s, P50, P95, P99 y error rate (de hey y de los logs JSON con duration_ms).
- Redis: hit ratio, hits, misses y memoria.
- Colas: mensajes esperando, en proceso, DLQ, procesados (CloudWatch) y reintentos.
- DB: conexiones activas, locks y tiempo de una consulta.
- CPU de cada contenedor durante cada prueba (docker stats).

## 5. Resultados

| Configuración | 100 | 500 | 1000 | 5000 |
|---|---|---|---|---|
| 3 APIs + Redis | 277 req/s, P95 808 ms, 0% errores | 255 req/s, P95 3053 ms, 0% | 273 req/s, 8,95% errores | 99,14% errores |
| 1 API + Redis | 192 req/s, P95 821 ms, 0% | 191 req/s, P95 2928 ms, 0% | 99,53% errores | 98,51% errores |
| 3 APIs sin Redis | 162 req/s, P95 1247 ms, 0% | 96,71% errores | 98,30% errores | 99,99% errores |

- Con Redis ON el hit ratio fue 1.0 y la DB estuvo cerca de 0% de CPU. Sin Redis subió a 19,5%.
- Con 3 copias la API usó 162-171% de CPU (casi los 2 núcleos). Con 1 copia, 103%.
- Seguimiento de COR-20261125-001: apareció en la API (http_request, 18,5 ms), en sync-worker
  (inventory_synced), en audit-worker (audit_saved) y en audit_log.
- Métricas con carga baja (WH-001): P50 10 ms, P95 14 ms, P99 23 ms, 0% errores, consulta en DB de 0,066 ms.

## 6. Cuello de botella
- Hasta 500 conexiones: la CPU de la API. Cada copia es un solo proceso de Python, las 3 comparten
  2 núcleos y la DB casi no trabaja.
- En 5000 conexiones: el balanceador. Nginx acepta unas 1024 conexiones por defecto y rechaza el resto.
  Por eso sale un req/s alto pero con 99% de errores, y la API en 1% de CPU: las peticiones no llegaron a la API.
- Sin Redis: las conexiones a la DB. Cada copia tiene como máximo 15 conexiones (pool de SQLAlchemy)
  y con 500 peticiones a la vez se quedan esperando hasta vencerse.

## 7. Hipótesis
- Pasar de 1 a 3 copias mejora (277 contra 192 req/s), pero no al triple porque están en la misma máquina.
  Repartidas en máquinas distintas deberían escalar casi lineal hasta que la DB o Redis se vuelvan el límite.
- El fallo en cadena viene de max_fails=1 en Nginx: bajo carga una demora saca a una copia de la lista
  10 s. Si salen las 3, Nginx responde error a todo aunque las APIs estén bien.
- El log JSON por petición cuesta CPU: en la Fase 5, sin logs, la misma prueba dio 450 req/s.
- El caché no ayuda cuando se escribe tanto como se lee (hit ratio 0 en WH-001), porque cada movimiento lo borra.

## 8. Evidencia
- carga/: salida de hey y CPU de cada una de las 12 pruebas.
- resultados_carga.csv: la tabla completa.
- metricas.txt: foto de API, Redis, colas y DB.
- Logs JSON con el correlationId en la API, los workers y el orquestador de transferencias.

## 9. Propuesta de mejora
1. Varios procesos por API (uvicorn --workers) y las copias en máquinas distintas, con un ALB de AWS
   que revise /health en vez del chequeo pasivo de Nginx.
2. Subir worker_connections de Nginx y usar max_fails más alto (por ejemplo 3) para evitar el fallo en cadena.
3. Agrandar el pool de conexiones a la DB o poner PgBouncer, para cuando Redis no esté.
4. Limitar la cantidad de peticiones por cliente (rate limiting) para responder 429 rápido en vez de caerse.
5. Logs más livianos: registrar todo en errores y solo una muestra de las peticiones exitosas.
