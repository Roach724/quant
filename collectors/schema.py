# collectors/schema.py
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    market: str
    frequency: str


@dataclass
class Quote:
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    market: str


@dataclass
class Trade:
    symbol: str
    timestamp: datetime
    price: float
    size: int
    side: str
    market: str
