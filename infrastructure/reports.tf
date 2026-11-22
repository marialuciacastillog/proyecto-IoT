# Persona 2 - Reportes
module "sqs_reports" {
  source = "./modules/sqs_reports"
}

module "s3_reports" {
  source = "./modules/s3_reports"
}
