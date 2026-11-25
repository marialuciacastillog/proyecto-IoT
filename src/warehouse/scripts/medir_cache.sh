#!/bin/bash
# La API pide token. Antes de correrlo:  export TOKEN=<token del Administrador Global>
# Fase 2 - Experimento: hace muchas consultas iguales y mide cuánto tarda en promedio.
# Uso: bash scripts/medir_cache.sh 8001 1 200
#   8001 = puerto de la bodega, 1 = id del producto, 200 = número de consultas

PUERTO=${1:-8001}
PRODUCTO=${2:-1}
VECES=${3:-200}
URL="http://localhost:$PUERTO/inventory/$PRODUCTO"

echo "Consultando $URL $VECES veces..."
INICIO=$(date +%s%N)
for i in $(seq 1 $VECES); do
  curl -s -o /dev/null -w "%{time_total}\n" -H "Authorization: Bearer $TOKEN" "$URL"
done | awk '{ suma += $1; if ($1 > max) max = $1 } END { printf "Promedio: %.2f ms | Más lenta: %.2f ms\n", (suma / NR) * 1000, max * 1000 }'
FIN=$(date +%s%N)
SEGUNDOS=$(awk "BEGIN { print ($FIN - $INICIO) / 1000000000 }")
awk "BEGIN { printf \"Total: %.2f s | Requests por segundo: %.1f\n\", $SEGUNDOS, $VECES / $SEGUNDOS }"

echo "Estadísticas del caché:"
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:$PUERTO/cache/stats"
echo
