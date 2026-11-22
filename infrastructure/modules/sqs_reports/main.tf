resource "aws_sqs_queue" "reports_dlq" {
  name = var.dlq_name

  message_retention_seconds = 1209600

  tags = {
    Project = "proyecto-IoT"
    Service = "reports"
    Type    = "dlq"
  }
}

resource "aws_sqs_queue" "reports" {
  name = var.queue_name

  visibility_timeout_seconds = 120
  message_retention_seconds  = 1209600
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.reports_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = {
    Project = "proyecto-IoT"
    Service = "reports"
    Type    = "main"
  }
}
