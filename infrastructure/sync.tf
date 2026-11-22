# Persona 1 - Sincronización
module "networking" {
  source = "./modules/networking"
}

module "ec2" {
  source = "./modules/ec2"
}

module "sqs_sync" {
  source = "./modules/sqs_sync"
}
