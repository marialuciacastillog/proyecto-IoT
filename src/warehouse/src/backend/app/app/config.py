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
    # Reservas con vencimiento
    SALE_RESERVATION_HOURS: int = 48          # una reserva de venta dura 48 horas
    MANUAL_RESERVATION_MAX_HOURS: int = 168   # una reserva manual dura máximo 7 días
    TRANSFER_ALERT_HOURS: int = 24            # si una transferencia sigue apartada 24 horas, se avisa
    SWEEP_SECONDS: int = 60                   # cada cuánto revisa el vigilante de la bodega

    class Config:
        env_file = ".env"

settings = Settings()
