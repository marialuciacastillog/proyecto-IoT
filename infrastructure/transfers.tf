# Fase 6 - Transferencias entre bodegas (Saga)
# Las colas se buscan por nombre desde el código (transfer-commands-wh-00X y transfer-events),
# así que no hay que pasarle URLs a las máquinas.

module "sqs_transfers" {
  source        = "./modules/sqs_transfers"
  warehouse_ids = ["wh-001", "wh-002", "wh-003"]
}

output "transfer_command_queue_urls" {
  value = module.sqs_transfers.command_queue_urls
}

output "transfer_events_queue_url" {
  value = module.sqs_transfers.events_queue_url
}
