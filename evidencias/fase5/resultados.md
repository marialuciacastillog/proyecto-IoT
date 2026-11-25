# Fase 5 - Balanceador y escalamiento (WH-002)

Carga con hey desde la EC2 central: 30 s, GET /inventory/1

| Experimento | Copias | Req/s | Latencia promedio | Errores |
|---|---|---|---|---|
| 1 | 1 | 345 | 0,289 s | 0 |
| 2 | 3 | 450 | 0,222 s | 0 |
| 3 (se apaga una copia a los 10 s) | 3 -> 2 | 446 | - | 0 (13407 respuestas 200) |

Round robin observado (X-Upstream): 172.18.0.8 -> .5 -> .7 -> .8 -> .5 -> .7
