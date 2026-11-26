# Estadísticas para el Líder y el Administrador Global (RF-18, RF-21).
# Se calculan en el momento sobre la base central:
#   - audit_log: cada movimiento de cada bodega (compras, ventas, entradas, salidas...) y sus alertas
#   - global_inventory: cuánto tiene cada bodega hoy
#   - transfers: transferencias entre bodegas
# Se puede filtrar por cuántos días hacia atrás (1 a 365) y por bodega.
import os
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from api.auth import requireRoles
from api.roles import ADMIN_GLOBAL, LIDER
from worker.queries import LOW_STOCK_THRESHOLD

router = APIRouter(prefix="/stats", tags=["Estadísticas"])
DATABASE_URL = os.environ["DATABASE_URL"]

# Movimientos de la ventana de tiempo (y de la bodega, si se pide)
movementsWindow = """
    FROM audit_log a
    WHERE a.event_type = 'INVENTORY_UPDATED'
      AND a.created_at >= (now() AT TIME ZONE 'UTC') - (%(days)s * INTERVAL '1 day')
      AND (%(warehouse)s::text IS NULL OR a.warehouse_id = %(warehouse)s)
"""

queries = {
    "totals": """
        SELECT COALESCE(SUM(CASE WHEN movement_type = 'SALE' THEN 1 END), 0) AS sales_count,
               COALESCE(SUM(CASE WHEN movement_type = 'SALE' THEN movement_quantity END), 0) AS sales_units,
               COALESCE(SUM(CASE WHEN movement_type = 'SALE' THEN total END), 0) AS sales_amount,
               COALESCE(SUM(CASE WHEN movement_type = 'PURCHASE' THEN 1 END), 0) AS purchases_count,
               COALESCE(SUM(CASE WHEN movement_type = 'PURCHASE' THEN movement_quantity END), 0) AS purchases_units,
               COALESCE(SUM(CASE WHEN movement_type = 'PURCHASE' THEN total END), 0) AS purchases_amount,
               COUNT(*) AS movements
    """ + movementsWindow,
    "by_day": """
        SELECT ((a.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Bogota')::date AS day,
               COALESCE(SUM(CASE WHEN movement_type = 'SALE' THEN total END), 0) AS sales_amount,
               COALESCE(SUM(CASE WHEN movement_type = 'PURCHASE' THEN total END), 0) AS purchases_amount,
               COUNT(*) AS movements
    """ + movementsWindow + " GROUP BY 1 ORDER BY 1",
    "by_warehouse": """
        SELECT a.warehouse_id,
               COALESCE(SUM(CASE WHEN movement_type = 'SALE' THEN total END), 0) AS sales_amount,
               COALESCE(SUM(CASE WHEN movement_type = 'SALE' THEN movement_quantity END), 0) AS sales_units,
               COALESCE(SUM(CASE WHEN movement_type = 'PURCHASE' THEN total END), 0) AS purchases_amount,
               COUNT(*) AS movements,
               MAX(a.created_at) AS last_movement
    """ + movementsWindow + " GROUP BY a.warehouse_id ORDER BY a.warehouse_id",
    "movement_types": """
        SELECT COALESCE(movement_type, 'SIN_TIPO') AS movement_type, COUNT(*) AS movements,
               COALESCE(SUM(movement_quantity), 0) AS units
    """ + movementsWindow + " GROUP BY 1 ORDER BY 2 DESC",
    "top_products": """
        SELECT a.product_id, MAX(p.name) AS name, SUM(movement_quantity) AS units, COALESCE(SUM(total), 0) AS amount
    """ + movementsWindow.replace("FROM audit_log a", "FROM audit_log a LEFT JOIN products p ON p.product_code = a.product_id") +
        " AND movement_type = 'SALE' GROUP BY a.product_id ORDER BY units DESC LIMIT 10",
    "alerts": """
        SELECT a.details->>'alertType' AS alert_type, COUNT(*) AS alerts
        FROM audit_log a
        WHERE a.event_type = 'STOCK_ALERT' AND COALESCE(a.details->>'level', '') <> 'INFO'
          AND a.created_at >= (now() AT TIME ZONE 'UTC') - (%(days)s * INTERVAL '1 day')
          AND (%(warehouse)s::text IS NULL OR a.warehouse_id = %(warehouse)s)
        GROUP BY 1 ORDER BY 2 DESC
    """,
    "transfers": """
        SELECT status, COUNT(*) AS transfers, COALESCE(SUM(quantity), 0) AS units,
               ROUND(AVG(EXTRACT(EPOCH FROM (updated_at - created_at)))::numeric, 1) AS average_seconds
        FROM transfers
        WHERE created_at >= now() - (%(days)s * INTERVAL '1 day')
          AND (%(warehouse)s::text IS NULL OR source_warehouse = %(warehouse)s OR destination_warehouse = %(warehouse)s)
        GROUP BY status ORDER BY 2 DESC
    """,
    "inventory": """
        SELECT warehouse_id, COUNT(*) AS products, COALESCE(SUM(quantity), 0) AS units,
               COUNT(*) FILTER (WHERE quantity < """ + str(LOW_STOCK_THRESHOLD) + """) AS low_stock
        FROM global_inventory
        WHERE (%(warehouse)s::text IS NULL OR warehouse_id = %(warehouse)s)
        GROUP BY warehouse_id ORDER BY warehouse_id
    """
}


@router.get("")
def statistics(days: int = 30, warehouse_id: Optional[str] = None, user=Depends(requireRoles(ADMIN_GLOBAL, LIDER))):
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days debe estar entre 1 y 365")
    params = {"days": days, "warehouse": (warehouse_id or "").strip().lower() or None}
    result = {"days": days, "warehouse_id": params["warehouse"]}
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            for name, sql in queries.items():
                result[name] = conn.execute(sql, params).fetchall()
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="No se pudieron calcular las estadísticas: " + str(error).split("\n")[0])
    result["totals"] = result["totals"][0]
    totals = result["totals"]
    totals["balance"] = totals["sales_amount"] - totals["purchases_amount"]
    completed = sum(row["transfers"] for row in result["transfers"] if row["status"] == "COMPLETED")
    allTransfers = sum(row["transfers"] for row in result["transfers"])
    result["transfer_success_rate"] = round(completed * 100 / allTransfers, 1) if allTransfers > 0 else None
    return result
