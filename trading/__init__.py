"""交易模块 — Futu 模拟/真实交易。物理隔离 Sim/Real DB。"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from .models import Base, TradingStrategy, VirtualAccount, VirtualPosition, TradeRecord

# Session factories per env (lazy init)
_sessions: dict[str, scoped_session] = {}


def _db_path(env: str) -> str:
    return f"/var/quant/trading/{env}/trading.db"


def init_db(env: str = "sim"):
    """初始化某个环境的交易数据库。返回 engine。"""
    db_path = _db_path(env)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return engine


def get_trading_session(env: str = "sim"):
    """获取环境专属的 DB session。每个 env 独立物理文件。"""
    if env not in _sessions:
        engine = init_db(env)
        factory = sessionmaker(bind=engine)
        _sessions[env] = scoped_session(factory)
    return _sessions[env]()
