#!/bin/bash
# Fase 11 - Pruebas de carga sobre la bodega WH-002 (la que tiene balanceador y 3 copias de la API).
# Corre la matriz completa y guarda una tabla con los resultados:
#   conexiones: 100, 500, 1000, 5000
#   configuraciones: 3 APIs + Redis ON, 1 API + Redis ON, 3 APIs + Redis OFF
# La carga se lanza desde la EC2 central (hey) para que el internet de la casa no ponga el límite.
#
# Uso (desde la carpeta del proyecto):  bash scripts/fase11/carga.sh

KEY=~/Downloads/labsuser.pem
SSHOPT="-i $KEY -o StrictHostKeyChecking=no -o LogLevel=ERROR"
DURACION=${DURACION:-15s}
IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
W2=$(echo $IPS | grep -o '"wh-002":"[^"]*"' | cut -d'"' -f4)
OUT=evidencias/fase11/carga
CSV=evidencias/fase11/resultados_carga.csv
mkdir -p $OUT
echo "config,apis,redis,conexiones,rps,p50_ms,p95_ms,p99_ms,error_pct,hit_ratio,cpu_apis,cpu_db,cpu_lb,cpu_redis" > $CSV

# Deja la bodega con N copias de la API y Redis prendido o apagado
setConfig() {
  apis=$1
  cache=$2
  echo ">>> Configurando: $apis API(s), Redis $cache"
  ssh $SSHOPT ubuntu@$W2 "cd /opt/proyecto/src/warehouse && sudo CACHE_ENABLED=$cache docker compose -f docker/components/wh_002/compose-warehouse.yml --project-directory . --env-file docker/components/wh_002/.env up -d --no-deps --scale wh_002_warehouse_api=$apis wh_002_warehouse_api 2>/dev/null && sudo docker restart lb_WH-002 > /dev/null"
  sleep 10
  for i in 1 2 3 4 5 6; do curl -s -o /dev/null http://$W2:8002/inventory/1; done
}

# Una prueba: hey durante $DURACION con N conexiones. A mitad de la prueba se mide la CPU de la bodega.
runTest() {
  name=$1
  apis=$2
  cache=$3
  conns=$4
  file=$OUT/$name-$conns.txt
  curl -s -o /dev/null -X DELETE http://$W2:8002/cache/stats
  ( sleep 7; ssh $SSHOPT ubuntu@$W2 "sudo docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}'" > $OUT/$name-$conns-cpu.txt ) &
  ssh $SSHOPT ubuntu@$C "sudo docker run --rm --ulimit nofile=65535:65535 williamyeh/hey -z $DURACION -c $conns http://$W2:8002/inventory/1" > $file
  wait
  rps=$(awk '/Requests\/sec/ {printf "%.0f", $2}' $file)
  p50=$(awk '$1 == "50%" {printf "%.0f", $3 * 1000}' $file)
  p95=$(awk '$1 == "95%" {printf "%.0f", $3 * 1000}' $file)
  p99=$(awk '$1 == "99%" {printf "%.0f", $3 * 1000}' $file)
  # Error rate: todo lo que no fue [200], incluyendo conexiones rechazadas o vencidas
  errors=$(awk '/^[ \t]*\[[0-9]+\]/ { code = $1; gsub(/[\[\]]/, "", code); if ($0 ~ /responses/) { total += $2; if (code != "200") bad += $2 } else { total += code; bad += code } } END { if (total > 0) printf "%.2f", bad * 100 / total; else print "100" }' $file)
  hit=$(curl -s http://$W2:8002/cache/stats | grep -o '"hit_ratio":[0-9.]*' | cut -d: -f2)
  cpuApis=$(awk '/warehouse_api/ { gsub("%", "", $2); s += $2 } END { printf "%.0f%%", s }' $OUT/$name-$conns-cpu.txt)
  cpuDb=$(awk '/db_warehouse/ { print $2 }' $OUT/$name-$conns-cpu.txt)
  cpuLb=$(awk '/lb_/ { print $2 }' $OUT/$name-$conns-cpu.txt)
  cpuRedis=$(awk '/redis_/ { print $2 }' $OUT/$name-$conns-cpu.txt)
  echo "$name,$apis,$cache,$conns,${rps:--},${p50:--},${p95:--},${p99:--},$errors,${hit:--},$cpuApis,${cpuDb:--},${cpuLb:--},${cpuRedis:--}" >> $CSV
  echo "    $conns conexiones -> $rps req/s | p50 ${p50:--} ms | p95 ${p95:--} ms | p99 ${p99:--} ms | errores $errors% | hit ratio ${hit:--} | CPU APIs $cpuApis, DB ${cpuDb:--}"
}

runConfig() {
  name=$1
  apis=$2
  cache=$3
  setConfig $apis $cache
  for conns in 100 500 1000 5000; do
    runTest $name $apis $cache $conns
  done
}

echo "Central: $C | WH-002: $W2 | cada prueba dura $DURACION"
ssh $SSHOPT ubuntu@$C "sudo docker pull -q williamyeh/hey > /dev/null"
runConfig 3apis-redis-on 3 true
runConfig 1api-redis-on 1 true
runConfig 3apis-redis-off 3 false
# Se deja como estaba: 3 copias con Redis
setConfig 3 true
echo
echo "Resultados en $CSV:"
cat $CSV
