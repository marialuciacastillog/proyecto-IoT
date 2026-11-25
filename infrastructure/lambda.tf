# Fase 9 - Lambda que valida y resume los reportes del Batch
module "lambda_reports" {
  source      = "./modules/lambda_reports"
  bucket_name = module.s3_reports.bucket_name
  source_file = "${path.root}/../src/lambda_reports/handler.py"
}

output "lambda_function_name" {
  value = module.lambda_reports.function_name
}

output "report_events_queue_url" {
  value = module.lambda_reports.events_queue_url
}
