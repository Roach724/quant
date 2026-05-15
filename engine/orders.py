from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class Order:
    symbol: str
    side: Literal["buy", "sell"]
    size: int
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None


@dataclass
class Fill:
    order: Order
    price: float
    size: int
    slippage: float
    commission: float
    timestamp: datetime | None = None


def simulate_fills(orders, bar_data, config):
    fills = []
    for order in orders:
        mid = bar_data["close"][order.symbol]
        slip = config.slippage_bps / 10000 * mid
        comm = max(
            config.min_commission,
            config.commission_bps / 10000 * order.size * mid,
        )
        exec_price = mid + slip if order.side == "buy" else mid - slip
        fills.append(Fill(
            order=order, price=exec_price, size=order.size,
            slippage=slip, commission=comm,
        ))
    return fills
