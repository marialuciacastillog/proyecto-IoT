#!/bin/bash
# Nota: los datos que se mandan con curl van sin tildes. En Git Bash de Windows curl recibe los textos
# con la codificación de Windows (no UTF-8) y la API no puede leer el JSON. Desde el navegador no pasa.
# Prueba de la operación de bodega en AWS: productos repetidos, compras y ventas, lotes, reservas
# que se vencen, alertas de incongruencias e historial de un producto.
# Uso (desde la carpeta del proyecto, después de las otras tres pruebas):
#   bash scripts/pruebas/prueba_operacion.sh
# Tarda unos 5 minutos: espera al vigilante de la bodega, que revisa cada minuto.

IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
W1=$(echo $IPS | grep -o '"wh-001":"[^"]*"' | cut -d'"' -f4)
W2=$(echo $IPS | grep -o '"wh-002":"[^"]*"' | cut -d'"' -f4)
# Direcciones por HTTPS y curl revisando los certificados con la CA del proyecto
. scripts/https.sh
KEY=~/Downloads/labsuser.pem
SSHOPT="-i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
ADMIN=$(bash scripts/token.sh)
J='Content-Type: application/json'; ok=0; fail=0
login() { curl -s -X POST $ID/auth/login -H "$J" -d "{\"username\":\"$1\",\"password\":\"$2\"}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4; }
code() { curl -s -o /tmp/body -w "%{http_code}" "$@"; }
# Como code, pero si la respuesta no trae el texto esperado devuelve "falta-" + el código
codeHas() { local text=$1; shift; local status=$(code "$@"); if grep -q "$text" /tmp/body; then echo $status; else echo "falta-$status"; fi; }
codeHasNot() { local text=$1; shift; local status=$(code "$@"); if grep -q "$text" /tmp/body; then echo "sobra-$status"; else echo $status; fi; }
check() { if echo "$3" | grep -qE "^($2)$"; then echo "OK   $1 -> $3"; ok=$((ok+1)); else echo "FAIL $1 -> esperado $2, salio $3 | $(head -c 200 /tmp/body)"; fail=$((fail+1)); fi; }
# Cambia la base de la bodega 1 por dentro (como si alguien la tocara sin pasar por la API)
sqlWh1() { ssh $SSHOPT ubuntu@$W1 "sudo docker exec db_warehouse_WH-001 psql -U postgres -d warehouse_db -tAc \"$1\""; }
# Espera hasta que la respuesta de una URL contenga un texto (máximo $4 segundos)
waitFor() { local i=0; while [ $i -lt $4 ]; do curl -s "$2" -H "$3" > /tmp/body; grep -q "$1" /tmp/body && return 0; sleep 10; i=$((i+10)); done; return 1; }
A="Authorization: Bearer $ADMIN"
AB="Authorization: Bearer $(login ana.bodega1 Bodega2026)"; E1="Authorization: Bearer $(login juli.encargada1 Encargo2026)"
M2="Authorization: Bearer $(login mario.bodega2 Bodega2026)"; E2="Authorization: Bearer $(login pedro.encargado2 Encargo2026)"
L="Authorization: Bearer $(login malu.lider Lider2026x)"
PID=$(curl -s $WH/products -H "$E1" | grep -o '"id":[0-9]*,"sku":"P-001"' | cut -d: -f2 | cut -d, -f1)
echo "P-001 tiene el id $PID en wh-001"

echo "--- Productos repetidos en el catálogo"
check "crear P-060 Martillo de bola (Herramientas SAS)" "201|409" $(code -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"P-060","name":"Martillo de bola","category":"Herramientas","supplier":"Herramientas SAS","cost":9000,"sale_price":15000,"wholesale_price":13000}')
check "mismo nombre y proveedor con otro código" 409 $(code -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"P-061","name":"MARTILLO  de  bola","category":"Herramientas","supplier":"herramientas sas","cost":9000,"sale_price":15000,"wholesale_price":13000}'); echo "     $(head -c 160 /tmp/body)"
check "mismo nombre con otro proveedor sí se puede" "201|409" $(code -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"P-061","name":"Martillo de bola","category":"Herramientas","supplier":"Stanley","cost":9500,"sale_price":16000,"wholesale_price":14000}')
check "cambiar P-061 para que quede igual a P-060" 409 $(code -X PATCH $CAT/products/P-061 -H "$A" -H "$J" -d '{"supplier":"Herramientas SAS"}')
check "ana pide como nuevo un producto repetido" 409 $(code -X POST $CAT/requests -H "$AB" -H "$J" -d '{"request_type":"NEW_PRODUCT","product_code":"P-062","data":{"name":"Martillo de Bola","category":"Herramientas","supplier":"Herramientas SAS","cost":1,"sale_price":2,"wholesale_price":1},"reason":"Prueba repetido"}')
check "ana pide uno con el mismo nombre y otro proveedor" 201 $(code -X POST $CAT/requests -H "$AB" -H "$J" -d '{"request_type":"NEW_PRODUCT","product_code":"P-064","data":{"name":"Martillo de bola","category":"Herramientas","supplier":"Truper","cost":8000,"sale_price":14000,"wholesale_price":12000},"reason":"Otra marca mas barata"}')
SOL=$(grep -o '"id":[0-9]*' /tmp/body | head -1 | cut -d: -f2)
check "el Admin Global ve el aviso de nombre parecido" 200 $(code "$CAT/requests?status=PENDING" -H "$A"); echo "     $(grep -o '"similar":\[[^]]*\]' /tmp/body | head -1)"
curl -s -o /dev/null -X POST $CAT/requests/$SOL/reject -H "$A" -H "$J" -d '{"note":"Prueba del aviso de repetidos"}'

echo "--- Compras y ventas (RF-13)"
check "el Encargado compra 30 al lote L-PRUEBA-A" 201 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"PURCHASE\",\"quantity\":30,\"lot_code\":\"L-PRUEBA-A\",\"expiry_date\":\"2027-12-31\",\"reference\":\"FAC-100\"}"); echo "     $(grep -o '"unit_price":"[^"]*","total":"[^"]*"' /tmp/body) $(grep -o '"counterparty":"[^"]*"' /tmp/body)"
check "el mismo lote con otra fecha de vencimiento" 400 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"PURCHASE\",\"quantity\":1,\"lot_code\":\"L-PRUEBA-A\",\"expiry_date\":\"2028-01-31\"}")
check "ana compra 10 a un lote que ya venció" 201 $(code -X POST $WH/inventory/movements -H "$AB" -H "$J" -d "{\"product_id\":$PID,\"type\":\"PURCHASE\",\"quantity\":10,\"lot_code\":\"L-PRUEBA-VIEJO\",\"expiry_date\":\"2020-01-31\",\"unit_price\":90,\"counterparty\":\"Aceros SAS\"}")
check "venta de 5 al detal (precio del catálogo)" 201 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"SALE\",\"quantity\":5,\"reference\":\"VENTA-1\"}"); echo "     $(grep -o '"total":"[^"]*","price_type":"[^"]*"' /tmp/body) lote: $(grep -o '"lot_code":"[^"]*"' /tmp/body)"
check "venta mayorista de 2 a 125 c/u" 201 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"SALE\",\"quantity\":2,\"price_type\":\"MAYORISTA\",\"unit_price\":125,\"counterparty\":\"Ferreteria Lopez\"}"); echo "     $(grep -o '"total":"[^"]*"' /tmp/body)"
check "vender del lote vencido" 400 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"SALE\",\"quantity\":1,\"lot_code\":\"L-PRUEBA-VIEJO\"}"); echo "     $(head -c 150 /tmp/body)"
check "vender más de lo que hay" 400 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"SALE\",\"quantity\":99999}")
check "tipo de precio inválido" 400 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"SALE\",\"quantity\":1,\"price_type\":\"REGALO\"}")
check "el Líder intenta comprar" 403 $(code -X POST $WH/inventory/movements -H "$L" -H "$J" -d "{\"product_id\":$PID,\"type\":\"PURCHASE\",\"quantity\":1}")
check "el Encargado de wh-002 vende en wh-001" 403 $(code -X POST $WH/inventory/movements -H "$E2" -H "$J" -d "{\"product_id\":$PID,\"type\":\"SALE\",\"quantity\":1}")
check "reservar por movimientos (ahora va por /reservations)" 400 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"RESERVATION\",\"quantity\":1}")
check "histórico de compras y ventas" 200 $(code $WH/transactions -H "$L"); echo "     $(grep -o '"summary":{[^}]*},"sales":{[^}]*}' /tmp/body)"

echo "--- Lotes (RF-11, RF-12)"
check "lotes de P-001" 200 $(code "$WH/lots?product_id=$PID" -H "$E1"); echo "     $(grep -o '"lot_code":"[^"]*","quantity":[0-9]*' /tmp/body | tr '\n' ' ')"
check "seguimiento del lote L-PRUEBA-A" 200 $(code $WH/lots/$PID/L-PRUEBA-A -H "$E1"); echo "     movimientos del lote: $(grep -o '"lot_quantity"' /tmp/body | wc -l)"
check "código de lote inválido" 400 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"type\":\"RECEIPT\",\"quantity\":1,\"lot_code\":\"a\"}")

echo "--- Reservas con vencimiento"
check "reserva de venta (48 horas)" 201 $(code -X POST $WH/reservations -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"quantity\":3,\"kind\":\"SALE\",\"counterparty\":\"Cliente Prueba\"}"); echo "     $(grep -o '"id":"RES-[^"]*"' /tmp/body) vence: $(grep -o '"expires_at":"[^"]*"' /tmp/body)"
RES1=$(grep -o '"id":"RES-[^"]*"' /tmp/body | cut -d'"' -f4)
check "reserva manual sin decir cuánto dura" 400 $(code -X POST $WH/reservations -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"quantity\":1,\"kind\":\"MANUAL\",\"reason\":\"Muestra para cliente\"}")
check "reserva manual de más de 7 días" 400 $(code -X POST $WH/reservations -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"quantity\":1,\"kind\":\"MANUAL\",\"hours\":200,\"reason\":\"Muestra para cliente\"}")
check "reserva manual de 2 horas" 201 $(code -X POST $WH/reservations -H "$AB" -H "$J" -d "{\"product_id\":$PID,\"quantity\":4,\"kind\":\"MANUAL\",\"hours\":2,\"reason\":\"Muestra para cliente\"}")
RES2=$(grep -o '"id":"RES-[^"]*"' /tmp/body | cut -d'"' -f4)
check "reservar más de lo disponible" 400 $(code -X POST $WH/reservations -H "$E1" -H "$J" -d "{\"product_id\":$PID,\"quantity\":99999,\"kind\":\"SALE\"}")
check "el Líder intenta liberar una reserva" 403 $(code -X POST $WH/reservations/$RES1/release -H "$L" -H "$J" -d '{}')
check "vender la reserva de venta" 200 $(code -X POST $WH/reservations/$RES1/sell -H "$E1" -H "$J" -d '{"price_type":"DETAL"}'); echo "     $(grep -o '"status":"[^"]*"' /tmp/body)"
check "venderla otra vez" 409 $(code -X POST $WH/reservations/$RES1/sell -H "$E1" -H "$J" -d '{}')

echo "--- El vigilante: reserva vencida e incongruencia (RF-15)"
echo "     se adelanta el vencimiento de $RES2 y se cambia el stock de P-001 directo en la base..."
sqlWh1 "UPDATE reservations SET expires_at = now() - interval '1 minute' WHERE id = '$RES2'" > /dev/null
sqlWh1 "UPDATE inventory SET quantity = quantity + 7 WHERE product_id = $PID" > /dev/null
echo "     esperando al vigilante (revisa cada minuto)..."
waitFor '"alert_type":"INCONGRUENCIA"' "$WH/alerts?status=OPEN" "$AB" 150
check "el vigilante detecta la incongruencia" 200 $(codeHas INCONGRUENCIA "$WH/alerts?status=OPEN" -H "$AB"); echo "     $(grep -o '"message":"El stock de P-001[^"]*"' /tmp/body | head -1)"
ALERT=$(grep -o '"id":[0-9]*,"alert_type":"INCONGRUENCIA"' /tmp/body | head -1 | cut -d: -f2 | cut -d, -f1)
waitFor '"status":"EXPIRED"' "$WH/reservations" "$AB" 60
check "la reserva manual se venció sola" 200 $(codeHas "\"id\":\"$RES2\"[^}]*\"status\":\"EXPIRED\"" $WH/reservations -H "$AB"); echo "     $(grep -o "\"id\":\"$RES2\"[^}]*\"status\":\"[^\"]*\"" /tmp/body | grep -o '"status":"[^"]*"')"
check "el Encargado intenta revisar la alerta" 403 $(code -X POST $WH/alerts/$ALERT/review -H "$E1" -H "$J" -d '{"note":"Revisado por mi"}')
check "revisar la alerta sin nota" 400 $(code -X POST $WH/alerts/$ALERT/review -H "$AB" -H "$J" -d '{"note":""}')
check "ana revisa la alerta con nota" 200 $(code -X POST $WH/alerts/$ALERT/review -H "$AB" -H "$J" -d '{"note":"Se esta contando a mano"}')
echo "     esperando que la alerta llegue a la central..."
waitFor 'Incongruencia en P-001' "$NOT/notifications" "$A" 60
check "el Admin Global recibió la alerta" 200 $(codeHas '"category":"ALERTA"' "$NOT/notifications" -H "$A"); echo "     $(grep -o '"title":"Incongruencia en P-001[^"]*"' /tmp/body | head -1)"
check "ana también la recibió" 200 $(code "$NOT/notifications" -H "$AB"); echo "     $(grep -o '"title":"Reserva [^"]*vencida[^"]*"' /tmp/body | head -1)"
check "mario (wh-002) no ve las alertas de wh-001" 200 $(codeHasNot 'Incongruencia en P-001 (wh-001)' "$NOT/notifications" -H "$M2"); echo "     alertas de wh-001 que ve mario: $(grep -o 'Incongruencia en P-001 (wh-001)' /tmp/body | wc -l)"

echo "--- Se arregla: el stock vuelve a cuadrar y el lote vencido se saca"
sqlWh1 "UPDATE inventory SET quantity = quantity - 7 WHERE product_id = $PID" > /dev/null
check "baja de las 10 unidades del lote vencido" 201 $(code -X POST $WH/inventory/movements -H "$AB" -H "$J" -d "{\"product_id\":$PID,\"type\":\"SHIPMENT\",\"quantity\":10,\"lot_code\":\"L-PRUEBA-VIEJO\",\"reference\":\"Baja por vencimiento\"}")

echo "--- Transferencia: la reserva la maneja la saga"
check "mario agrega P-001 a wh-002" "202|409" $(code -X POST $CAT/warehouses/wh-002/products -H "$M2" -H "$J" -d '{"product_code":"P-001","initial_quantity":0}')
sleep 10
check "ana transfiere 2 de P-001 a wh-002" 202 $(code -X POST $TR/transfers -H "$AB" -H "$J" -d '{"source_warehouse":"wh-001","destination_warehouse":"wh-002","product_id":"P-001","quantity":2,"justification":"Prueba de reserva de la saga"}')
T=$(grep -o 'TR-[A-Z0-9]*' /tmp/body | head -1); echo "     $T"
echo "     esperando que la saga termine y que el vigilante vea que todo cuadra..."
waitFor "\"status\":\"CONSUMED\",\"reason\":\"Transferencia $T\"" "$WH/reservations" "$AB" 120
check "la reserva de la transferencia quedó cerrada por la saga" 200 $(codeHas "\"status\":\"CONSUMED\",\"reason\":\"Transferencia $T\"" $WH/reservations -H "$AB"); echo "     $(grep -o "\"kind\":\"TRANSFER\",\"status\":\"[A-Z]*\",\"reason\":\"Transferencia $T\"" /tmp/body | grep -o '"status":"[^"]*"')"
check "wh-002 recibió las unidades en un lote con el nombre de la transferencia" 200 $(codeHas "\"lot_code\":\"$T\"" "$WH2/lots" -H "$E2"); echo "     $(grep -o "\"lot_code\":\"$T\",\"quantity\":[0-9]*" /tmp/body)"
waitFor '"status":"RESOLVED"' "$WH/alerts" "$AB" 90
sleep 10
check "no quedan incongruencias abiertas" 200 $(codeHasNot INCONGRUENCIA "$WH/alerts?status=OPEN" -H "$AB"); echo "     abiertas: $(grep -o '"alert_type":"[A-Z_]*"' /tmp/body | sort | uniq -c | tr '\n' ' ')"
check "la alerta revisada quedó con la fecha en que se resolvió" 200 $(code "$WH/alerts" -H "$AB"); echo "     $(grep -o "\"id\":$ALERT,[^}]*" /tmp/body | grep -o '"status":"[^"]*"\|"resolved_at":"[^"]*"' | tr '\n' ' ')"

echo "--- Historial completo de P-001 (RF-27)"
check "historial de P-001" 200 $(code $WH/inventory/$PID/movements -H "$L"); echo "     $(grep -o '"type":"[A-Z_]*"' /tmp/body | sort | uniq -c | tr '\n' ' ')"
echo "     quedó en: $(grep -o '"available_after":[0-9-]*,"reserved_after":[0-9-]*' /tmp/body | tail -1)"
echo "Resultado: $ok OK, $fail FAIL"
