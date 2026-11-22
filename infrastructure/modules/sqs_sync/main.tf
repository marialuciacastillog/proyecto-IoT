# Cola de errores (DLQ): aquí caen los mensajes que fallan 3 veces
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.queue_name}-dlq"
  message_retention_seconds = 1209600 # 14 días
}

# Cola principal de sincronización
resource "aws_sqs_queue" "queue" {
  name                       = var.queue_name
  message_retention_seconds  = 1209600 # 14 días: si la central se cae, los mensajes esperan
  visibility_timeout_seconds = 30      # si el worker no borra el mensaje en 30 s, vuelve a la cola
  receive_wait_time_seconds  = 20      # el worker espera hasta 20 s por mensajes nuevos

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}