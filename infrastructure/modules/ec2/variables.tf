variable "subnet_ids" {
  type        = map(string)
  description = "Subred donde va cada máquina (sale del módulo networking)"
}

variable "security_group_ids" {
  type        = map(string)
  description = "Security group de cada máquina (sale del módulo networking)"
}

variable "instance_type" {
  type        = string
  description = "Tamaño de las máquinas"
}

variable "sync_queue_url" {
  type        = string
  description = "URL de la cola sync-queue"
}

variable "repo_url" {
  type        = string
  description = "Repositorio de GitHub que cada máquina descarga al prender"
}

variable "reports_queue_url" {
  type        = string
  description = "URL de la cola reports-queue"
}

variable "reports_bucket_name" {
  type        = string
  description = "Bucket donde se guardan los reportes"
}

variable "sync_topic_arn" {
  type        = string
  description = "ARN del tema SNS inventory-events (Fase 3)"
}

variable "audit_queue_url" {
  type        = string
  description = "URL de la cola audit-queue (Fase 3)"
}

variable "jwt_private_key_pem" {
  type        = string
  sensitive   = true
  description = "Llave privada para firmar tokens (solo la usa la central) - Etapa A"
}

variable "jwt_public_key_pem" {
  type        = string
  description = "Llave pública para revisar tokens (la usan todas las máquinas) - Etapa A"
}
