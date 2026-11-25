# Fase 6: una cola de órdenes por bodega (la central -> la bodega)
# y una cola de respuestas (las bodegas -> la central). Cada una con su DLQ.

resource "aws_sqs_queue" "commands_dlq" {
  for_each                  = toset(var.warehouse_ids)
  name                      = "transfer-commands-${each.key}-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "commands" {
  for_each                   = toset(var.warehouse_ids)
  name                       = "transfer-commands-${each.key}"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.commands_dlq[each.key].arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue" "events_dlq" {
  name                      = "transfer-events-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "events" {
  name                       = "transfer-events"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 5

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.events_dlq.arn
    maxReceiveCount     = 5
  })
}
