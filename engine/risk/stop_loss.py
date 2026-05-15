from engine.orders import Order


class StopLoss:
    def __init__(self, pct: float = 0.05, scope: str = "position"):
        self.pct = pct
        self.scope = scope

    def apply(self, orders, portfolio, bar_data):
        result = list(orders)
        close_prices = bar_data.get("close", {})
        for sym, pos in portfolio.positions.items():
            if pos.size <= 0 or sym not in close_prices:
                continue
            pnl_pct = (close_prices[sym] - pos.avg_entry) / pos.avg_entry if pos.avg_entry > 0 else 0
            if pnl_pct < -self.pct:
                result.append(Order(symbol=sym, side="sell", size=pos.size))
        return result
