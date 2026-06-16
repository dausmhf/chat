from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

# Create engine (for PostgreSQL)
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Declarative Base
Base = declarative_base()

def get_db():
    """
    Dependency helper to get database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def check_db_connection() -> bool:
    """
    Checks if database connection is healthy.
    """
    try:
        # Simple test query
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"Database connection check failed: {str(e)}")
        return False
