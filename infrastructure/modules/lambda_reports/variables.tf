variable "bucket_name" {
  description = "Bucket donde el Batch deja los reportes"
  type        = string
}

variable "source_file" {
  description = "Archivo con el código de la Lambda"
  type        = string
}
