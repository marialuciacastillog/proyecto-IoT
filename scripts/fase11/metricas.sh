#!/bin/bash
# Fase 11 - Foto de las métricas del sistema en este momento: API, Redis, colas y bases de datos.
# Uso (desde la carpeta del proyecto):  bash scripts/fase11/metricas.sh

KEY=~/Downloads/labsuser.pem
# UserKnownHostsFile=/dev/null: no se guarda la llave de la máquina, porque cambia cada vez que se recrea
SSHOPT="-i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
W1=$(echo $IPS | grep -o '"wh-001":"[^"]*"' | cut -d'"' -f4)
SQ=$(terraform -chdir=infrastructure output -raw sync_queue_url)
AQ=$(terraform -chdir=infrastructure output -raw audit_queue_url)

TOKEN=$(bash scripts/token.sh)
echo "===== API WH-001 (calculado con los logs JSON de los últimos 5 minutos) ====="
ssh $SSHOPT ubuntu@$W1 "sudo docker logs --since 5m warehouse_api_node_WH-001 2>&1 | grep http_request" > /tmp/api_logs.txt
total=$(wc -l < /tmp/api_logs.txt)
echo "Peticiones: $total"
if [ "$total" -gt 0 ]; then
  grep -o '"duration_ms": [0-9.]*' /tmp/api_logs.txt | awk '{print $2}' | sort -n > /tmp/durations.txt
  # Percentil: se ordenan las duraciones y se toma la que queda en la posición del porcentaje
  awk '{ d[NR] = $1 } END { i50 = int(NR * 0.50); i95 = int(NR * 0.95); i99 = int(NR * 0.99); if (i50 < 1) i50 = 1; if (i95 < 1) i95 = 1; if (i99 < 1) i99 = 1; printf "Latencia  P50: %s ms | P95: %s ms | P99: %s ms\n", d[i50], d[i95], d[i99] }' /tmp/durations.txt
  errores=$(grep -c '"status": 5' /tmp/api_logs.txt)
  awk -v e=$errores -v t=$total 'BEGIN { printf "Error rate (5xx): %.2f%% (%d de %d)\n", e * 100 / t, e, t }'
fi

echo
echo "===== Redis WH-001 ====="
ssh $SSHOPT ubuntu@$W1 "sudo docker exec redis_WH-001 redis-cli INFO stats | grep -E 'keyspace_hits|keyspace_misses'; sudo docker exec redis_WH-001 redis-cli INFO memory | grep used_memory_human"
echo "Estadísticas de la API: $(curl -s -H "Authorization: Bearer $TOKEN" http://$W1:8001/cache/stats)"

echo
echo "===== Colas (SQS) ====="
for q in $SQ $SQ-dlq $AQ $AQ-dlq; do
  name=${q##*/}
  attrs=$(aws sqs get-queue-attributes --queue-url $q --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --query "Attributes.[ApproximateNumberOfMessages, ApproximateNumberOfMessagesNotVisible]" --output text)
  echo "$name -> esperando / en proceso: $attrs"
done
desde=$(date -u -d '-15 minutes' +%Y-%m-%dT%H:%M:%SZ)
hasta=$(date -u +%Y-%m-%dT%H:%M:%SZ)
procesados=$(aws cloudwatch get-metric-statistics --namespace AWS/SQS --metric-name NumberOfMessagesDeleted --dimensions Name=QueueName,Value=sync-queue --start-time $desde --end-time $hasta --period 900 --statistics Sum --query "Datapoints[0].Sum" --output text)
echo "sync-queue procesados en 15 min (CloudWatch): $procesados"
reintentos=$(ssh $SSHOPT ubuntu@$C "sudo docker logs central_sync_worker 2>&1 | grep -c processing_failed")
echo "Reintentos en el worker de sincronización: $reintentos"

echo
echo "===== Base de datos WH-001 ====="
ssh $SSHOPT ubuntu@$W1 "sudo docker exec db_warehouse_WH-001 psql -U postgres -d warehouse_db -c \"select state, count(*) as conexiones from pg_stat_activity where datname = 'warehouse_db' group by state\" -c \"select mode, count(*) as locks from pg_locks group by mode\" -c 'explain analyze select * from inventory where product_id = 1'" | grep -vE "Seq Scan|Filter|Rows Removed|Index|Planning"

echo
echo "===== Base de datos central ====="
ssh $SSHOPT ubuntu@$C "sudo docker exec db_central psql -U postgres -d central_db -c \"select state, count(*) as conexiones from pg_stat_activity where datname = 'central_db' group by state\""
