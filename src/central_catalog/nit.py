# Validación del NIT colombiano con su dígito de verificación (regla de la DIAN).
# Se multiplica cada dígito (de derecha a izquierda) por un peso, se suma todo y se saca el residuo
# de dividir entre 11. Si el residuo es 0 o 1, ese es el dígito; si no, el dígito es 11 - residuo.
import re

weights = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]


def checkDigit(number):
    total = 0
    position = 0
    for digit in reversed(number):
        total = total + int(digit) * weights[position]
        position = position + 1
    remainder = total % 11
    digit = remainder
    if remainder >= 2:
        digit = 11 - remainder
    return digit


# Recibe "900.123.456-8" o "900123456-8". Devuelve el NIT limpio ("900123456-8") o "" si no es válido
def normalizeNit(nit):
    clean = re.sub(r"[.\s]", "", nit or "")
    result = ""
    match = re.fullmatch(r"(\d{9})-(\d)", clean)
    if match and checkDigit(match.group(1)) == int(match.group(2)):
        result = match.group(1) + "-" + match.group(2)
    return result
