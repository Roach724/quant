"""交易模块数据模型 — SQLAlchemy ORM"""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TradingStrategy(Base):
    """注册的策略配置"""

    __tablename__ = "trading_strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)
    market = Column(String(8), nullable=False)  # us / hk
    strategy_class = Column(String(64), nullable=False)  # SimpleMomentum, MACD, etc.
    capital_allocated = Column(Float, nullable=False)  # 分配资金
    config_yaml = Column(Text, nullable=False)  # 策略参数 YAML
    status = Column(String(16), default="stopped")  # running / stopped / paused
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class VirtualAccount(Base):
    """虚拟子账户 — 每个策略一个，资金隔离"""

    __tablename__ = "trading_virtual_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    cash = Column(Float, nullable=False, default=0.0)
    initial_capital = Column(Float, nullable=False)
    peak_equity = Column(Float, nullable=False, default=0.0)
    total_commission = Column(Float, nullable=False, default=0.0)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class VirtualPosition(Base):
    """虚拟持仓 — 策略级别的持仓记录"""

    __tablename__ = "trading_virtual_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)  # LONG / SHORT
    qty = Column(Integer, nullable=False)
    avg_entry_price = Column(Float, nullable=False)
    market_value = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class TradeRecord(Base):
    """交易记录 — 每笔成交"""

    __tablename__ = "trading_trade_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)  # BUY / SELL
    qty = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    commission = Column(Float, nullable=False, default=0.0)
    order_id = Column(String(64))
    signal_info = Column(JSON)  # 原始信号信息
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
