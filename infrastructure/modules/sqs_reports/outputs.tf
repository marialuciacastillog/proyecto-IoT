output "queue_url" {
  description = "URL de la cola principal de reportes"
  value       = aws_sqs_queue.reports.url
}

output "queue_arn" {
  description = "ARN de la cola principal de reportes"
  value       = aws_sqs_queue.reports.arn
}

output "dlq_url" {
  description = "URL de la cola de mensajes fallidos"
  value       = aws_sqs_queue.reports_dlq.url
}

output "dlq_arn" {
  description = "ARN de la cola de mensajes fallidos"
  value       = aws_sqs_queue.reports_dlq.arn
}
