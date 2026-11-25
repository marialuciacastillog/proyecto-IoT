output "command_queue_urls" {
  value = { for k, v in aws_sqs_queue.commands : k => v.url }
}

output "events_queue_url" {
  value = aws_sqs_queue.events.url
}
