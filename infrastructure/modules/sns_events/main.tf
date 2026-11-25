# Fase 3: fan-out con SNS (como un exchange "fanout" de RabbitMQ).
# Las bodegas publican UNA vez en el tema inventory-events y SNS entrega una copia a cada cola suscrita:
#   - sync-queue  -> worker de sincronización (actualiza global_inventory)
#   - audit-queue -> worker de auditoría (guarda audit_log)

resource "aws_sns_topic" "inventory_events" {
  name = "inventory-events"
}

# Cola de errores de auditoría: aquí caen los mensajes que fallan 3 veces
resource "aws_sqs_queue" "audit_dlq" {
  name                      = "audit-queue-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "audit" {
  name                       = "audit-queue"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.audit_dlq.arn
    maxReceiveCount     = 3
  })
}

# Permiso para que el tema SNS pueda escribir en cada cola
resource "aws_sqs_queue_policy" "allow_sns" {
  for_each = {
    sync  = { url = var.sync_queue_url, arn = var.sync_queue_arn }
    audit = { url = aws_sqs_queue.audit.url, arn = aws_sqs_queue.audit.arn }
  }

  queue_url = each.value.url
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = each.value.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_sns_topic.inventory_events.arn } }
    }]
  })
}

# Suscripciones. raw_message_delivery = true: la cola recibe el JSON tal cual lo mandó la bodega,
# sin el sobre de SNS, así los workers no cambian.
resource "aws_sns_topic_subscription" "to_sync" {
  topic_arn            = aws_sns_topic.inventory_events.arn
  protocol             = "sqs"
  endpoint             = var.sync_queue_arn
  raw_message_delivery = true
}

resource "aws_sns_topic_subscription" "to_audit" {
  topic_arn            = aws_sns_topic.inventory_events.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.audit.arn
  raw_message_delivery = true
}
