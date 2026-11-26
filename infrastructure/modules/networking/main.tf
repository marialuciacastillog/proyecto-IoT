# Creación de las 4 VPCs
resource "aws_vpc" "vpcs" {
  for_each             = var.vpc_cidrs
  cidr_block           = each.value
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "vpc-${each.key}" }
}

# Una subred en cada VPC
resource "aws_subnet" "subnets" {
  for_each                = var.subnet_cidrs
  vpc_id                  = aws_vpc.vpcs[each.key].id
  cidr_block              = each.value
  availability_zone       = "us-east-1a"
  map_public_ip_on_launch = true
  tags                    = { Name = "subnet-${each.key}" }
}

# Puerta a internet de cada VPC
resource "aws_internet_gateway" "igws" {
  for_each = var.vpc_cidrs
  vpc_id   = aws_vpc.vpcs[each.key].id
  tags     = { Name = "igw-${each.key}" }
}

# Tabla de rutas: todo lo que no es de la VPC sale por la puerta a internet
resource "aws_route_table" "routes" {
  for_each = var.vpc_cidrs
  vpc_id   = aws_vpc.vpcs[each.key].id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igws[each.key].id
  }

  tags = { Name = "rt-${each.key}" }
}

# Conectar cada subred con su tabla de rutas
resource "aws_route_table_association" "routes" {
  for_each       = var.subnet_cidrs
  subnet_id      = aws_subnet.subnets[each.key].id
  route_table_id = aws_route_table.routes[each.key].id
}

# Permisos de acceso (security group) de cada VPC
resource "aws_security_group" "sgs" {
  for_each    = var.vpc_cidrs
  name        = "secgroup-${each.key}"
  description = "SSH, HTTPS y puertos internos solo entre las maquinas del proyecto"
  vpc_id      = aws_vpc.vpcs[each.key].id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # RNF-03: desde internet solo se entra por HTTPS (443). Ahí está el front y, detrás, las APIs.
  ingress {
    description = "HTTPS: fronts y APIs de la central y de las bodegas"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Los puertos HTTP de los fronts (8080 central, 8081-8083 bodegas) siguen abiertos,
  # pero solo responden con una redirección a HTTPS.
  ingress {
    description = "HTTP de los fronts: redirige a HTTPS"
    from_port   = 8080
    to_port     = 8090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Los puertos de las APIs (8000-8010) ya no se abren a internet: solo a las IPs fijas de las
  # máquinas del proyecto (por ejemplo, la prueba de carga de la Fase 11 corre desde la central).
  ingress {
    description = "APIs internas, solo entre las maquinas del proyecto"
    from_port   = 8000
    to_port     = 8010
    protocol    = "tcp"
    cidr_blocks = var.machine_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "secgroup-${each.key}" }
}