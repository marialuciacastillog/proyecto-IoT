#!/bin/bash
# Prueba de la seguridad de las comunicaciones (RNF-03) en AWS:
#   - todo entra por HTTPS con certificados firmados por la CA del proyecto, y el HTTP redirige
#   - los puertos de las APIs ya no se pueden usar desde internet, pero sí entre las máquinas
#   - las bodegas le hablan a la central por HTTPS revisando su certificado (login, catálogo, IoT)
# Uso (desde la carpeta del proyecto):  bash scripts/pruebas/prueba_https.sh

. scripts/https.sh
KEY=~/Downloads/labsuser.pem
SSHOPT="-i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
J='Content-Type: application/json'; ok=0; fail=0
check() { if echo "$3" | grep -qE "^($2)$"; then echo "OK   $1 -> $3"; ok=$((ok+1)); else echo "FAIL $1 -> esperado $2, salio $3"; fail=$((fail+1)); fi; }
# Código HTTP y a dónde redirige
redirect() { command curl -s -o /dev/null --max-time 10 -w "%{http_code} %{redirect_url}" "$1"; }
# Código HTTP de una URL por HTTPS, revisando el certificado con la CA del proyecto
secure() { curl -s -o /dev/null --max-time 10 -w "%{http_code}" "$@"; }
# Si curl NO llega en 5 segundos responde 000 (puerto cerrado por el security group)
closed() { command curl -s -o /dev/null --connect-timeout 5 --max-time 8 -w "%{http_code}" "$1"; }

echo "--- Certificados"
echo "     CA del proyecto: $(openssl x509 -in $CA -noout -subject 2>/dev/null)"
for name in central:$C wh-001:$W1 wh-002:$W2 wh-003:$W3; do
  ip=${name#*:}
  info=$(echo | openssl s_client -connect $ip:443 -CAfile $CA -verify_ip $ip 2>/dev/null)
  check "${name%%:*}: certificado válido para la IP $ip" "0 \(ok\)" "$(echo "$info" | grep -o 'Verify return code: [0-9]* ([a-z]*)' | head -1 | cut -d: -f2 | sed 's/^ //')"
  echo "     $(echo "$info" | openssl x509 -noout -subject -enddate 2>/dev/null | tr -d '\r' | tr '\n' ' ')"
done
check "sin la CA del proyecto, curl no confía (no es un certificado cualquiera)" "60|35" "$(command curl -s -o /dev/null https://$C/ ; echo $?)"
check "TLS 1.1 o más viejo no se acepta" rechazado "$(command curl -s -o /dev/null --ssl-no-revoke --cacert $CA --tls-max 1.1 https://$C/ && echo aceptado || echo rechazado)"
check "la central manda HSTS (el navegador recuerda usar HTTPS)" "si" "$(curl -s -I https://$C/ | grep -qi 'strict-transport-security' && echo si || echo no)"

echo "--- El HTTP redirige a HTTPS"
check "front central http://:8080" "301 https://$C/" "$(redirect http://$C:8080/)"
check "front wh-001 http://:8081" "301 https://$W1/" "$(redirect http://$W1:8081/)"
check "front wh-002 http://:8082" "301 https://$W2/" "$(redirect http://$W2:8082/)"
check "front central por HTTPS" 200 "$(secure https://$C/)"
check "front wh-001 por HTTPS" 200 "$(secure https://$W1/)"

echo "--- Las APIs solo por HTTPS"
check "identidad por https://central/svc/identity" 200 "$(secure $ID/health)"
check "catálogo por https://central/svc/catalog" 200 "$(secure $CAT/health)"
check "reportes por https://central/svc/reports" 200 "$(secure $RP/health)"
check "API de wh-001 por https://wh-001/api" 200 "$(secure $WH/health)"
check "API de wh-002 por https://wh-002/api" 200 "$(secure $WH2/health)"
check "identidad directo por http://:8008 (cerrado)" 000 "$(closed http://$C:8008/health)"
check "reportes directo por http://:8000 (cerrado)" 000 "$(closed http://$C:8000/health)"
check "API de wh-001 directo por http://:8001 (cerrado)" 000 "$(closed http://$W1:8001/health)"
check "ingesta IoT directo por http://:8005 (cerrado)" 000 "$(closed http://$C:8005/health)"
check "pero entre máquinas sí: de la central al balanceador de wh-002" 200 "$(ssh $SSHOPT ubuntu@$C "curl -s -o /dev/null --max-time 5 -w '%{http_code}' http://$W2:8002/health")"

echo "--- Las bodegas le hablan a la central por HTTPS revisando su certificado"
check "login desde el front de wh-001 (wh-001 -> central)" 200 "$(secure -X POST https://$W1/identity/auth/login -H "$J" -d '{"username":"ana.bodega1","password":"Bodega2026"}')"
check "catálogo desde el front de wh-002 (wh-002 -> central)" 200 "$(secure https://$W2/catalog/health)"
LINEA=$(ssh $SSHOPT ubuntu@$W1 "sudo docker logs --since 2m iot_gateway 2>&1 | grep -c send_failed")
check "el gateway IoT de wh-001 manda sin errores por HTTPS (último 2 min)" 0 "$LINEA"
LECTURAS=$(ssh $SSHOPT ubuntu@$C "sudo docker exec db_central psql -U postgres -d central_db -tAc \"select count(*) from iot_events where stored_at > now() - interval '1 minute'\"" | tr -d ' \r')
check "la central sigue recibiendo lecturas IoT" "[1-9][0-9]*" "$LECTURAS"; echo "     lecturas del último minuto: $LECTURAS"
echo "Resultado: $ok OK, $fail FAIL"
