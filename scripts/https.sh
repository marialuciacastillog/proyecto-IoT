#!/bin/bash
# Se usa desde los otros scripts con:  . scripts/https.sh
# Todo se habla por HTTPS (puerto 443) y los puertos de las APIs ya no están
# abiertos a internet. Este archivo:
#   1. Saca las IPs fijas de las máquinas
#   2. Guarda la CA del proyecto en un archivo y hace que curl confíe en ella (revisa los certificados de verdad)
#   3. Deja listas las direcciones de cada API por la puerta de entrada HTTPS

IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
W1=$(echo $IPS | grep -o '"wh-001":"[^"]*"' | cut -d'"' -f4)
W2=$(echo $IPS | grep -o '"wh-002":"[^"]*"' | cut -d'"' -f4)
W3=$(echo $IPS | grep -o '"wh-003":"[^"]*"' | cut -d'"' -f4)

CA=/tmp/proyecto-ca.crt
terraform -chdir=infrastructure output -raw ca_certificate > $CA
# Cada vez que el script usa curl, curl revisa el certificado con la CA del proyecto.
# (Para bajar de S3 se usa "command curl", que usa las CA normales de internet.)
curl() { command curl --cacert "$CA" "$@"; }

# APIs de la central, por su front HTTPS (/svc/...)
ID=https://$C/svc/identity
CAT=https://$C/svc/catalog
NOT=https://$C/svc/notifications
TR=https://$C/svc/transfers
RP=https://$C/svc/reports
# APIs de las bodegas, por el front HTTPS de cada bodega (/api/ va al balanceador de la bodega)
WH=https://$W1/api
WH2=https://$W2/api
WH3=https://$W3/api
