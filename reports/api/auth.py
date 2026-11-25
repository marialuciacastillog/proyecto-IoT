# Etapa A: revisión del token en cada petición. Este archivo se copia igual en cada servicio.
# El token lo firma SOLO el servicio de identidad con su llave privada.
# Los demás servicios solo tienen la llave pública: pueden comprobar que el token es auténtico,
# pero no pueden fabricar uno. Así cada bodega revisa los tokens sola, sin preguntarle a la central.
import os
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from api.roles import ADMIN_GLOBAL, TOKEN_ISSUER, WAREHOUSE_ROLES

publicKeyPath = os.environ.get("JWT_PUBLIC_KEY_PATH", "/secrets/jwt_public.pem")
bearer = HTTPBearer(auto_error=False)
keys = {"public": None}


def getPublicKey():
    if keys["public"] is None:
        with open(publicKeyPath) as keyFile:
            keys["public"] = keyFile.read()
    return keys["public"]


# Devuelve los datos del usuario que vienen en el token, o responde 401 si el token no sirve
def currentUser(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Falta el token. Inicia sesión en POST /auth/login",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(credentials.credentials, getPublicKey(), algorithms=["RS256"], issuer=TOKEN_ISSUER)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="El token se venció. Inicia sesión otra vez",
                            headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido", headers={"WWW-Authenticate": "Bearer"})
    return {
        "username": claims["sub"],
        "role": claims["role"],
        "warehouse_id": claims.get("warehouse_id"),
        "name": claims.get("name", "")
    }


# Uso:  user = Depends(requireRoles(ADMIN_GLOBAL, LIDER))
# Responde 403 si el rol del usuario no está en la lista.
def requireRoles(*allowedRoles):
    def checker(user=Depends(currentUser)):
        if user["role"] not in allowedRoles:
            raise HTTPException(status_code=403, detail="Tu rol (" + user["role"] + ") no tiene permiso para esto")
        return user
    return checker


# Para los roles de una sola bodega: revisa que la bodega del token sea la que se quiere tocar
def checkWarehouse(user, warehouseId):
    if user["role"] in WAREHOUSE_ROLES and (user["warehouse_id"] or "").lower() != (warehouseId or "").lower():
        raise HTTPException(status_code=403, detail="Solo puedes trabajar con tu bodega (" + str(user["warehouse_id"]) + ")")


def isGlobalAdmin(user):
    return user["role"] == ADMIN_GLOBAL
