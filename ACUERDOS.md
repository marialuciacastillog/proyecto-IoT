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
{ "eventId": "uuid", "eventType": "INVENTORY_UPDATED", "correlationId": "COR-1A2B3C4D", "warehouseId": "wh-001", "productId": "P-001", "quantity": 90, "createdAt": "2026-11-22T10:00:00" }
- Se publica en el tema SNS inventory-events, que copia el mensaje a sync-queue y audit-queue (Fase 3)
- correlationId = sale del encabezado X-Correlation-ID de la petición (o se crea uno); en transferencias es el id de la transferencia
- productId = código del producto (sku), igual en todas las bodegas
- quantity = cantidad TOTAL después del movimiento
- details (opcional) = qué movimiento fue: `movementType`, `movementQuantity`, `unitPrice`, `total`, `lotCode`, `reference`...
  Con `eventType: STOCK_ALERT` el mensaje es una alerta de la bodega y details trae `alertType`, `level`, `title` y `message`

## Mensaje de reportes (API de reportes -> worker de reportes)
{ "job_id": "uuid", "report_type": "global_inventory", "format": "csv" }
- report_type: global_inventory, inventory_by_warehouse, low_stock, transfers, audit, movements_history, sales_purchases,
  sales_by_product, warehouse_summary, stock_alerts, contracts (lista en reports/worker/queries.py)
- Los reportes históricos aceptan filtros al pedirlos: date_from, date_to, warehouse_id, product_id. Se guardan en
  report_jobs.params y el worker los lee de ahí (el mensaje de la cola no cambia)
- format: csv o json

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

## Transferencias entre bodegas (Fase 6)
- API: src/central_transfers (puerto 8010). POST /transfers, GET /transfers/{id}
- Colas: transfer-commands-wh-001/002/003 (central -> bodega) y transfer-events (bodega -> central)
- Orden: { "transferId": "TR-XXXX", "command": "RESERVE|RECEIVE|COMMIT|RELEASE", "productId": "P-001", "quantity": 30, "expiresAt": "fecha o null" }
- Respuesta: { "transferId": "TR-XXXX", "command": "RESERVE", "warehouseId": "wh-001", "ok": true, "reason": "" }
- Orden para prender en la central: src/central_sync_worker -> reports -> src/central_transfers

## Fronts
- Se entra a los dos por **HTTPS**: `https://<IP de la central>/` y `https://<IP de la bodega>/`. Los puertos HTTP
  (8080 y 8081-8083) solo redirigen a HTTPS.
- Bodega: src/warehouse/src/frontend/app. La misma imagen en las 3 bodegas.
  Nginx manda /api/... al balanceador de la bodega y entrega /config.js con WAREHOUSE_ID (del compose).
  Vistas: Resumen (stock y caché), Productos, Inventario (movimientos con X-Correlation-ID) e Historial.
- Central: src/central_frontend (443, y el 8080 redirige). Se prende después de src/central_sync_worker (usa iot-central-network).
  Nginx reenvía /svc/reports/ -> iot-reports-api, /svc/transfers/ -> central_transfers_api y /svc/iot/ -> iot_ingestion.
  Vistas: Resumen, Inventario global (Read Model), Transferencias (saga) y Reportes (S3).
- Login: los dos fronts usan session.js (mismo archivo). Cada elemento con data-roles="ROL1,ROL2" solo se ve para esos roles.
## Usuarios, login y roles

- Servicio de identidad en la central, puerto **8008**.
- Login: `POST /auth/login` con `{"username": "...", "password": "..."}` -> devuelve `access_token` (dura 2 horas).
- Todas las APIs (bodegas, reportes, transferencias) piden el encabezado `Authorization: Bearer <access_token>`.
  Sin token responden 401; con un rol sin permiso responden 403. `/health` no pide token.
- Roles: `ADMIN_GLOBAL`, `ADMIN_BODEGA`, `ENCARGADO`, `LIDER`. `ADMIN_BODEGA` y `ENCARGADO` llevan `warehouse_id` y solo pueden usar su bodega.
- Solo `ADMIN_GLOBAL` crea usuarios: `POST /users`, `GET /users`, `PATCH /users/{id}`, `POST /users/{id}/reset-password`.
- Cada quien cambia su contraseña con `POST /auth/change-password`. Reglas: mínimo 8 caracteres, con letras y números. 5 intentos fallidos bloquean 5 minutos.
- El primer admin es `admin`; su contraseña inicial está en la central, en `/opt/proyecto/secrets/admin_password.txt` (solo con sudo).
- Los movimientos guardan `created_by` y el evento de sincronización lleva `performedBy`. Las transferencias guardan `requested_by` y `justification`.

## Bodegas y catálogo global

- Servicio de catálogo en la central, puerto **8007** (`src/central_catalog`). El front central lo usa por `/svc/catalog/` y el de bodega por `/catalog/`.
- Bodegas (RF-02): `GET/POST /warehouses`, `GET/PATCH/DELETE /warehouses/{id}`. Campos: `warehouse_id` (wh-004), `nit` (con dígito de verificación DIAN), `name`, `location`, `description`, `manager_name`, `manager_username`.
  Solo el Admin Global crea, edita y elimina. Al arrancar se crean wh-001, wh-002 y wh-003 con datos de ejemplo.
- Eliminar (RF-03): si la bodega tiene usuarios activos -> 409. Si tiene historia (productos, inventario, auditoría o transferencias) -> se archiva (`status = ARCHIVED`). Si no tiene nada -> se borra.
  Una bodega archivada no acepta usuarios, productos ni transferencias nuevas. Se reactiva con `PATCH {"status": "ACTIVE"}`.
- Catálogo (RF-04): `GET/POST /products`, `GET/PATCH /products/{code}`. Campos: `product_code`, `name`, `category`, `description`, `supplier`, `unit`, `cost`, `sale_price`, `wholesale_price`, `initial_quantity`.
  Regla: `wholesale_price <= sale_price`. Solo el Admin Global crea y edita; todos los roles consultan.
  Regla: no puede haber dos productos con el mismo nombre y el mismo proveedor (sin importar mayúsculas, tildes ni espacios) -> 409.
  Aplica al crear, al editar y en las solicitudes. Si solo coincide el nombre, la solicitud trae `similar` para que el Admin Global lo vea al revisar.
- Productos de cada bodega (RF-08): el Admin por Bodega hace `POST /warehouses/{su bodega}/products` con `{"product_code", "initial_quantity"}` (202).
  La central guarda el aviso en `catalog_outbox`, el publicador lo manda a SNS `catalog-events` y cada bodega lo recibe en su cola `catalog-wh-00X`.
  El worker de la bodega crea el producto y registra la cantidad inicial como un RECEIPT a nombre de quien la pidió.
- Avisos: `PRODUCT_ASSIGNED` (para una bodega) y `PRODUCT_UPDATED` (cambio en el catálogo, llega a todas las bodegas que tienen el producto). Son idempotentes por `eventId`.
- La bodega ya no crea productos sueltos (se quitó `POST /products` de la API de bodega).

## Solicitudes, notificaciones y DLQ

- Solicitudes (RF-05, RF-06, RF-07), en el catálogo (puerto 8007):
  - `POST /requests` (Admin por Bodega) con `{"request_type": "NEW_PRODUCT" | "UPDATE_PRODUCT", "product_code", "data", "reason"}`.
    NEW_PRODUCT lleva todos los datos del producto; UPDATE_PRODUCT solo lo que cambia. Se valida con las mismas reglas del catálogo.
  - `GET /requests?status=PENDING` (Admin Global ve todas; Admin por Bodega solo las de su bodega).
  - `POST /requests/{id}/approve` y `POST /requests/{id}/reject` con `{"note"}` (solo Admin Global; rechazar exige motivo).
    Aprobar un producto nuevo lo crea en el catálogo y lo agrega a la bodega que lo pidió. Aprobar un cambio llega a todas las bodegas que lo manejan.
- Notificaciones (RF-16), servicio en la central, puerto **8006** (`src/central_notifications`), front central por `/svc/notifications/`:
  - `GET /notifications?unread_only=true`, `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`.
  - Cada notificación va para un rol y, si es de una bodega, solo para esa bodega. Categorías: SOLICITUD, TRANSFERENCIA, DLQ, ALERTA.
  - Las crean: el catálogo (solicitud nueva -> Admin Global; aprobada o rechazada -> Admin por Bodega), el orquestador
    (transferencia FAILED o RESERVATION_RELEASED -> Admin Global y las dos bodegas) y el vigilante de DLQ (mensajes nuevos -> Admin Global).
  - El código para crear notificaciones está en `notify.py` (mismo archivo en catálogo, transferencias, notificaciones y sincronización).
  - El worker de sincronización convierte cada `STOCK_ALERT` de una bodega en una notificación ALERTA para el Admin Global y el de esa bodega.
- DLQ (solo Admin Global): `GET /dlq`, `GET /dlq/{cola}/messages`, `POST /dlq/{cola}/messages/{id}/redrive` y `.../discard` con `{"justification"}`.
  Reprocesar devuelve el mensaje a la cola original (nombre sin `-dlq`); es seguro porque los workers son idempotentes.
  Descartar exige justificación. Cada acción queda en `dlq_actions` con quién la hizo.

## Operación de bodega

- Movimientos a mano (`POST /inventory/movements`, Encargado o Admin por Bodega):
  - `PURCHASE` (compra) y `SALE` (venta) llevan precio (RF-13). Si no se manda `unit_price`, se usa el del catálogo:
    compra = `cost`; venta = `sale_price` o `wholesale_price` según `price_type` (`DETAL` o `MAYORISTA`). `counterparty` = proveedor o cliente.
  - `RECEIPT`, `SHIPMENT` y `RETURN` son entradas, salidas y devoluciones sin precio (ajustes, bajas).
  - Las reservas ya no van por aquí: van por `/reservations`. `TRANSFER_IN`, `RESERVATION_CONFIRM`, `RESERVATION` y `RELEASE` los hace el sistema.
- Lotes (RF-11, RF-12), tabla `lots`:
  - Todo lo que entra va a un lote (`lot_code`, `expiry_date` opcional). Sin lote: `L-AAAAMMDD` (devoluciones `DEV-AAAAMMDD`).
    La cantidad inicial del catálogo entra al lote `INICIAL`; una transferencia entra a un lote con el id de la transferencia.
  - Lo que sale se saca de los lotes que vencen primero (o del lote que se diga). Las ventas nunca sacan de un lote vencido.
  - `movement_lots` guarda de qué lote salió cada movimiento. Siempre: suma de lotes = disponible + reservado.
  - `GET /lots?product_id=&include_empty=` y `GET /lots/{product_id}/{lot_code}` (seguimiento del lote).
- Compras y ventas: `GET /transactions?type=PURCHASE|SALE` con totales.
- Historial de un producto (RF-27): `GET /inventory/{id}/movements` (con lote, precio, usuario y cómo quedó el stock). Toda la bodega: `GET /movements`.
- Reservas, tabla `reservations` (cada reserva es su propio registro):
  - `SALE` (venta): se vence a las 48 horas. `MANUAL`: vencimiento obligatorio, de 1 a 168 horas (7 días), y motivo.
  - `TRANSFER`: la crea la saga en el RESERVE y la cierra en el COMMIT (CONSUMED) o el RELEASE (RELEASED). No la toca una persona.
  - `POST /reservations`, `GET /reservations?status=ACTIVE`, `POST /reservations/{id}/sell` y `POST /reservations/{id}/release`.
  - Estados: ACTIVE, SOLD, RELEASED, EXPIRED, CONSUMED.
- Vigilante de cada bodega (`app/modules/alerts/sweeper.py`, contenedor `vigilante_WH-00X`, cada 60 s):
  - Vence las reservas SALE y MANUAL que pasaron su fecha (RELEASE a nombre de `sistema:vencimiento`) y avisa.
  - Avisa, sin liberar, si una reserva de transferencia lleva más de 24 horas abierta.
  - Avisa si un lote vencido todavía tiene unidades.
  - Incongruencias (RF-15): stock negativo, stock distinto a la suma de sus movimientos, reservado distinto a la suma
    de las reservas activas, disponible + reservado distinto a la suma de los lotes. Se calcula sobre una sola foto de la base.
  - Cada alerta queda en `stock_alerts` (OPEN, RESOLVED, REVIEWED) y sale a la central como `STOCK_ALERT` por `pending_events`.
    No se repite mientras el problema siga. Cuando vuelve a cuadrar se marca resuelta y se avisa.
  - `GET /alerts?status=OPEN` y `POST /alerts/{id}/review` con `{"note"}` (Admin por Bodega o Admin Global).
- Transferencias: si el orquestador no puede mandar una orden (por ejemplo, la bodega destino está registrada pero no tiene cola),
  el estado igual avanza y la orden se reintenta; al vencerse el tiempo, la transferencia falla o se compensa. Así no quedan unidades apartadas para siempre.

## Líder: contratos, estadísticas y reportes

- Todo está en la API de reportes (puerto **8000**, `reports/api`), que el front central usa por `/svc/reports/`.
- Contratos (RF-19, RF-20), `reports/api/contracts.py`:
  - `POST /contracts` (solo el Líder) como formulario multipart: `file` + `title`, `contract_type` (PROVEEDOR, CLIENTE,
    ARRENDAMIENTO, SERVICIO, OTRO), `counterparty`, `start_date`, y opcionales `end_date`, `warehouse_id`, `product_code`, `value`, `notes`.
  - El archivo (PDF, .docx, PNG o JPG; máximo 10 MB; se revisa su contenido, no solo la extensión) va al bucket de reportes,
    carpeta `contratos/CT-0001/`, cifrado con SSE-S3. La ficha queda en la tabla `contracts` de la base central, con su SHA-256.
  - La bodega y el producto deben existir en el catálogo. El mismo archivo no se carga dos veces.
  - `GET /contracts?warehouse_id=&contract_type=&search=&include_archived=`, `GET /contracts/{id}`,
    `GET /contracts/{id}/download` (link de 15 min), `GET /contracts/{id}/verify` (compara la huella del archivo en S3).
  - No se borran: `POST /contracts/{id}/archive` con `{"reason"}` (solo el Líder).
  - Consultan el Líder y el Admin Global. Vigencia: VIGENTE, POR_VENCER (menos de 30 días), VENCIDO, FUTURO, ARCHIVADO.
- Estadísticas (RF-18), `GET /stats?days=30&warehouse_id=` (Líder y Admin Global): totales de compras y ventas, ventas y
  compras por día, por bodega, productos más vendidos, movimientos por tipo, transferencias por estado y alertas.
  Se calculan con `audit_log` (que ahora guarda tipo de movimiento, cantidad, precio y lote), `global_inventory` y `transfers`.

## Seguridad de las comunicaciones

- Detalle en `docs/seguridad.md`.
- Cada máquina tiene una IP elástica fija (`infrastructure/https.tf`), que no cambia al recrear las EC2.
- Terraform crea la CA del proyecto y un certificado por máquina, con su IP fija y el nombre `<máquina>.proyecto-iot.internal`.
  El user_data los deja en `/opt/proyecto/secrets/tls/` (`server.crt`, `server.key`, `ca.crt`).
- Desde internet solo se entra por el 443. Las APIs de la central van por `https://central/svc/<servicio>/` y las de
  una bodega por `https://<bodega>/api/`. Los puertos 8000-8010 solo se abren entre las IPs de las máquinas.
- El front de bodega le habla a la central por `https://<IP central>/svc/identity/` y `/svc/catalog/`, revisando el certificado
  con el nombre `central.proyecto-iot.internal`. El gateway IoT manda a `https://<IP central>/svc/iot/ingest` con `SSL_CERT_FILE`.
- Scripts: `. scripts/https.sh` saca las IPs, guarda la CA en `/tmp/proyecto-ca.crt`, hace que curl revise los certificados
  y deja listas las variables `ID`, `CAT`, `NOT`, `TR`, `RP`, `WH`, `WH2` y `WH3`.
- Permisos IAM de mínimo privilegio: diseñados en `infrastructure/iam/` (el Learner Lab no deja crear roles).

## Pruebas de carga con k6

- Están en `scripts/k6/` y se corren con `bash scripts/k6/correr.sh` (o `bash scripts/k6/correr.sh ventas` para una sola).
- k6 no se instala: corre en Docker (`grafana/k6`) dentro de la central, por HTTPS y confiando en la CA del proyecto.
- Cada prueba deja en `resultados/k6/` un reporte HTML con gráficos (panel web de k6) y un .txt con el resumen.
  - `consultas.js`: hasta 50 usuarios consultando el inventario de wh-001 (RNF-06).
  - `bodegas.js`: carga fuerte en wh-002 y suave en wh-001 al mismo tiempo (RNF-08, RNF-09, RNF-16).
  - `ventas.js`: 50 usuarios vendiendo P-002 a la vez; al final se revisa que el stock cuadre (RNF-05).
  - `reportes.js`: pedir reportes pesados mientras se mide la API de la central (RNF-07).
- Se corren después de las pruebas de `scripts/pruebas/`, porque necesitan los usuarios y productos que esas dejan.
