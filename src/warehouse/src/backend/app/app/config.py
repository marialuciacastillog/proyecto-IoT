from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/warehouse_db"
    WAREHOUSE_ID: str = "WH-001"  # Identificador de esta instancia autónoma
    SYNC_QUEUE_URL: str = ""  # URL de la cola sync-queue (sale de terraform output)
    SYNC_TOPIC_ARN: str = ""  # Fase 3: tema SNS inventory-events (sale de terraform output)
    AWS_REGION: str = "us-east-1"
    REDIS_URL: str = "redis://localhost:6379/0"  # Redis de esta bodega
    CACHE_TTL_SECONDS: int = 30  # cuánto dura un dato en Redis antes de vencerse
    CACHE_ENABLED: bool = True  # en false la API no usa Redis (para comparar en el experimento)

    class Config:
        env_file = ".env"

settings = Settings()
