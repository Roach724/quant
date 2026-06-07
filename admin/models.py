"""SQLAlchemy models for Quant Admin Platform."""

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase, sessionmaker, scoped_session
from datetime import datetime, timezone

DB_PATH = "/var/quant/admin.db"
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, pool_size=5, max_overflow=10)

_session_factory = sessionmaker(bind=engine)
SessionLocal = scoped_session(_session_factory)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(50), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    params = Column(JSON, nullable=True)
    result = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class MlDataset(Base):
    __tablename__ = "ml_datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False)
    market = Column(String(10), nullable=False)
    label = Column(String(50), nullable=False)
    factor_ids = Column(Text, nullable=False)
    train_start = Column(String(20), nullable=False)
    train_end = Column(String(20), nullable=False)
    val_start = Column(String(20), nullable=False)
    val_end = Column(String(20), nullable=False)
    test_start = Column(String(20), nullable=False)
    test_end = Column(String(20), nullable=False)
    bq_table = Column(String(200), nullable=True)
    status = Column(String(20), default="registered")
    row_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MlConfig(Base):
    __tablename__ = "ml_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False)
    description = Column(String(500), nullable=True)
    config_path = Column(String(500), nullable=False)
    dataset_name = Column(String(200), nullable=True)
    registry_model_name = Column(String(200), nullable=True)
    status = Column(String(20), default="draft")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    """Return a scoped session. Auto-cleaned per-request."""
    return SessionLocal()


def cleanup_session():
    """Remove scoped session after request (called by middleware/lifespan)."""
    SessionLocal.remove()
