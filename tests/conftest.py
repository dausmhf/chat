import pytest
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.storage.database import Base
from app.storage import models

@pytest.fixture(scope="function")
def test_db():
    # Use SQLite in-memory for unit testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    
    # We create tables
    # Note: SQLite does not support PostgreSQL custom ENUMs natively or pgvector directly,
    # so we mock or replace columns dynamically if needed, but standard Column(String) maps fine.
    Base.metadata.create_all(bind=engine)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    # Setup default client for testing
    client = models.Client(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        client_code="travel_alfalah",
        name="Test Travel Client",
        brand_name="Travel Al-Falah"
    )
    db.add(client)
    db.commit()
    
    # Setup package for testing
    package = models.Package(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        client_id=client.id,
        package_code="OCT12D",
        package_name="Umroh Oktober 12 Hari",
        price_per_pax=29000000,
        currency="IDR",
        quota=45,
        remaining_seat=12,
        status="active"
    )
    db.add(package)
    db.commit()
    
    yield db
    
    db.close()
    Base.metadata.drop_all(bind=engine)
