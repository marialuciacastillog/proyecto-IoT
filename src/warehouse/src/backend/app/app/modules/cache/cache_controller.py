from fastapi import APIRouter
from app.modules.cache.redis_cache import getCacheStats, resetCacheStats

router = APIRouter(prefix="/cache", tags=["Cache"])


# Cuántas lecturas salieron de Redis (hits) y cuántas fueron a PostgreSQL (misses)
@router.get("/stats")
def cacheStats():
    stats = getCacheStats()
    return stats


@router.delete("/stats")
def cacheStatsReset():
    resetCacheStats()
    stats = getCacheStats()
    return stats
