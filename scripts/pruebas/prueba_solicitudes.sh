#!/bin/bash
# Nota: los datos que se mandan con curl van sin tildes. En Git Bash de Windows curl recibe los textos
# con la codificación de Windows (no UTF-8) y la API no puede leer el JSON. Desde el navegador no pasa.
# Prueba de solicitudes de productos, notificaciones y DLQ en AWS.
# Uso (desde la carpeta del proyecto, después de prueba_roles.sh y prueba_catalogo.sh):
#   bash scripts/pruebas/prueba_solicitudes.sh

IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
W1=$(echo $IPS | grep -o '"wh-001":"[^"]*"' | cut -d'"' -f4)
SQ=$(terraform -chdir=infrastructure output -raw sync_queue_url)
ID=$C:8008; CAT=$C:8007; WH=$W1:8001; NOT=$C:8006; TR=$C:8010
ADMIN=$(bash scripts/token.sh)

# Dos mensajes para la DLQ: uno roto (el worker lo manda directo a la DLQ) y uno valido que "fallo antes"
# (se mete directo en la DLQ, como si hubiera fallado cuando la base estaba caida). Es de una fecha vieja,
# asi que al reprocesarlo el worker lo marca VIEJO y no cambia ningun inventario.
aws sqs send-message --queue-url $SQ --message-body "esto no es json" > /dev/null
aws sqs send-message --queue-url $SQ-dlq --message-body '{"eventId":"EV-REPROCESO-'$(date +%s)'","eventType":"INVENTORY_UPDATED","warehouseId":"wh-001","productId":"P-001","quantity":1,"createdAt":"2020-01-01T00:00:00"}' > /dev/null
J='Content-Type: application/json'; ok=0; fail=0
login() { curl -s -X POST $ID/auth/login -H "$J" -d "{\"username\":\"$1\",\"password\":\"$2\"}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4; }
code() { curl -s -o /tmp/body -w "%{http_code}" "$@"; }
check() { if echo "$3" | grep -qE "^($2)$"; then echo "OK   $1 -> $3"; ok=$((ok+1)); else echo "FAIL $1 -> esperado $2, salio $3 | $(head -c 200 /tmp/body)"; fail=$((fail+1)); fi; }
A="Authorization: Bearer $ADMIN"
curl -s -o /dev/null -X POST $ID/users -H "$A" -H "$J" -d '{"username":"mario.bodega2","password":"Bodega2026","full_name":"Mario Admin WH2","role":"ADMIN_BODEGA","warehouse_id":"wh-002"}'
AB="Authorization: Bearer $(login ana.bodega1 Bodega2026)"; E1="Authorization: Bearer $(login juli.encargada1 Encargo2026)"
M2="Authorization: Bearer $(login mario.bodega2 Bodega2026)"; L="Authorization: Bearer $(login malu.lider Lider2026x)"
curl -s -o /dev/null -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"P-001","name":"Tornillo 1/4","category":"Ferreteria","supplier":"Aceros SAS","cost":100,"sale_price":150,"wholesale_price":130,"initial_quantity":0}'
SUF=$(date +%s | tail -c 4)
NEW="P-1$SUF"
echo "--- Solicitudes (RF-05, RF-06)"
check "admin bodega pide producto nuevo $NEW" 201 $(code -X POST $CAT/requests -H "$AB" -H "$J" -d "{\"request_type\":\"NEW_PRODUCT\",\"product_code\":\"$NEW\",\"data\":{\"name\":\"Martillo\",\"category\":\"Herramientas\",\"supplier\":\"Herramientas SAS\",\"unit\":\"UNIDADES\",\"cost\":9000,\"sale_price\":15000,\"wholesale_price\":13000,\"initial_quantity\":12},\"reason\":\"Los clientes lo piden cada semana\"}")
SOL1=$(grep -o '"id":[0-9]*' /tmp/body | head -1 | cut -d: -f2); echo "     $(grep -o '"label":"[^"]*"' /tmp/body)"
check "pedir el mismo producto otra vez (pendiente)" 409 $(code -X POST $CAT/requests -H "$AB" -H "$J" -d "{\"request_type\":\"NEW_PRODUCT\",\"product_code\":\"$NEW\",\"data\":{\"name\":\"Martillo\",\"category\":\"Herramientas\",\"supplier\":\"Herramientas SAS\",\"cost\":9000,\"sale_price\":15000,\"wholesale_price\":13000},\"reason\":\"Otra vez\"}")
check "pedir como nuevo un producto que ya existe" 409 $(code -X POST $CAT/requests -H "$AB" -H "$J" -d '{"request_type":"NEW_PRODUCT","product_code":"P-001","data":{"name":"Otro","category":"Varios","supplier":"Otro SAS","cost":1,"sale_price":2,"wholesale_price":1},"reason":"Prueba repetido"}')
check "solicitud con mayorista mayor que venta" 400 $(code -X POST $CAT/requests -H "$AB" -H "$J" -d '{"request_type":"NEW_PRODUCT","product_code":"P-777","data":{"name":"Taladro","category":"Herramientas","supplier":"Herramientas SAS","cost":1,"sale_price":2,"wholesale_price":5},"reason":"Prueba precios"}')
check "el Encargado intenta pedir" 403 $(code -X POST $CAT/requests -H "$E1" -H "$J" -d '{"request_type":"UPDATE_PRODUCT","product_code":"P-001","data":{"sale_price":200},"reason":"Prueba encargado"}')
check "admin bodega pide cambio de precio de P-001" 201 $(code -X POST $CAT/requests -H "$AB" -H "$J" -d '{"request_type":"UPDATE_PRODUCT","product_code":"P-001","data":{"sale_price":200,"wholesale_price":170},"reason":"El proveedor subio el precio"}')
SOL2=$(grep -o '"id":[0-9]*' /tmp/body | head -1 | cut -d: -f2)
check "cambio sin ningún dato" 400 $(code -X POST $CAT/requests -H "$M2" -H "$J" -d '{"request_type":"UPDATE_PRODUCT","product_code":"P-001","data":{},"reason":"Sin cambios"}')
check "admin bodega wh-002 no ve las solicitudes de wh-001" 200 $(code $CAT/requests -H "$M2"); echo "     solicitudes que ve mario: $(grep -o '"label"' /tmp/body | wc -l)"
check "el Líder intenta ver solicitudes" 403 $(code $CAT/requests -H "$L")
echo "--- Notificaciones al Admin Global"
check "el Admin Global tiene notificaciones" 200 $(code "$NOT/notifications?unread_only=true" -H "$A"); echo "     $(grep -o '"title":"[^"]*"' /tmp/body | head -4 | tr '\n' ' ')"
echo "--- Decisiones (RF-07)"
check "admin bodega intenta aprobar" 403 $(code -X POST $CAT/requests/$SOL1/approve -H "$AB" -H "$J" -d '{"note":""}')
check "Admin Global aprueba el producto nuevo" 200 $(code -X POST $CAT/requests/$SOL1/approve -H "$A" -H "$J" -d '{"note":"Aprobado para la temporada"}'); echo "     $(grep -o '"message":"[^"]*"' /tmp/body)"
check "aprobar otra vez" 409 $(code -X POST $CAT/requests/$SOL1/approve -H "$A" -H "$J" -d '{"note":""}')
check "rechazar sin motivo" 400 $(code -X POST $CAT/requests/$SOL2/reject -H "$A" -H "$J" -d '{"note":""}')
check "rechazar el cambio de precio con motivo" 200 $(code -X POST $CAT/requests/$SOL2/reject -H "$A" -H "$J" -d '{"note":"Primero hay que negociar con el proveedor"}')
check "el producto nuevo quedó en el catálogo y en wh-001" 200 $(code $CAT/products/$NEW -H "$A")
echo "     esperando que llegue a la bodega..."; sleep 10
check "la bodega ya tiene el producto nuevo" 200 $(code $WH/products -H "$E1"); echo "     $(grep -o "\"sku\":\"$NEW\"" /tmp/body)"
echo "--- Notificaciones al Admin por Bodega"
check "ana ve sus 2 notificaciones (aprobada y rechazada)" 200 $(code "$NOT/notifications?unread_only=true" -H "$AB"); echo "     $(grep -o '"title":"[^"]*"' /tmp/body | tr '\n' ' ')"
NID=$(grep -o '"id":[0-9]*' /tmp/body | head -1 | cut -d: -f2)
check "mario (wh-002) no ve las de wh-001" 200 $(code "$NOT/notifications" -H "$M2"); echo "     mario tiene: $(grep -o '"unread":[0-9]*' /tmp/body)"
check "ana marca una como leída" 200 $(code -X POST $NOT/notifications/$NID/read -H "$AB")
check "mario intenta marcar una de ana" 404 $(code -X POST $NOT/notifications/$NID/read -H "$M2")
check "ana: contador sin leer" 200 $(code $NOT/notifications/unread-count -H "$AB"); echo "     $(cat /tmp/body)"
check "ana marca todas" 200 $(code -X POST $NOT/notifications/read-all -H "$AB")
echo "--- DLQ (solo Admin Global)"
check "admin bodega intenta ver las DLQ" 403 $(code $NOT/dlq -H "$AB")
check "Admin Global ve las DLQ" 200 $(code $NOT/dlq -H "$A"); echo "     $(grep -o '{"queue":"sync-queue-dlq"[^}]*}' /tmp/body)"
check "ver los mensajes de sync-queue-dlq" 200 $(code $NOT/dlq/sync-queue-dlq/messages -H "$A")
BROKEN=$(grep -o '"message_id":"[^"]*","body":"esto no es json"' /tmp/body | head -1 | cut -d'"' -f4)
VALID=$(grep -o '"message_id":"[^"]*","body":"{\\"eventId\\":\\"EV-REPROCESO-[0-9]*' /tmp/body | head -1 | cut -d'"' -f4)
echo "     mensaje roto: ${BROKEN:-no encontrado} | mensaje valido: ${VALID:-no encontrado}"
check "descartar sin justificación" 400 $(code -X POST $NOT/dlq/sync-queue-dlq/messages/$BROKEN/discard -H "$A" -H "$J" -d '{"justification":""}')
check "descartar el mensaje roto con justificación" 200 $(code -X POST $NOT/dlq/sync-queue-dlq/messages/$BROKEN/discard -H "$A" -H "$J" -d '{"justification":"JSON roto, no se puede arreglar"}')
check "reprocesar el mensaje válido" 200 $(code -X POST $NOT/dlq/sync-queue-dlq/messages/$VALID/redrive -H "$A" -H "$J" -d '{"justification":"La base ya volvio"}'); echo "     $(grep -o '"message":"[^"]*"' /tmp/body)"
sleep 10
check "los dos ya no están en la DLQ" 200 $(code $NOT/dlq/sync-queue-dlq/messages -H "$A")
QUEDAN=$(grep -c "$BROKEN\|$VALID" /tmp/body); echo "     siguen en la DLQ: $QUEDAN"; [ "$QUEDAN" = "0" ] && ok=$((ok+1)) || { echo "FAIL siguen en la DLQ"; fail=$((fail+1)); }
check "acciones registradas" 200 $(code $NOT/dlq/actions -H "$A"); echo "     $(grep -o '"action":"[A-Z]*"' /tmp/body | head -2 | tr '\n' ' ')"
echo "     esperando la notificación del vigilante de DLQ (revisa cada minuto)..."
for i in 1 2 3 4 5 6 7; do curl -s "$NOT/notifications" -H "$A" | grep -q '"category":"DLQ"' && break; sleep 10; done
check "el Admin Global recibió la notificación de la DLQ" 200 $(code "$NOT/notifications" -H "$A"); echo "     $(grep -o '"title":"Mensajes nuevos en[^"]*"' /tmp/body | head -1)"
echo "--- Transferencia fallida"
check "transferencia de un producto que no existe" 202 $(code -X POST $TR/transfers -H "$AB" -H "$J" -d '{"source_warehouse":"wh-001","destination_warehouse":"wh-002","product_id":"P-999","quantity":1}')
T=$(grep -o 'TR-[A-Z0-9]*' /tmp/body); echo "     $T, esperando que la saga termine..."; sleep 35
check "ana recibió la notificación de la transferencia fallida" 200 $(code "$NOT/notifications" -H "$AB"); echo "     $(grep -o "\"title\":\"Transferencia $T[^\"]*\"" /tmp/body)"
check "mario (destino wh-002) también" 200 $(code "$NOT/notifications" -H "$M2"); echo "     $(grep -o "\"title\":\"Transferencia $T[^\"]*\"" /tmp/body)"
echo; echo "Resultado: $ok OK, $fail FAIL"
