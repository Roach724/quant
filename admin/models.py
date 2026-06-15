"""SQLAlchemy models for Quant Admin Platform."""

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase, sessionmaker, scoped_session
from datetime import datetime, timezone

DB_PATH = "/var/data/admin.db"
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
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


class AiStrategy(Base):
    """AI Decision Engine strategy instance (one per configured strategy)."""
    __tablename__ = "ai_strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False)
    market = Column(String(10), nullable=False, default="us")
    enabled = Column(Integer, nullable=False, default=0)  # 0=disabled, 1=enabled
    config_yaml = Column(Text, nullable=False)
    cron_schedule = Column(String(100), nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String(20), nullable=True)  # success | failed | running
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AiDecisionRun(Base):
    """Single execution record of the AI Decision Engine."""
    __tablename__ = "ai_decision_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="running")  # running | success | failed
    recall_result = Column(JSON, nullable=True)
    analysis_result = Column(JSON, nullable=True)
    fusion_result = Column(JSON, nullable=True)
    decision_result = Column(JSON, nullable=True)
    summary = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)


class AiDecisionConfig(Base):
    """YAML config template for AI Decision Engine strategies."""
    __tablename__ = "ai_decision_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False)
    market = Column(String(10), nullable=False, default="us")
    description = Column(String(500), nullable=True)
    config_yaml = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CronRun(Base):
    """Unified run history for all cron jobs — manual and scheduled."""
    __tablename__ = "cron_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String(200), nullable=False, index=True)
    command = Column(Text, nullable=True)
    trigger_type = Column(String(20), nullable=False, default="scheduled")  # "manual" | "scheduled"
    status = Column(String(20), nullable=False, default="running")  # "running" | "success" | "failed" | "skipped"
    exit_code = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    log_file = Column(String(500), nullable=True)
    error_tail = Column(String(500), nullable=True)  # last 500 chars of error output


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    """Return a scoped session. Auto-cleaned per-request."""
    return SessionLocal()


def cleanup_session():
    """Remove scoped session after request (called by middleware/lifespan)."""
    SessionLocal.remove()
