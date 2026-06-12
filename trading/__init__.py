"""交易模块 — Futu 模拟/真实交易"""

from .models import Base, TradingStrategy, VirtualAccount, VirtualPosition, TradeRecord


def init_db(db_path: str = "/var/quant/trading/trading.db"):
    """初始化交易数据库"""
    import os
    from sqlalchemy import create_engine

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return engine
