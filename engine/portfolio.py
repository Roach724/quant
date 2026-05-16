from datetime import datetime
import pandas as pd


class Position:
    def __init__(self, symbol: str, entry_price: float = 0.0):
        self.symbol = symbol
        self.avg_entry = entry_price
        self.size = 0
        self._total_cost = 0.0
        self.realized_pnl = 0.0

    def add(self, size: int, price: float):
        new_total = self.size + size
        if new_total == 0:
            self.avg_entry = 0.0
            self._total_cost = 0.0
        else:
            self._total_cost += size * price
            self.avg_entry = self._total_cost / new_total
        self.size = new_total

    def unrealized_pnl(self, current_price: float) -> float:
        if self.size == 0:
            return 0.0
        return self.size * (current_price - self.avg_entry)

    def close(self, price: float):
        pnl = self.unrealized_pnl(price)
        self.realized_pnl += pnl
        self.size = 0
        self.avg_entry = 0.0
        self._total_cost = 0.0
        return pnl


class Portfolio:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self._equity: list[float] = []
        self._timestamps: list[datetime] = []

    @property
    def total_equity(self) -> float:
        return self.cash

    def _mark_to_market(self, bar_data):
        total = self.cash
        for sym, pos in self.positions.items():
            if pos.size != 0 and sym in bar_data.get("close", {}):
                total += pos.size * bar_data["close"][sym]
        return total

    def update(self, fills, bar_data):
        for fill in fills:
            if fill.order.side == "sell":
                self.cash += fill.price * fill.size - fill.commission
            else:
                self.cash -= fill.price * fill.size + fill.commission
            sym = fill.order.symbol
            if sym not in self.positions:
                self.positions[sym] = Position(symbol=sym)
            if fill.order.side == "sell":
                self.positions[sym].add(-fill.size, fill.price)
            else:
                self.positions[sym].add(fill.size, fill.price)

    def record_snapshot(self, ts):
        self._timestamps.append(ts)
        self._equity.append(self._mark_to_market({}))

    def mark_and_record(self, ts, bar_data):
        self._timestamps.append(ts)
        self._equity.append(self._mark_to_market(bar_data))

    @property
    def equity_curve(self) -> pd.Series:
        return pd.Series(self._equity, index=self._timestamps, name="equity")

    @property
    def returns(self) -> pd.Series:
        return self.equity_curve.pct_change().dropna()

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions and self.positions[symbol].size > 0
