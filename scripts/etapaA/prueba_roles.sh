#!/bin/bash
# Etapa A - Prueba de usuarios, login y permisos por rol en AWS.
# Crea 4 usuarios (uno por rol) y prueba lo que cada uno puede y no puede hacer.
# Uso (desde la carpeta del proyecto, una vez por despliegue):
#   bash scripts/etapaA/prueba_roles.sh | tee evidencias/etapaA/prueba_roles.txt

IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
W1=$(echo $IPS | grep -o '"wh-001":"[^"]*"' | cut -d'"' -f4)
ID=$C:8008; WH=$W1:8001; TR=$C:8010; RP=$C:8000
J='Content-Type: application/json'
ok=0; fail=0
login() { curl -s -X POST $ID/auth/login -H "$J" -d "{\"username\":\"$1\",\"password\":\"$2\"}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4; }
code() { curl -s -o /tmp/body -w "%{http_code}" "$@"; }
check() {
  if echo "$3" | grep -qE "^($2)$"; then echo "OK   $1 -> $3"; ok=$((ok+1))
  else echo "FAIL $1 -> esperado $2, salió $3 | $(head -c 150 /tmp/body)"; fail=$((fail+1)); fi
}

ADMIN=$(bash scripts/token.sh)
A="Authorization: Bearer $ADMIN"
check "login del Administrador Global" 200 $(code $ID/auth/me -H "$A")
check "login con contraseña mala" 401 $(code -X POST $ID/auth/login -H "$J" -d '{"username":"admin","password":"mala12345"}')
echo "--- El Administrador Global crea un usuario por rol"
check "crear admin bodega wh-001" "201|409" $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"ana.bodega1","password":"Bodega2026","full_name":"Ana Admin WH1","role":"ADMIN_BODEGA","warehouse_id":"wh-001"}')
check "crear encargado wh-001" "201|409" $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"juli.encargada1","password":"Encargo2026","full_name":"Juli Encargada WH1","role":"ENCARGADO","warehouse_id":"wh-001"}')
check "crear encargado wh-002" "201|409" $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"pedro.encargado2","password":"Encargo2026","full_name":"Pedro Encargado WH2","role":"ENCARGADO","warehouse_id":"wh-002"}')
check "crear lider" "201|409" $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"malu.lider","password":"Lider2026x","full_name":"Malu Lider","role":"LIDER"}')
check "encargado sin bodega" 400 $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"x.malo","password":"Encargo2026","full_name":"Sin bodega","role":"ENCARGADO"}')
check "clave debil" 400 $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"x.debil","password":"123","full_name":"Debil","role":"LIDER"}')
check "usuario repetido" 409 $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"malu.lider","password":"Lider2026x","full_name":"Otra","role":"LIDER"}')
AB="Authorization: Bearer $(login ana.bodega1 Bodega2026)"
E1="Authorization: Bearer $(login juli.encargada1 Encargo2026)"
E2="Authorization: Bearer $(login pedro.encargado2 Encargo2026)"
L="Authorization: Bearer $(login malu.lider Lider2026x)"
check "el Líder intenta crear usuarios" 403 $(code -X POST $ID/users -H "$L" -H "$J" -d '{"username":"y.y","password":"Lider2026x","full_name":"YYY","role":"LIDER"}')
check "el Administrador Global lista usuarios" 200 $(code $ID/users -H "$A")
echo "--- Bodega wh-001"
check "sin token" 401 $(code $WH/products)
check "token inventado" 401 $(code $WH/products -H "Authorization: Bearer token.falso.123")
check "el Encargado crea un producto en el catálogo" 403 $(code -X POST $C:8007/products -H "$E1" -H "$J" -d '{"product_code":"P-009","name":"Tornillo","category":"Ferretería","supplier":"Aceros SAS","cost":1,"sale_price":2,"wholesale_price":1}')
# Etapa B: el producto se crea en el catálogo de la central y el Admin por Bodega lo agrega a su bodega
CAT=$C:8007
check "el Admin Global crea P-001 en el catálogo" "201|409" $(code -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"P-001","name":"Tornillo","category":"Ferretería","supplier":"Aceros SAS","cost":100,"sale_price":150,"wholesale_price":130,"initial_quantity":0}')
check "el Encargado intenta agregar P-001 a la bodega" 403 $(code -X POST $CAT/warehouses/wh-001/products -H "$E1" -H "$J" -d '{"product_code":"P-001"}')
check "el Admin por Bodega agrega P-001 a su bodega" "202|409" $(code -X POST $CAT/warehouses/wh-001/products -H "$AB" -H "$J" -d '{"product_code":"P-001","initial_quantity":0}')
echo "     esperando a que el producto llegue a la bodega..."; sleep 12
check "el Encargado registra una entrada" 201 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d '{"product_id":1,"type":"RECEIPT","quantity":20}')
echo "     quién la hizo: $(grep -o '"created_by":"[^"]*"' /tmp/body)"
check "el Encargado registra TRANSFER_IN a mano" 400 $(code -X POST $WH/inventory/movements -H "$E1" -H "$J" -d '{"product_id":1,"type":"TRANSFER_IN","quantity":5}')
check "el Encargado de wh-002 registra en wh-001" 403 $(code -X POST $WH/inventory/movements -H "$E2" -H "$J" -d '{"product_id":1,"type":"RECEIPT","quantity":5}')
check "el Encargado de wh-002 consulta wh-001" 403 $(code $WH/inventory/1 -H "$E2")
check "el Líder registra un movimiento" 403 $(code -X POST $WH/inventory/movements -H "$L" -H "$J" -d '{"product_id":1,"type":"RECEIPT","quantity":5}')
check "el Líder consulta inventario" 200 $(code $WH/inventory/1 -H "$L")
check "health sin token" 200 $(code $WH/health)
echo "--- Reportes"
check "el Encargado pide un reporte" 403 $(code -X POST $RP/reports -H "$E1" -H "$J" -d '{"report_type":"global_inventory","format":"csv"}')
check "el Líder pide un reporte" 202 $(code -X POST $RP/reports -H "$L" -H "$J" -d '{"report_type":"global_inventory","format":"csv"}')
check "el Admin por Bodega ve el consolidado" 403 $(code $RP/global/inventory/P-001 -H "$AB")
echo "--- Transferencias"
check "Admin por Bodega wh-001 pide desde wh-002" 403 $(code -X POST $TR/transfers -H "$AB" -H "$J" -d '{"source_warehouse":"wh-002","destination_warehouse":"wh-001","product_id":"P-001","quantity":2}')
check "el Encargado pide una transferencia" 403 $(code -X POST $TR/transfers -H "$E1" -H "$J" -d '{"source_warehouse":"wh-001","destination_warehouse":"wh-002","product_id":"P-001","quantity":2}')
check "Admin por Bodega wh-001 pide hacia wh-002" 202 $(code -X POST $TR/transfers -H "$AB" -H "$J" -d '{"source_warehouse":"wh-001","destination_warehouse":"wh-002","product_id":"P-001","quantity":2,"justification":"Falta stock en wh-002 para pedido grande"}')
T1=$(grep -o 'TR-[A-Z0-9]*' /tmp/body)
check "Admin Global pide wh-001 hacia wh-003" 202 $(code -X POST $TR/transfers -H "$A" -H "$J" -d '{"source_warehouse":"wh-001","destination_warehouse":"wh-003","product_id":"P-001","quantity":1}')
T2=$(grep -o 'TR-[A-Z0-9]*' /tmp/body)
check "Encargado wh-002 ve $T1 (wh-002 es destino)" 200 $(code $TR/transfers/$T1 -H "$E2")
echo "     $(grep -o '"requested_by":"[^"]*","justification":"[^"]*"' /tmp/body)"
check "Encargado wh-002 ve $T2 (no es de su bodega)" 404 $(code $TR/transfers/$T2 -H "$E2")
echo "--- Bloqueo y desactivación"
# Se usa un usuario aparte para no dejar bloqueado a nadie de las pruebas anteriores
curl -s -o /dev/null -X POST $ID/users -H "$A" -H "$J" -d '{"username":"prueba.bloqueo","password":"Bloqueo2026","full_name":"Usuario de prueba","role":"LIDER"}'
for i in 1 2 3 4 5; do curl -s -o /dev/null -X POST $ID/auth/login -H "$J" -d '{"username":"prueba.bloqueo","password":"Mala2026xx"}'; done
check "contraseña correcta tras 5 intentos fallidos" 401 $(code -X POST $ID/auth/login -H "$J" -d '{"username":"prueba.bloqueo","password":"Bloqueo2026"}')
echo "     $(cat /tmp/body)"
echo
echo "Resultado: $ok OK, $fail FAIL"
