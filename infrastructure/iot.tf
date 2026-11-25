# Fase 12 - Extensión IoT: SNS iot-events -> colas de storage, alerts y analytics
module "iot_events" {
  source = "./modules/iot_events"
}

output "iot_topic_arn" {
  value = module.iot_events.topic_arn
}

output "iot_queue_urls" {
  value = module.iot_events.queue_urls
}
