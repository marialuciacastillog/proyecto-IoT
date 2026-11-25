from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

# La sesión de la base trabaja en UTC: así las fechas se guardan y se leen igual en cualquier máquina
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, connect_args={"options": "-c timezone=utc"})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
