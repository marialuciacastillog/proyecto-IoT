import json
import redis
from app.config import settings

# Cliente de Redis de esta bodega.
# Si Redis no responde, ninguna función falla: la API sigue trabajando con PostgreSQL.
redisClient = redis.Redis.from_url(
    settings.REDIS_URL,
    socket_connect_timeout=1,
    socket_timeout=1,
    decode_responses=True
)


# La llave lleva la bodega para que wh-001 y wh-002 no se pisen
def buildInventoryKey(productId):
    key = "warehouse:" + settings.WAREHOUSE_ID + ":inventory:" + str(productId)
    return key


def getCache(key):
    value = None
    if settings.CACHE_ENABLED:
        try:
            text = redisClient.get(key)
            if text is not None:
                value = json.loads(text)
        except Exception as error:
            print("Redis no responde, se usa PostgreSQL:", error)
    return value


# Guarda con TTL: aunque nadie la borre, la llave se vence sola
def setCache(key, value):
    if settings.CACHE_ENABLED:
        try:
            redisClient.set(key, json.dumps(value, default=str), ex=settings.CACHE_TTL_SECONDS)
        except Exception as error:
            print("No se pudo guardar en Redis:", error)


# Invalidación explícita: se llama después de cambiar el inventario en PostgreSQL
def deleteCache(key):
    try:
        redisClient.delete(key)
        print("CACHE BORRADO", key)
    except Exception as error:
        print("No se pudo borrar de Redis:", error)


# Cuenta aciertos (hits) y fallos (misses) para medir el cache hit ratio
def countCache(result):
    try:
        redisClient.incr("warehouse:" + settings.WAREHOUSE_ID + ":cache:" + result)
    except Exception as error:
        print("No se pudo contar en Redis:", error)


def getCacheStats():
    stats = {"warehouse_id": settings.WAREHOUSE_ID, "cache_enabled": settings.CACHE_ENABLED,
             "hits": 0, "misses": 0, "hit_ratio": 0.0, "redis_ok": False}
    try:
        hits = int(redisClient.get("warehouse:" + settings.WAREHOUSE_ID + ":cache:hits") or 0)
        misses = int(redisClient.get("warehouse:" + settings.WAREHOUSE_ID + ":cache:misses") or 0)
        stats["hits"] = hits
        stats["misses"] = misses
        stats["redis_ok"] = True
        if hits + misses > 0:
            stats["hit_ratio"] = round(hits / (hits + misses), 3)
    except Exception as error:
        print("No se pudieron leer las estadísticas de Redis:", error)
    return stats


def resetCacheStats():
    try:
        redisClient.delete("warehouse:" + settings.WAREHOUSE_ID + ":cache:hits")
        redisClient.delete("warehouse:" + settings.WAREHOUSE_ID + ":cache:misses")
    except Exception as error:
        print("No se pudieron reiniciar las estadísticas:", error)
