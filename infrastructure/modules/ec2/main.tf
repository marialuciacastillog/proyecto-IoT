# Imagen de Ubuntu 24.04 más reciente (así no hay que buscar el ami_id a mano)
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical, los creadores de Ubuntu

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

# Cada máquina recibe su IP elástica (fija). Las IPs se crean afuera de este módulo (infrastructure/https.tf),
# así no cambian al recrear las máquinas y las bodegas saben desde el arranque dónde está la central.
resource "aws_eip_association" "servers" {
  for_each      = var.subnet_ids
  instance_id   = aws_instance.servers[each.key].id
  allocation_id = var.elastic_ips[each.key].allocation_id
}

# Una máquina por VPC: wh-001, wh-002, wh-003 y central
resource "aws_instance" "servers" {
  for_each               = var.subnet_ids
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = each.value
  vpc_security_group_ids = [var.security_group_ids[each.key]]
  key_name               = "vockey"             # llave del Learner Lab (labsuser.pem en AWS Details)
  iam_instance_profile   = "LabInstanceProfile" # rol del Learner Lab: le da permiso de usar SQS

  # Para que los contenedores puedan pedir las credenciales del rol a la máquina
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = 20
  }

  # Script que corre solo una vez, al prender: instala Docker, baja el repo y levanta los contenedores
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    role                = each.key
    wh_folder           = replace(each.key, "-", "_")
    sync_queue_url      = var.sync_queue_url
    repo_url            = var.repo_url
    reports_queue_url   = var.reports_queue_url
    reports_bucket_name = var.reports_bucket_name
    sync_topic_arn      = var.sync_topic_arn
    audit_queue_url     = var.audit_queue_url
    jwt_private_key_pem = each.key == "central" ? var.jwt_private_key_pem : ""
    jwt_public_key_pem  = var.jwt_public_key_pem
    central_ip          = var.elastic_ips["central"].public_ip
    # Certificado HTTPS de esta máquina (RNF-03) y la CA del proyecto
    tls_cert_pem        = var.tls_certificates[each.key].cert_pem
    tls_key_pem         = var.tls_certificates[each.key].key_pem
    tls_ca_pem          = var.tls_ca_pem
  })
  user_data_replace_on_change = true

  tags = { Name = "ec2-${each.key}" }
}
