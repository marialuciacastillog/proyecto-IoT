variable "vpc_cidrs" {
  type        = map(string)
  description = "Rango de IPs de cada VPC"
}

variable "subnet_cidrs" {
  type        = map(string)
  description = "Rango de IPs de la subred de cada VPC"
}
variable "machine_cidrs" {
  type        = list(string)
  description = "IPs fijas de las máquinas del proyecto (x.x.x.x/32): las únicas que llegan a los puertos 8000-8010"
}
