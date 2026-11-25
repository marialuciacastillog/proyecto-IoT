from fastapi import APIRouter, Depends
from app.modules.auth.auth import requireRoles
from app.modules.auth.roles import ADMIN_GLOBAL
from app.modules.cache.redis_cache import getCacheStats, resetCacheStats

router = APIRouter(prefix="/cache", tags=["Cache"])


# Cuántas lecturas salieron de Redis (hits) y cuántas fueron a PostgreSQL (misses)
@router.get("/stats")
def cacheStats(user=Depends(requireRoles(ADMIN_GLOBAL))):
    stats = getCacheStats()
    return stats


@router.delete("/stats")
def cacheStatsReset(user=Depends(requireRoles(ADMIN_GLOBAL))):
    resetCacheStats()
    stats = getCacheStats()
    return stats
