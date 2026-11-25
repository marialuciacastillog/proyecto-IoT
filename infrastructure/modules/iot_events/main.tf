# Fase 12: fan-out de las lecturas IoT. El servicio de ingesta publica en el tema iot-events
# y SNS entrega una copia a cada cola: iot-storage, iot-alerts e iot-analytics.

locals {
  consumers = ["storage", "alerts", "analytics"]
}

resource "aws_sns_topic" "iot_events" {
  name = "iot-events"
}

resource "aws_sqs_queue" "dlq" {
  for_each                  = toset(local.consumers)
  name                      = "iot-${each.key}-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "queue" {
  for_each                   = toset(local.consumers)
  name                       = "iot-${each.key}"
  message_retention_seconds  = 345600 # 4 días: si un worker se cae, las lecturas esperan
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_policy" "allow_sns" {
  for_each  = toset(local.consumers)
  queue_url = aws_sqs_queue.queue[each.key].url
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.queue[each.key].arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_sns_topic.iot_events.arn } }
    }]
  })
}

resource "aws_sns_topic_subscription" "to_queue" {
  for_each             = toset(local.consumers)
  topic_arn            = aws_sns_topic.iot_events.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.queue[each.key].arn
  raw_message_delivery = true
}
