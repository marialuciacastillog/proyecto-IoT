module "sqs_reports" {
  source = "./modules/sqs_reports"
}

module "s3_reports" {
  source = "./modules/s3_reports"
}

output "reports_queue_url" {
  value = module.sqs_reports.queue_url
}

output "reports_bucket_name" {
  value = module.s3_reports.bucket_name
}
