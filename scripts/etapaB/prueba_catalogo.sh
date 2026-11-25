#!/bin/bash
# Nota: los datos que se mandan con curl van sin tildes. En Git Bash de Windows curl recibe los textos
# con la codificación de Windows (no UTF-8) y la API no puede leer el JSON. Desde el navegador no pasa.
# Etapa B - Prueba de bodegas y catálogo global en AWS.
# Uso (desde la carpeta del proyecto, después de bash scripts/etapaA/prueba_roles.sh):
#   bash scripts/etapaB/prueba_catalogo.sh | tee evidencias/etapaB/prueba_catalogo.txt

IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
W1=$(echo $IPS | grep -o '"wh-001":"[^"]*"' | cut -d'"' -f4)
ID=$C:8008; CAT=$C:8007; WH=$W1:8001; TR=$C:8010
J='Content-Type: application/json'; ok=0; fail=0
login() { curl -s -X POST $ID/auth/login -H "$J" -d "{\"username\":\"$1\",\"password\":\"$2\"}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4; }
code() { curl -s -o /tmp/body -w "%{http_code}" "$@"; }
check() { if echo "$3" | grep -qE "^($2)$"; then echo "OK   $1 -> $3"; ok=$((ok+1)); else echo "FAIL $1 -> esperado $2, salió $3 | $(head -c 200 /tmp/body)"; fail=$((fail+1)); fi; }
A="Authorization: Bearer $(bash scripts/token.sh)"
curl -s -o /dev/null -X POST $ID/users -H "$A" -H "$J" -d '{"username":"ana.bodega1","password":"Bodega2026","full_name":"Ana Admin WH1","role":"ADMIN_BODEGA","warehouse_id":"wh-001"}'
curl -s -o /dev/null -X POST $ID/users -H "$A" -H "$J" -d '{"username":"juli.encargada1","password":"Encargo2026","full_name":"Juli Encargada WH1","role":"ENCARGADO","warehouse_id":"wh-001"}'
AB="Authorization: Bearer $(login ana.bodega1 Bodega2026)"; E1="Authorization: Bearer $(login juli.encargada1 Encargo2026)"
echo "--- Bodegas (RF-02, RF-03)"
check "las 3 bodegas iniciales" 200 $(code $CAT/warehouses -H "$E1"); echo "     $(grep -o '"warehouse_id":"[^"]*"' /tmp/body | tr '\n' ' ')"
check "NIT con dígito de verificación malo" 400 $(code -X POST $CAT/warehouses -H "$A" -H "$J" -d '{"warehouse_id":"wh-004","nit":"900456789-0","name":"Bodega Oriente","location":"Cali - Oriente","manager_name":"Laura Gomez"}')
check "crear bodega wh-004" "201|409" $(code -X POST $CAT/warehouses -H "$A" -H "$J" -d '{"warehouse_id":"wh-004","nit":"900.456.789-4","name":"Bodega Oriente","location":"Cali - Oriente","description":"Nueva bodega","manager_name":"Laura Gomez"}')
echo "     NIT guardado: $(grep -o '"nit":"[^"]*"' /tmp/body)"
check "NIT repetido" 409 $(code -X POST $CAT/warehouses -H "$A" -H "$J" -d '{"warehouse_id":"wh-005","nit":"900456789-4","name":"Otra","location":"Cali","manager_name":"Alguien"}')
check "admin bodega crea bodega" 403 $(code -X POST $CAT/warehouses -H "$AB" -H "$J" -d '{"warehouse_id":"wh-006","nit":"900123456-8","name":"X","location":"Cali","manager_name":"Alguien"}')
check "editar encargado de wh-001" 200 $(code -X PATCH $CAT/warehouses/wh-001 -H "$A" -H "$J" -d '{"manager_name":"Ana Admin WH1","manager_username":"ana.bodega1"}')
echo "--- Catálogo (RF-04)"
check "precio mayorista mayor que el de venta" 400 $(code -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"P-001","name":"Tornillo","category":"Ferreteria","supplier":"Aceros SAS","cost":100,"sale_price":150,"wholesale_price":200,"initial_quantity":50}')
check "crear producto P-001" "201|409" $(code -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"p-001","name":"Tornillo 1/4","category":"Ferreteria","description":"Tornillo de acero","supplier":"Aceros SAS","cost":100,"sale_price":150,"wholesale_price":130,"initial_quantity":50}')
check "crear producto P-002" "201|409" $(code -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"P-002","name":"Tuerca","category":"Ferreteria","supplier":"Aceros SAS","cost":50,"sale_price":80,"wholesale_price":70,"initial_quantity":0}')
check "código repetido" 409 $(code -X POST $CAT/products -H "$A" -H "$J" -d '{"product_code":"P-001","name":"Otro","category":"Varios","supplier":"Otro SAS","cost":1,"sale_price":2,"wholesale_price":1}')
check "admin bodega crea producto" 403 $(code -X POST $CAT/products -H "$AB" -H "$J" -d '{"product_code":"P-009","name":"X","category":"X","supplier":"Y","cost":1,"sale_price":2,"wholesale_price":1}')
check "encargado ve el catálogo" 200 $(code $CAT/products -H "$E1")
echo "--- Productos de cada bodega (RF-08)"
check "encargado agrega producto a su bodega" 403 $(code -X POST $CAT/warehouses/wh-001/products -H "$E1" -H "$J" -d '{"product_code":"P-001"}')
check "admin bodega wh-001 agrega a wh-002" 403 $(code -X POST $CAT/warehouses/wh-002/products -H "$AB" -H "$J" -d '{"product_code":"P-001"}')
check "admin bodega agrega P-001 con 40 unidades (409 si ya estaba)" "202|409" $(code -X POST $CAT/warehouses/wh-001/products -H "$AB" -H "$J" -d '{"product_code":"P-001","initial_quantity":40}')
check "admin bodega agrega P-002 (sin cantidad: usa la del catálogo)" "202|409" $(code -X POST $CAT/warehouses/wh-001/products -H "$AB" -H "$J" -d '{"product_code":"P-002"}')
check "agregar P-001 otra vez" 409 $(code -X POST $CAT/warehouses/wh-001/products -H "$AB" -H "$J" -d '{"product_code":"P-001"}')
echo "     esperando que el aviso llegue a la bodega..."; sleep 9
check "la bodega ya tiene los productos" 200 $(code $WH/products -H "$E1"); echo "     $(grep -o '"sku":"[^"]*"' /tmp/body | tr '\n' ' ') $(grep -o '"sale_price":"[^"]*"' /tmp/body | head -1)"
check "inventario inicial de P-001" 200 $(code $WH/inventory/1 -H "$E1"); echo "     $(grep -o '"quantity":[0-9]*' /tmp/body)"
check "movimiento inicial con quién lo hizo" 200 $(code $WH/inventory/1/movements -H "$E1"); echo "     $(grep -o '"reference":"[^"]*","created_by":"[^"]*"' /tmp/body)"
echo "--- Cambio de precio llega a la bodega"
check "admin global cambia el precio de P-001" 200 $(code -X PATCH $CAT/products/P-001 -H "$A" -H "$J" -d '{"sale_price":175,"wholesale_price":150}')
sleep 8; curl -s $WH/products/1 -H "$E1" | grep -o '"sale_price":"[^"]*","wholesale_price":"[^"]*"' | sed 's/^/     en la bodega: /'
echo "--- Registro de bodegas en usuarios y transferencias"
check "usuario para bodega no registrada wh-009" 400 $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"x.nueve","password":"Encargo2026","full_name":"Nadie","role":"ENCARGADO","warehouse_id":"wh-009"}'); echo "     $(cat /tmp/body)"
check "usuario para wh-004 (registrada)" 201 $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"laura.bodega4","password":"Bodega2026","full_name":"Laura Gomez","role":"ADMIN_BODEGA","warehouse_id":"wh-004"}')
L4="Authorization: Bearer $(login laura.bodega4 Bodega2026)"
check "laura agrega P-002 a wh-004" 202 $(code -X POST $CAT/warehouses/wh-004/products -H "$L4" -H "$J" -d '{"product_code":"P-002","initial_quantity":5}')
check "transferencia hacia wh-004" 202 $(code -X POST $TR/transfers -H "$AB" -H "$J" -d '{"source_warehouse":"wh-001","destination_warehouse":"wh-004","product_id":"P-001","quantity":1}')
echo "--- Borrar bodegas (RF-03)"
check "borrar wh-004 con un usuario activo" 409 $(code -X DELETE $CAT/warehouses/wh-004 -H "$A"); echo "     $(cat /tmp/body)"
LID=$(curl -s "$ID/users?warehouse_id=wh-004" -H "$A" | grep -o '"id":[0-9]*' | cut -d: -f2)
curl -s -o /dev/null -X PATCH $ID/users/$LID -H "$A" -H "$J" -d '{"active":false}'
check "borrar wh-004 con historia: se archiva" 200 $(code -X DELETE $CAT/warehouses/wh-004 -H "$A"); echo "     $(cat /tmp/body)"
check "usuario nuevo para wh-004 archivada" 400 $(code -X POST $ID/users -H "$A" -H "$J" -d '{"username":"y.cuatro","password":"Encargo2026","full_name":"Nadie","role":"ENCARGADO","warehouse_id":"wh-004"}')
check "transferencia hacia wh-004 archivada" 400 $(code -X POST $TR/transfers -H "$AB" -H "$J" -d '{"source_warehouse":"wh-001","destination_warehouse":"wh-004","product_id":"P-001","quantity":1}')
check "wh-004 sigue apareciendo con include_archived" 200 $(code "$CAT/warehouses?include_archived=true" -H "$A"); echo "     $(grep -o '"warehouse_id":"wh-004"[^}]*"status":"[A-Z]*"' /tmp/body | grep -o '"status":"[A-Z]*"')"
check "crear wh-005 sin nada" 201 $(code -X POST $CAT/warehouses -H "$A" -H "$J" -d '{"warehouse_id":"wh-005","nit":"900567890-7","name":"Bodega Temporal","location":"Cali","manager_name":"Por asignar"}')
check "borrar wh-005 sin historia: se borra" 200 $(code -X DELETE $CAT/warehouses/wh-005 -H "$A"); echo "     $(grep -o '"result":"[A-Z]*"' /tmp/body)"
echo; echo "Resultado: $ok OK, $fail FAIL"
