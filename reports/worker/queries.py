# Tipos de reporte que se pueden pedir. Cada uno es una consulta sobre la base central.
# Para agregar un reporte nuevo basta con agregarlo aquí: la API y el worker lo leen de este archivo.
# Los que tienen "filters" aceptan filtros al pedirlos (desde, hasta, bodega, producto). Los filtros que no
# se mandan llegan como NULL y no filtran.

LOW_STOCK_THRESHOLD = 10
FILTERS = ["date_from", "date_to", "warehouse_id", "product_id"]

# Condición común de los reportes históricos sobre audit_log (fechas en hora de Colombia)
historyFilter = """
    AND (%(date_from)s::date IS NULL OR ((a.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Bogota')::date >= %(date_from)s::date)
    AND (%(date_to)s::date IS NULL OR ((a.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Bogota')::date <= %(date_to)s::date)
    AND (%(warehouse_id)s::text IS NULL OR a.warehouse_id = %(warehouse_id)s)
    AND (%(product_id)s::text IS NULL OR a.product_id = %(product_id)s)
"""
localTime = "((a.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Bogota')"

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
    },
    # ---- Reportes históricos y consolidados (RF-17, RF-29, RF-30) ----
    "movements_history": {
        "description": "Historial de movimientos de las bodegas (compras, ventas, entradas, salidas, reservas, transferencias)",
        "filters": FILTERS,
        "sql": """
            SELECT """ + localTime + """ AS fecha, a.warehouse_id, a.product_id, a.movement_type, a.movement_quantity,
                   a.quantity AS disponible_despues, a.details->>'reservedAfter' AS reservado_despues, a.lot_code,
                   a.unit_price, a.total, a.details->>'counterparty' AS proveedor_o_cliente,
                   a.details->>'reference' AS referencia, a.performed_by, a.correlation_id
            FROM audit_log a
            WHERE a.event_type = 'INVENTORY_UPDATED'
        """ + historyFilter + """
            ORDER BY a.created_at DESC
            LIMIT 20000
        """
    },
    "sales_purchases": {
        "description": "Histórico de compras y ventas con precio, total y proveedor o cliente",
        "filters": FILTERS,
        "sql": """
            SELECT """ + localTime + """ AS fecha, a.warehouse_id, a.product_id, p.name AS producto,
                   CASE a.movement_type WHEN 'SALE' THEN 'VENTA' ELSE 'COMPRA' END AS tipo,
                   a.movement_quantity AS cantidad, a.unit_price, a.total, a.details->>'priceType' AS tipo_precio,
                   a.details->>'counterparty' AS proveedor_o_cliente, a.lot_code, a.performed_by
            FROM audit_log a
            LEFT JOIN products p ON p.product_code = a.product_id
            WHERE a.event_type = 'INVENTORY_UPDATED' AND a.movement_type IN ('SALE', 'PURCHASE')
        """ + historyFilter + """
            ORDER BY a.created_at DESC
        """
    },
    "sales_by_product": {
        "description": "Consolidado de compras y ventas por producto y bodega (unidades y dinero)",
        "filters": FILTERS,
        "sql": """
            SELECT a.product_id, MAX(p.name) AS producto, a.warehouse_id,
                   COALESCE(SUM(a.movement_quantity) FILTER (WHERE a.movement_type = 'SALE'), 0) AS unidades_vendidas,
                   COALESCE(SUM(a.total) FILTER (WHERE a.movement_type = 'SALE'), 0) AS total_vendido,
                   COALESCE(SUM(a.movement_quantity) FILTER (WHERE a.movement_type = 'PURCHASE'), 0) AS unidades_compradas,
                   COALESCE(SUM(a.total) FILTER (WHERE a.movement_type = 'PURCHASE'), 0) AS total_comprado
            FROM audit_log a
            LEFT JOIN products p ON p.product_code = a.product_id
            WHERE a.event_type = 'INVENTORY_UPDATED' AND a.movement_type IN ('SALE', 'PURCHASE')
        """ + historyFilter + """
            GROUP BY a.product_id, a.warehouse_id
            ORDER BY total_vendido DESC, a.product_id
        """
    },
    "warehouse_summary": {
        "description": "Consolidado por bodega: inventario de hoy, ventas, compras, movimientos, alertas y transferencias",
        "filters": ["date_from", "date_to", "warehouse_id"],
        "sql": """
            SELECT w.warehouse_id, w.name AS bodega, w.status,
                   (SELECT COUNT(*) FROM global_inventory g WHERE g.warehouse_id = w.warehouse_id) AS productos,
                   (SELECT COALESCE(SUM(g.quantity), 0) FROM global_inventory g WHERE g.warehouse_id = w.warehouse_id) AS unidades_hoy,
                   COALESCE(SUM(a.total) FILTER (WHERE a.movement_type = 'SALE'), 0) AS total_vendido,
                   COALESCE(SUM(a.total) FILTER (WHERE a.movement_type = 'PURCHASE'), 0) AS total_comprado,
                   COUNT(a.event_id) FILTER (WHERE a.event_type = 'INVENTORY_UPDATED') AS movimientos,
                   COUNT(a.event_id) FILTER (WHERE a.event_type = 'STOCK_ALERT' AND a.details->>'level' <> 'INFO') AS alertas,
                   (SELECT COUNT(*) FROM transfers t WHERE t.source_warehouse = w.warehouse_id) AS transferencias_enviadas,
                   (SELECT COUNT(*) FROM transfers t WHERE t.destination_warehouse = w.warehouse_id) AS transferencias_recibidas,
                   MAX(""" + localTime + """) AS ultimo_movimiento
            FROM warehouses w
            LEFT JOIN audit_log a ON a.warehouse_id = w.warehouse_id
                AND (%(date_from)s::date IS NULL OR """ + localTime + """::date >= %(date_from)s::date)
                AND (%(date_to)s::date IS NULL OR """ + localTime + """::date <= %(date_to)s::date)
            WHERE (%(warehouse_id)s::text IS NULL OR w.warehouse_id = %(warehouse_id)s)
            GROUP BY w.warehouse_id, w.name, w.status
            ORDER BY w.warehouse_id
        """
    },
    "stock_alerts": {
        "description": "Alertas de las bodegas: incongruencias, reservas y lotes vencidos",
        "filters": FILTERS,
        "sql": """
            SELECT """ + localTime + """ AS fecha, a.warehouse_id, a.product_id, a.details->>'alertType' AS tipo,
                   a.details->>'level' AS nivel, a.details->>'title' AS titulo, a.details->>'message' AS mensaje
            FROM audit_log a
            WHERE a.event_type = 'STOCK_ALERT'
        """ + historyFilter + """
            ORDER BY a.created_at DESC
        """
    },
    "contracts": {
        "description": "Contratos digitales cargados por el Líder, con su vigencia",
        "filters": ["warehouse_id"],
        "sql": """
            SELECT 'CT-' || LPAD(id::text, 4, '0') AS contrato, title AS titulo, contract_type AS tipo,
                   counterparty AS contraparte, warehouse_id, product_code, start_date AS inicio, end_date AS fin,
                   CASE WHEN status = 'ARCHIVED' THEN 'ARCHIVADO'
                        WHEN end_date IS NOT NULL AND end_date < CURRENT_DATE THEN 'VENCIDO'
                        ELSE 'VIGENTE' END AS vigencia,
                   value AS valor, file_name AS archivo, sha256, uploaded_by AS cargado_por, uploaded_at AS cargado
            FROM contracts
            WHERE (%(warehouse_id)s::text IS NULL OR warehouse_id = %(warehouse_id)s)
            ORDER BY id
        """
    }
}

FORMATS = {
    "csv": "text/csv",
    "json": "application/json"
}
