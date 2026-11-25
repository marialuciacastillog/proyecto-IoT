# Fase 3 - Fan-out de eventos de inventario: SNS -> sync-queue y audit-queue
module "sns_events" {
  source         = "./modules/sns_events"
  sync_queue_url = module.sqs_sync.queue_url
  sync_queue_arn = module.sqs_sync.queue_arn
}

output "inventory_topic_arn" {
  value = module.sns_events.topic_arn
}

output "audit_queue_url" {
  value = module.sns_events.audit_queue_url
}
