# Yopii- Sincronización

variable "vpc_cidrs" {
  type        = map(string)
  description = "Rango de IPs de cada VPC"
}

variable "subnet_cidrs" {
  type        = map(string)
  description = "Rango de IPs de la subred de cada VPC"
}

variable "instance_type" {
  type        = string
  description = "Tamaño de las máquinas EC2"
}

variable "repo_url" {
  type        = string
  description = "Repositorio de GitHub que descargan las EC2"
}

module "networking" {
  source       = "./modules/networking"
  vpc_cidrs    = var.vpc_cidrs
  subnet_cidrs = var.subnet_cidrs
}

module "ec2" {
  source              = "./modules/ec2"
  subnet_ids          = module.networking.subnet_ids
  security_group_ids  = module.networking.security_group_ids
  instance_type       = var.instance_type
  sync_queue_url      = module.sqs_sync.queue_url
  repo_url            = var.repo_url
  reports_queue_url   = module.sqs_reports.queue_url
  reports_bucket_name = module.s3_reports.bucket_name
  sync_topic_arn      = module.sns_events.topic_arn
  audit_queue_url     = module.sns_events.audit_queue_url
  jwt_private_key_pem = tls_private_key.jwt.private_key_pem
  jwt_public_key_pem  = tls_private_key.jwt.public_key_pem

  # Las suscripciones de SNS deben existir antes de que las bodegas empiecen a publicar
  depends_on = [module.sns_events, module.iot_events, module.catalog_events]
}

module "sqs_sync" {
  source     = "./modules/sqs_sync"
  queue_name = "sync-queue"
}

output "vpc_ids" {
  value = module.networking.vpc_ids
}

output "sync_queue_url" {
  value = module.sqs_sync.queue_url
}

output "ec2_public_ips" {
  value = module.ec2.public_ips
}
