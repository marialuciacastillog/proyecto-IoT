#!/bin/bash
# Corre las pruebas de carga de k6 y trae los reportes con gráficos.
# k6 NO se instala en el computador: corre en Docker dentro de la máquina de wh-003, que no participa en
# las pruebas. Así la carga sale desde la nube (no depende del internet de la casa) y la máquina que genera
# la carga no le quita CPU ni memoria a las máquinas que se están midiendo.
# Uso (desde la carpeta del proyecto, después de las pruebas de scripts/pruebas/, para que haya usuarios y productos):
#   bash scripts/k6/correr.sh                  -> las cuatro pruebas (unos 16 minutos)
#   bash scripts/k6/correr.sh ventas           -> solo una (consultas, bodegas, ventas o reportes)
# Los reportes quedan en resultados/k6/: un .html con gráficos por prueba (se abre con doble clic) y un .txt con el resumen.

. scripts/https.sh
KEY=~/Downloads/labsuser.pem
SSHOPT="-i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
PRUEBAS=${*:-consultas bodegas ventas reportes}
OUT=resultados/k6
mkdir -p $OUT

# La máquina que genera la carga
K6=$W3
echo "Copiando las pruebas a la máquina de wh-003 ($K6), que es la que genera la carga..."
ssh $SSHOPT ubuntu@$K6 "rm -rf /tmp/k6 && mkdir -p /tmp/k6/reportes && chmod 777 /tmp/k6/reportes"
scp $SSHOPT -q scripts/k6/*.js ubuntu@$K6:/tmp/k6/

for prueba in $PRUEBAS; do
  echo ""
  echo "=== Prueba: $prueba (unos 4 minutos) ==="
  # SSL_CERT_FILE: k6 confía en la CA del proyecto para revisar los certificados HTTPS
  # K6_WEB_DASHBOARD_EXPORT: al terminar, k6 guarda el reporte HTML con los gráficos
  ssh $SSHOPT ubuntu@$K6 "sudo docker run --rm \
    -v /tmp/k6:/scripts -v /opt/proyecto/secrets/tls/ca.crt:/tls/ca.crt:ro \
    -e SSL_CERT_FILE=/tls/ca.crt -e CENTRAL=$C -e WH1=$W1 -e WH2=$W2 \
    -e K6_WEB_DASHBOARD=true -e K6_WEB_DASHBOARD_PERIOD=5s -e K6_WEB_DASHBOARD_EXPORT=/scripts/reportes/$prueba.html \
    -e K6_NO_USAGE_REPORT=true \
    grafana/k6 run --quiet /scripts/$prueba.js" | tee $OUT/$prueba.txt
  # El reporte se trae apenas termina cada prueba
  scp $SSHOPT -q "ubuntu@$K6:/tmp/k6/reportes/$prueba.html" $OUT/ && echo "Reporte: $OUT/$prueba.html"
done

echo ""
ls -1 $OUT
echo "Abre los .html de $OUT con doble clic para ver los gráficos."
