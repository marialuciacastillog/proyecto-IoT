# Los 4 roles del sistema. Este archivo se copia igual en cada servicio que revisa tokens
# (identidad, bodegas, reportes y transferencias), para que todos usen los mismos nombres.

ADMIN_GLOBAL = "ADMIN_GLOBAL"   # Administrador Global
ADMIN_BODEGA = "ADMIN_BODEGA"   # Administrador por Bodega
ENCARGADO = "ENCARGADO"         # Encargado de recepción y envío
LIDER = "LIDER"                 # Líder

ALL_ROLES = [ADMIN_GLOBAL, ADMIN_BODEGA, ENCARGADO, LIDER]

# Estos roles trabajan en UNA bodega: su token dice cuál, y solo pueden tocar esa
WAREHOUSE_ROLES = [ADMIN_BODEGA, ENCARGADO]

TOKEN_ISSUER = "proyecto-iot-identity"
