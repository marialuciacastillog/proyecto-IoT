# La central se muestra con su IP elástica (la fija); las bodegas con la IP que les dio AWS
output "public_ips" {
  value = merge(
    { for k, v in aws_instance.servers : k => v.public_ip },
    { central = aws_eip.central.public_ip }
  )
}
