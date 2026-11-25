#!/bin/bash
# Uso: definir C, W1, W2, W3 con las IPs de terraform output y luego: source comparar.sh
altA() { total=0; t0=$(date +%s%N); for w in "$W1:8001" "$W2:8002" "$W3:8003"; do q=$(curl -s --max-time 5 -H "Authorization: Bearer $TOKEN" http://$w/inventory/1 | grep -o '"quantity":[0-9]*' | cut -d: -f2); if [ -z "$q" ]; then echo "   $w NO RESPONDE"; else total=$((total+q)); fi; done; t1=$(date +%s%N); echo "A) total=$total | $(( (t1-t0)/1000000 )) ms"; }
altB() { t0=$(date +%s%N); r=$(curl -s --max-time 5 -H "Authorization: Bearer $TOKEN" http://$C:8000/global/inventory/P-001 | grep -o '"total_quantity":[0-9]*' | cut -d: -f2); t1=$(date +%s%N); echo "B) total=$r | $(( (t1-t0)/1000000 )) ms"; }
