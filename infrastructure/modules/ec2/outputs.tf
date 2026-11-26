# Cada máquina con su IP elástica (fija)
output "public_ips" {
  value = { for k, v in var.elastic_ips : k => v.public_ip }
}
