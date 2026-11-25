#!/bin/bash
# Etapa A - Saca un token de inicio de sesión desde el servicio de identidad (central, puerto 8008).
# Uso (desde la carpeta del proyecto):
#   TOKEN=$(bash scripts/token.sh)                      -> token del admin (lee su contraseña de la central)
#   TOKEN=$(bash scripts/token.sh usuario contraseña)   -> token de otro usuario
# Luego se usa así:  curl -H "Authorization: Bearer $TOKEN" ...

KEY=~/Downloads/labsuser.pem
# UserKnownHostsFile=/dev/null: no se guarda la llave de la máquina, porque cambia cada vez que se recrea
SSHOPT="-i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
C=$(terraform -chdir=infrastructure output -json ec2_public_ips | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
USUARIO=${1:-admin}
CLAVE=$2
if [ -z "$CLAVE" ]; then
  CLAVE=$(ssh $SSHOPT ubuntu@$C "sudo cat /opt/proyecto/secrets/admin_password.txt")
fi
curl -s -X POST http://$C:8008/auth/login -H "Content-Type: application/json" \
  -d "{\"username\":\"$USUARIO\",\"password\":\"$CLAVE\"}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4
