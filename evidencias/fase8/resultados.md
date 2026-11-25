# Fase 8 - S3 y Batch

El proceso Batch (src/central_batch) lee lo que llegó de todas las bodegas (tabla sync_history),
arma un reporte por bodega y uno general, y los guarda en S3. Cada ejecución queda en batch_runs.
Está programado con cron todos los días a las 23:00 (hora del servidor, UTC).

## Resultados

| Ejecución | Qué se probó | Resultado |
|---|---|---|
| 1 | Correr el Batch | SUCCESS, 3 bodegas, 4 archivos en S3, total 98 unidades |
| 2 | Bucket que no existe | FAILED, el error quedó guardado en batch_runs |
| 3 | Volver a correr el mismo día | SUCCESS, siguen siendo 4 archivos (se reemplazaron, no se duplicaron) |

Total: wh-001 = 28, wh-002 = 30, wh-003 = 40. P-001 = 90, P-002 = 8.

## Preguntas

**¿Por qué no generar este reporte dentro del request HTTP?**
Porque tiene que leer los datos de todas las bodegas y puede tardar mucho. El cliente se quedaría esperando
y la API estaría ocupada en vez de atender consultas normales. Si falla a la mitad, no queda registro.
Separado, corre aparte, queda anotado y se puede repetir.

**¿Cuándo usar Batch?**
Cuando hay que procesar muchos datos juntos cada cierto tiempo y el resultado no se necesita al instante,
como el reporte diario. Corre, termina y se apaga.

**¿Cuándo usar un worker?**
Cuando llegan mensajes todo el tiempo y hay que atenderlos apenas llegan, uno por uno,
como el worker de sincronización que lee la cola de SQS.

**¿Cuándo usar Lambda?**
Cuando la tarea es corta y pasa de vez en cuando, por ejemplo reaccionar a un archivo nuevo en S3.
No hay que tener una máquina prendida y se paga solo por uso, pero tiene un tiempo máximo de ejecución.
