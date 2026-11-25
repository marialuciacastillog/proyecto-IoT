# Etapa B - Catálogo: avisos de productos asignados y actualizados hacia cada bodega
module "catalog_events" {
  source        = "./modules/catalog_events"
  warehouse_ids = ["wh-001", "wh-002", "wh-003"]
}

output "catalog_topic_arn" {
  value = module.catalog_events.topic_arn
}

output "catalog_queue_urls" {
  value = module.catalog_events.queue_urls
}
