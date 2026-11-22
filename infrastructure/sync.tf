# Yopii- Sincronización

variable "vpc_cidrs" {
  type        = map(string)
  description = "Rango de IPs de cada VPC"
}

variable "subnet_cidrs" {
  type        = map(string)
  description = "Rango de IPs de la subred de cada VPC"
}

module "networking" {
  source       = "./modules/networking"
  vpc_cidrs    = var.vpc_cidrs
  subnet_cidrs = var.subnet_cidrs
}

module "ec2" {
  source = "./modules/ec2"
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