-- Filtros de los reportes históricos (desde, hasta, bodega, producto)
ALTER TABLE report_jobs ADD COLUMN IF NOT EXISTS params JSONB;
