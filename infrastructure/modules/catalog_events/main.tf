# Etapa B: avisos del catálogo. El publicador del catálogo (central) publica en catalog-events
# y SNS entrega una copia a la cola de cada bodega (catalog-wh-00X), con su DLQ.
# Para agregar una bodega nueva basta con agregar su id a la lista (sin tocar las demás).

resource "aws_sns_topic" "catalog_events" {
  name = "catalog-events"
}

resource "aws_sqs_queue" "dlq" {
  for_each                  = toset(var.warehouse_ids)
  name                      = "catalog-${each.key}-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "queue" {
  for_each                   = toset(var.warehouse_ids)
  name                       = "catalog-${each.key}"
  message_retention_seconds  = 1209600 # 14 días: si la bodega está caída, los avisos la esperan
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_policy" "allow_sns" {
  for_each  = toset(var.warehouse_ids)
  queue_url = aws_sqs_queue.queue[each.key].url
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.queue[each.key].arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_sns_topic.catalog_events.arn } }
    }]
  })
}

resource "aws_sns_topic_subscription" "to_warehouse" {
  for_each             = toset(var.warehouse_ids)
  topic_arn            = aws_sns_topic.catalog_events.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.queue[each.key].arn
  raw_message_delivery = true
}
