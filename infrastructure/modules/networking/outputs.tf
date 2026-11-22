output "vpc_ids" {
  value = { for k, v in aws_vpc.vpcs : k => v.id }
}

output "subnet_ids" {
  value = { for k, v in aws_subnet.subnets : k => v.id }
}

output "security_group_ids" {
  value = { for k, v in aws_security_group.sgs : k => v.id }
}