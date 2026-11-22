from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/warehouse_db"
    WAREHOUSE_ID: str = "WH-001"  # Identificador de esta instancia autónoma
    SYNC_QUEUE_URL: str = ""  # URL de la cola sync-queue (sale de terraform output)
    AWS_REGION: str = "us-east-1"

    class Config:
        env_file = ".env"

settings = Settings()
