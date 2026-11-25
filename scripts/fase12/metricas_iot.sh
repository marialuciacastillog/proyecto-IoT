#!/bin/bash
# Fase 12 - Métricas del sistema IoT: throughput, latencia, pérdida, duplicados, fuera de orden,
# alertas, analytics, colas y buffer de cada gateway.
# Uso (desde la carpeta del proyecto):  bash scripts/fase12/metricas_iot.sh

KEY=~/Downloads/labsuser.pem
SSHOPT="-i $KEY -o StrictHostKeyChecking=no -o LogLevel=ERROR"
IPS=$(terraform -chdir=infrastructure output -json ec2_public_ips)
C=$(echo $IPS | grep -o '"central":"[^"]*"' | cut -d'"' -f4)
SQL="sudo docker exec db_central psql -U postgres -d central_db -c"

echo "===== Sensores y bodegas ====="
ssh $SSHOPT ubuntu@$C "$SQL \"select count(distinct warehouse_id) as bodegas, count(*) as sensores from iot_sensor_state\" -c \"select warehouse_id, count(*) as sensores from iot_sensor_state group by warehouse_id order by warehouse_id\""

echo "===== Throughput y latencia (último minuto) ====="
ssh $SSHOPT ubuntu@$C "$SQL \"select count(*) as lecturas, round(count(*) / 60.0, 1) as lecturas_por_segundo, round(avg(extract(epoch from stored_at - event_time)) * 1000) as latencia_promedio_ms, round((percentile_cont(0.95) within group (order by extract(epoch from stored_at - event_time)) * 1000)::numeric) as latencia_p95_ms, round(max(extract(epoch from stored_at - event_time)) * 1000) as latencia_max_ms from iot_events where stored_at > now() - interval '1 minute'\""

echo "===== Pérdida, duplicados y fuera de orden (desde el inicio) ====="
# Pérdida: en cada sensor, los sequence_number deberían ir sin huecos desde el primero hasta el último
ssh $SSHOPT ubuntu@$C "$SQL \"select sum(esperadas) as esperadas, sum(recibidas) as recibidas, sum(esperadas - recibidas) as perdidas from (select max(sequence_number) - min(sequence_number) + 1 as esperadas, count(*) as recibidas from iot_events group by warehouse_id, sensor_id) as s\" -c \"select name as contador, value from iot_counters order by name\" -c \"select count(*) as guardadas, count(distinct event_id) as distintas from iot_events\""

echo "===== Estado final consistente (el último dato de cada sensor es el de mayor sequence_number) ====="
ssh $SSHOPT ubuntu@$C "$SQL \"select count(*) as sensores, sum(case when s.last_sequence = m.max_seq then 1 else 0 end) as con_el_dato_mas_nuevo from iot_sensor_state s join (select warehouse_id, sensor_id, max(sequence_number) as max_seq from iot_events group by warehouse_id, sensor_id) m using (warehouse_id, sensor_id)\""

echo "===== Alertas ====="
ssh $SSHOPT ubuntu@$C "$SQL \"select rule as regla, count(*) as alertas from iot_alerts group by rule order by rule\""

echo "===== Analytics (último minuto completo, temperatura) ====="
ssh $SSHOPT ubuntu@$C "$SQL \"select warehouse_id, readings as lecturas, round((total / readings)::numeric, 1) as promedio, min_value as minimo, max_value as maximo from iot_analytics where sensor_type = 'temperature' and minute = date_trunc('minute', now()) - interval '1 minute' order by warehouse_id\""

echo "===== Colas IoT (esperando / en proceso) ====="
for q in iot-storage iot-alerts iot-analytics iot-storage-dlq iot-alerts-dlq iot-analytics-dlq; do
  url=$(aws sqs get-queue-url --queue-name $q --query QueueUrl --output text)
  echo "$q -> $(aws sqs get-queue-attributes --queue-url $url --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --query 'Attributes.[ApproximateNumberOfMessages, ApproximateNumberOfMessagesNotVisible]' --output text)"
done

echo "===== Buffer de cada gateway ====="
for w in wh-001 wh-002 wh-003; do
  ip=$(echo $IPS | grep -o "\"$w\":\"[^\"]*\"" | cut -d'"' -f4)
  echo "$w: $(ssh $SSHOPT ubuntu@$ip "sudo docker logs iot_gateway 2>&1 | grep gateway_stats | tail -1")"
done
