# Llaves para firmar los tokens de inicio de sesión (RS256).
# - La llave PRIVADA solo va a la central (servicio de identidad): es la única que puede crear tokens.
# - La llave PÚBLICA va a todas las máquinas: con ella cada servicio comprueba que un token es auténtico.
# Así una bodega revisa los tokens sola, sin preguntarle a la central (RNF-04), y si alguien
# entrara a una bodega no podría fabricar tokens falsos.
# Nota: la llave queda en el estado de Terraform y en el user_data. En una cuenta real se guardaría
# en AWS Secrets Manager; en Learner Lab se deja así para no depender de más servicios.
resource "tls_private_key" "jwt" {
  algorithm = "RSA"
  rsa_bits  = 2048
}
