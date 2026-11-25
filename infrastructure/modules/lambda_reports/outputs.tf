output "function_name" {
  value = aws_lambda_function.report_processor.function_name
}

output "events_queue_url" {
  value = aws_sqs_queue.report_events.url
}
