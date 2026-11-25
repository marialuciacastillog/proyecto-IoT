# Tipos de reporte que se pueden pedir. Cada uno es una consulta sobre la base central.
# Para agregar un reporte nuevo basta con agregarlo aquí: la API y el worker lo leen de este archivo.

LOW_STOCK_THRESHOLD = 10

REPORT_TYPES = {
    "global_inventory": {
        "description": "Total de cada producto sumando todas las bodegas",
        "sql": """
            SELECT product_id, SUM(quantity) AS total_quantity
            FROM global_inventory
            GROUP BY product_id
            ORDER BY product_id
        """
    },
    "inventory_by_warehouse": {
        "description": "Cantidad de cada producto en cada bodega",
        "sql": """
            SELECT warehouse_id, product_id, quantity, updated_at
            FROM global_inventory
            ORDER BY warehouse_id, product_id
        """
    },
    "low_stock": {
        "description": "Productos con menos de " + str(LOW_STOCK_THRESHOLD) + " unidades en alguna bodega",
        "sql": """
            SELECT warehouse_id, product_id, quantity
            FROM global_inventory
            WHERE quantity < """ + str(LOW_STOCK_THRESHOLD) + """
            ORDER BY quantity, warehouse_id
        """
    },
    "transfers": {
        "description": "Transferencias entre bodegas y su estado",
        "sql": """
            SELECT transfer_id, source_warehouse, destination_warehouse, product_id,
                   quantity, status, reason, requested_by, justification, created_at, updated_at
            FROM transfers
            ORDER BY created_at DESC
        """
    },
    "audit": {
        "description": "Últimos 1000 movimientos registrados por auditoría, con su correlation_id y quién lo hizo",
        "sql": """
            SELECT event_id, event_type, correlation_id, warehouse_id,
                   product_id, quantity, performed_by, created_at
            FROM audit_log
            ORDER BY created_at DESC
            LIMIT 1000
        """
    }
}

FORMATS = {
    "csv": "text/csv",
    "json": "application/json"
}
