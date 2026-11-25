output "topic_arn" {
  value = aws_sns_topic.inventory_events.arn
}

output "audit_queue_url" {
  value = aws_sqs_queue.audit.url
}

output "audit_dlq_url" {
  value = aws_sqs_queue.audit_dlq.url
}
