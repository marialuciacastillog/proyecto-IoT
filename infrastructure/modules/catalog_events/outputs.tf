output "topic_arn" {
  value = aws_sns_topic.catalog_events.arn
}

output "queue_urls" {
  value = { for name, queue in aws_sqs_queue.queue : name => queue.url }
}
