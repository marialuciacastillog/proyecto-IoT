
variable "queue_name" {
  description = "Nombre de la cola principal de reportes"
  type        = string
  default     = "reports-queue"
}

variable "dlq_name" {
  description = "Nombre de la cola de mensajes fallidos"
  type        = string
  default     = "reports-queue-dlq"
}

variable "max_receive_count" {
  description = "Numero de recepciones antes de enviar un mensaje a la DLQ"
  type        = number
  default     = 3
}
