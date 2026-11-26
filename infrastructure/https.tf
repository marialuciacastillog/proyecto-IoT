# Seguridad de las comunicaciones (RNF-03)
#
# 1. IPs fijas: cada máquina tiene su IP elástica. Están aquí afuera del módulo ec2, así que
#    "terraform destroy -target=module.ec2" ya NO las borra: al recrear las máquinas las IPs son las mismas.
# 2. Una autoridad certificadora (CA) propia del proyecto. Con ella se firma un certificado para cada
#    máquina, con su IP fija y un nombre interno (central.proyecto-iot.internal, wh-001...).
# 3. Cada máquina sirve HTTPS en el puerto 443 con su certificado. Los clientes (navegador, scripts,
#    los fronts de bodega cuando le hablan a la central y los gateways IoT) confían en la CA del proyecto.
#
# En el Learner Lab no hay dominio propio, así que no se puede usar un certificado público (ACM o
# Let's Encrypt): por eso la CA es propia. El navegador avisa la primera vez, hasta que se instala la CA.

locals {
  tls_domain = "proyecto-iot.internal"
}

resource "aws_eip" "servers" {
  for_each = var.vpc_cidrs
  domain   = "vpc"
  tags     = { Name = "eip-${each.key}" }
}

# Autoridad certificadora del proyecto (llave ECDSA P-256, dura 2 años)
resource "tls_private_key" "ca" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "ca" {
  private_key_pem       = tls_private_key.ca.private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 17520
  allowed_uses          = ["cert_signing", "crl_signing", "digital_signature"]

  subject {
    common_name  = "CA Proyecto IoT"
    organization = "Warehouse Cloud Platform"
  }
}

# Un certificado por máquina (dura 1 año), con su IP fija y su nombre interno
resource "tls_private_key" "servers" {
  for_each    = var.vpc_cidrs
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_cert_request" "servers" {
  for_each        = var.vpc_cidrs
  private_key_pem = tls_private_key.servers[each.key].private_key_pem
  ip_addresses    = [aws_eip.servers[each.key].public_ip]
  dns_names       = ["${each.key}.${local.tls_domain}"]

  subject {
    common_name  = "${each.key}.${local.tls_domain}"
    organization = "Warehouse Cloud Platform"
  }
}

resource "tls_locally_signed_cert" "servers" {
  for_each              = var.vpc_cidrs
  cert_request_pem      = tls_cert_request.servers[each.key].cert_request_pem
  ca_private_key_pem    = tls_private_key.ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.ca.cert_pem
  validity_period_hours = 8760
  allowed_uses          = ["digital_signature", "key_encipherment", "server_auth"]
}

# Los scripts de prueba la guardan para que curl confíe en los certificados:
#   terraform -chdir=infrastructure output -raw ca_certificate > /tmp/proyecto-ca.crt
output "ca_certificate" {
  value = tls_self_signed_cert.ca.cert_pem
}
