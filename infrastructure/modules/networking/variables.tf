variable "vpc_cidrs" {
  type        = map(string)
  description = "Rango de IPs de cada VPC"
}

variable "subnet_cidrs" {
  type        = map(string)
  description = "Rango de IPs de la subred de cada VPC"
}