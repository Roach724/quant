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
        elif size > 0:
            # Buy: accumulate cost, recalculate average entry
            self._total_cost += size * price
            self.avg_entry = self._total_cost / new_total
        else:
            # Sell: reduce cost proportionally, avg_entry unchanged
            if self.size > 0:
                self._total_cost += size * self.avg_entry  # size < 0
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
        self._peak_equity = initial_capital
        self.positions: dict[str, Position] = {}
        self._last_prices: dict[str, float] = {}
        self._equity: list[float] = []
        self._timestamps: list[datetime] = []

    @property
    def drawdown(self) -> float:
        """Current drawdown from peak equity (negative = underwater)."""
        current = self._mark_to_market({})
        if self._peak_equity > 0:
            return (current - self._peak_equity) / self._peak_equity
        return 0.0

    @property
    def total_equity(self) -> float:
        """Total account equity = cash + mark-to-market value of all positions."""
        return self._mark_to_market({})

    def _mark_to_market(self, bar_data):
        total = self.cash
        close_data = bar_data.get("close", {})
        for sym, pos in self.positions.items():
            if pos.size == 0:
                continue
            if sym in close_data:
                total += pos.size * close_data[sym]
            else:
                total += pos.size * self._last_prices.get(sym, 0.0)
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
        close_data = bar_data.get("close", {})
        for sym, price in close_data.items():
            self._last_prices[sym] = float(price)
        current = self._mark_to_market(bar_data)
        if current > self._peak_equity:
            self._peak_equity = current
        self._timestamps.append(ts)
        self._equity.append(current)

    @property
    def equity_curve(self) -> pd.Series:
        return pd.Series(self._equity, index=self._timestamps, name="equity")

    @property
    def returns(self) -> pd.Series:
        return self.equity_curve.pct_change().dropna()

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions and self.positions[symbol].size > 0
