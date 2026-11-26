#!/bin/bash
# Nota: los datos que se mandan con curl van sin tildes. En Git Bash de Windows curl recibe los textos
# con la codificación de Windows (no UTF-8) y la API no puede leer el JSON. Desde el navegador no pasa.
# Prueba de lo del Líder en AWS: contratos digitales en S3, estadísticas y reportes históricos con filtros.
# Uso (desde la carpeta del proyecto, después de las otras cuatro pruebas, para que haya compras y ventas):
#   bash scripts/pruebas/prueba_lider.sh

IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
ID=$C:8008; RP=$C:8000
ADMIN=$(bash scripts/token.sh)
J='Content-Type: application/json'; ok=0; fail=0
login() { curl -s -X POST $ID/auth/login -H "$J" -d "{\"username\":\"$1\",\"password\":\"$2\"}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4; }
code() { curl -s -o /tmp/body -w "%{http_code}" "$@"; }
codeHasNot() { local text=$1; shift; local status=$(code "$@"); if grep -q "$text" /tmp/body; then echo "sobra-$status"; else echo $status; fi; }
codeHas() { local text=$1; shift; local status=$(code "$@"); if grep -q "$text" /tmp/body; then echo $status; else echo "falta-$status"; fi; }
check() { if echo "$3" | grep -qE "^($2)$"; then echo "OK   $1 -> $3"; ok=$((ok+1)); else echo "FAIL $1 -> esperado $2, salio $3 | $(head -c 200 /tmp/body)"; fail=$((fail+1)); fi; }
A="Authorization: Bearer $ADMIN"
L="Authorization: Bearer $(login malu.lider Lider2026x)"
AB="Authorization: Bearer $(login ana.bodega1 Bodega2026)"; E1="Authorization: Bearer $(login juli.encargada1 Encargo2026)"

# Archivos de prueba: un PDF y una imagen PNG pequeños (llevan la hora para que cada vez sean distintos)
mkdir -p /tmp/contratos
printf '%%PDF-1.4\n%% Contrato de suministro de prueba %s\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%%%EOF\n' "$(date +%s)" > /tmp/contratos/suministro.pdf
printf '\x89PNG\r\n\x1a\nimagen de prueba %s' "$(date +%s)" > /tmp/contratos/arriendo.png
printf 'esto es texto, no un PDF' > /tmp/contratos/falso.pdf
printf 'MZ programa' > /tmp/contratos/programa.exe
# Campos de un contrato (en un arreglo, para que los textos con espacios no se partan)
form() { FORM=(--form-string "title=$1" --form-string "contract_type=$2" --form-string "counterparty=$3" --form-string "start_date=$4"); }

echo "--- Contratos digitales (RF-19, RF-20)"
check "el Líder carga un contrato PDF de proveedor" 201 $(code -X POST $RP/contracts -H "$L" -F "file=@/tmp/contratos/suministro.pdf" \
  --form-string "title=Suministro de tornillos 2027" --form-string "contract_type=PROVEEDOR" --form-string "counterparty=Aceros SAS" \
  --form-string "warehouse_id=wh-001" --form-string "product_code=P-001" --form-string "start_date=2026-01-01" \
  --form-string "end_date=2027-12-31" --form-string "value=5000000"); echo "     $(grep -o '"label":"[^"]*"' /tmp/body) $(grep -o '"validity":"[^"]*"' /tmp/body)"
CT1=$(grep -o '"id":[0-9]*' /tmp/body | head -1 | cut -d: -f2)
check "el mismo archivo otra vez" 409 $(form "Repetido" PROVEEDOR "Aceros SAS" 2026-01-01; code -X POST $RP/contracts -H "$L" -F "file=@/tmp/contratos/suministro.pdf" "${FORM[@]}")
check "un texto con extensión .pdf" 400 $(form "Falso" OTRO "Nadie" 2026-01-01; code -X POST $RP/contracts -H "$L" -F "file=@/tmp/contratos/falso.pdf" "${FORM[@]}"); echo "     $(head -c 120 /tmp/body)"
check "un archivo .exe" 400 $(form "Programa" OTRO "Nadie" 2026-01-01; code -X POST $RP/contracts -H "$L" -F "file=@/tmp/contratos/programa.exe" "${FORM[@]}")
check "una bodega que no existe" 400 $(form "Arriendo" ARRENDAMIENTO "Inmobiliaria Sur" 2026-01-01; code -X POST $RP/contracts -H "$L" -F "file=@/tmp/contratos/arriendo.png" "${FORM[@]}" --form-string "warehouse_id=wh-099")
check "termina antes de empezar" 400 $(form "Arriendo" ARRENDAMIENTO "Inmobiliaria Sur" 2026-06-01; code -X POST $RP/contracts -H "$L" -F "file=@/tmp/contratos/arriendo.png" "${FORM[@]}" --form-string "end_date=2026-01-01")
check "el Líder carga un contrato de arriendo (imagen)" 201 $(form "Arriendo bodega sur" ARRENDAMIENTO "Inmobiliaria Sur" 2026-01-01; code -X POST $RP/contracts -H "$L" -F "file=@/tmp/contratos/arriendo.png" "${FORM[@]}" --form-string "warehouse_id=wh-002")
CT2=$(grep -o '"id":[0-9]*' /tmp/body | head -1 | cut -d: -f2)
check "el Admin Global intenta cargar (solo el Líder carga)" 403 $(form "X" OTRO "Nadie" 2026-01-01; code -X POST $RP/contracts -H "$A" -F "file=@/tmp/contratos/arriendo.png" "${FORM[@]}")
check "el Admin por Bodega intenta cargar" 403 $(form "X" OTRO "Nadie" 2026-01-01; code -X POST $RP/contracts -H "$AB" -F "file=@/tmp/contratos/arriendo.png" "${FORM[@]}")
check "el Encargado intenta ver los contratos" 403 $(code $RP/contracts -H "$E1")
check "el Líder ve los contratos de wh-001" 200 $(codeHas "\"id\":$CT1," "$RP/contracts?warehouse_id=wh-001" -H "$L"); echo "     contratos de wh-001: $(grep -o '"label"' /tmp/body | wc -l)"
check "el Admin Global también los consulta" 200 $(codeHas "Aceros SAS" "$RP/contracts?search=aceros" -H "$A")
check "link de descarga" 200 $(code $RP/contracts/$CT1/download -H "$L")
URL=$(grep -o '"download_url":"[^"]*"' /tmp/body | cut -d'"' -f4 | sed 's/\\u0026/\&/g')
curl -s -o /tmp/contratos/descargado.pdf "$URL"
if [ "$(sha256sum < /tmp/contratos/descargado.pdf)" = "$(sha256sum < /tmp/contratos/suministro.pdf)" ]; then IGUAL=igual; else IGUAL=distinto; fi
check "el archivo descargado de S3 es igual al cargado" igual $IGUAL
check "verificar la huella SHA-256 del archivo en S3" 200 $(codeHas '"intact":true' $RP/contracts/$CT1/verify -H "$A")
check "archivar sin motivo" 400 $(code -X POST $RP/contracts/$CT2/archive -H "$L" -H "$J" -d '{"reason":""}')
check "archivar el de arriendo con motivo" 200 $(codeHas '"status":"ARCHIVED"' -X POST $RP/contracts/$CT2/archive -H "$L" -H "$J" -d '{"reason":"Se cargo una version vieja"}')
check "el archivado no sale en la lista normal" 200 $(codeHasNot "\"id\":$CT2," "$RP/contracts" -H "$L")
check "pero sí cuando se piden los archivados" 200 $(codeHas "\"id\":$CT2," "$RP/contracts?include_archived=true" -H "$L")

echo "--- Estadísticas (RF-18)"
check "estadísticas de los últimos 30 días" 200 $(codeHas '"sales_count":[1-9]' "$RP/stats?days=30" -H "$L"); echo "     $(grep -o '"totals":{[^}]*}' /tmp/body)"
echo "     transferencias completadas: $(grep -o '"transfer_success_rate":[0-9.a-z]*' /tmp/body)"
check "estadísticas solo de wh-001" 200 $(codeHas '"warehouse_id":"wh-001"' "$RP/stats?days=30&warehouse_id=wh-001" -H "$A"); echo "     más vendido: $(grep -o '"top_products":\[{[^}]*}' /tmp/body)"
check "días fuera de rango" 400 $(code "$RP/stats?days=0" -H "$L")
check "el Admin por Bodega intenta ver estadísticas" 403 $(code "$RP/stats" -H "$AB")

echo "--- Reportes históricos y consolidados (RF-17, RF-29, RF-30)"
check "hay reportes nuevos" 200 $(codeHas '"movements_history"' $RP/reports/types -H "$L"); echo "     tipos: $(grep -o '"report_type":"[a-z_]*"' /tmp/body | cut -d'"' -f4 | tr '\n' ' ')"
DESDE=$(date -d "30 days ago" +%F); HOY=$(date +%F)
check "compras y ventas de wh-001 del último mes (JSON)" 202 $(code -X POST $RP/reports -H "$L" -H "$J" -d "{\"report_type\":\"sales_purchases\",\"format\":\"json\",\"warehouse_id\":\"wh-001\",\"date_from\":\"$DESDE\",\"date_to\":\"$HOY\"}")
R1=$(grep -o '"job_id":"[^"]*"' /tmp/body | cut -d'"' -f4)
check "historial de movimientos de P-001 (CSV)" 202 $(code -X POST $RP/reports -H "$L" -H "$J" -d '{"report_type":"movements_history","format":"csv","product_id":"p-001"}')
R2=$(grep -o '"job_id":"[^"]*"' /tmp/body | cut -d'"' -f4)
check "consolidado por bodega" 202 $(code -X POST $RP/reports -H "$L" -H "$J" -d '{"report_type":"warehouse_summary","format":"csv"}')
R3=$(grep -o '"job_id":"[^"]*"' /tmp/body | cut -d'"' -f4)
check "reporte de contratos" 202 $(code -X POST $RP/reports -H "$A" -H "$J" -d '{"report_type":"contracts","format":"json"}')
R4=$(grep -o '"job_id":"[^"]*"' /tmp/body | cut -d'"' -f4)
check "filtro que ese reporte no acepta" 400 $(code -X POST $RP/reports -H "$L" -H "$J" -d '{"report_type":"warehouse_summary","format":"csv","product_id":"P-001"}')
check "hasta antes de desde" 400 $(code -X POST $RP/reports -H "$L" -H "$J" -d '{"report_type":"sales_purchases","format":"csv","date_from":"2026-11-20","date_to":"2026-11-01"}')
check "el Encargado intenta pedir un reporte" 403 $(code -X POST $RP/reports -H "$E1" -H "$J" -d '{"report_type":"sales_purchases","format":"csv"}')
echo "     esperando que el worker los suba a S3..."
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  LISTOS=0
  for R in $R1 $R2 $R3 $R4; do curl -s $RP/reports/jobs/$R -H "$L" | grep -q '"status":"COMPLETED"' && LISTOS=$((LISTOS+1)); done
  [ $LISTOS -eq 4 ] && break; sleep 5
done
check "los cuatro reportes quedaron listos" 4 $LISTOS
bajar() { curl -s $RP/reports/jobs/$1/download -H "$L" | grep -o '"download_url":"[^"]*"' | cut -d'"' -f4 | sed 's/\\u0026/\&/g' | xargs curl -s -o /tmp/contratos/$2; }
bajar $R1 ventas.json; bajar $R2 movimientos.csv; bajar $R3 bodegas.csv; bajar $R4 contratos.json
OTRAS=$(grep -o '"warehouse_id": "wh-00[23]"' /tmp/contratos/ventas.json | wc -l)
check "compras y ventas: solo trae wh-001 y tiene ventas" "0-si" "$OTRAS-$(grep -q '"tipo": "VENTA"' /tmp/contratos/ventas.json && echo si || echo no)"; echo "     filas: $(grep -c '"fecha"' /tmp/contratos/ventas.json)"
check "historial de movimientos: solo P-001" "si" "$(awk -F, 'NR>1 && $3!="P-001"{bad=1} END{print (NR>1 && !bad) ? "si" : "no"}' /tmp/contratos/movimientos.csv)"; echo "     $(head -1 /tmp/contratos/movimientos.csv | cut -c1-150)"
check "consolidado por bodega: una fila por bodega" "si" "$(grep -c '^wh-00' /tmp/contratos/bodegas.csv | awk '{print ($1>=3) ? "si" : "no"}')"; echo "     $(sed -n 2p /tmp/contratos/bodegas.csv)"
check "reporte de contratos: trae el contrato cargado" "si" "$(grep -q "\"contrato\": \"CT-$(printf %04d $CT1)\"" /tmp/contratos/contratos.json && echo si || echo no)"
echo "Resultado: $ok OK, $fail FAIL"
