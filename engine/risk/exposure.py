class ExposureLimit:
    def __init__(self, max_pct: float = 0.25):
        self.max_pct = max_pct

    def apply(self, orders, portfolio, bar_data):
        result = []
        close_prices = bar_data.get("close", {})
        total_equity = portfolio.total_equity
        for order in orders:
            sym = order.symbol
            if sym not in close_prices:
                result.append(order)
                continue
            current = 0
            if sym in portfolio.positions and hasattr(portfolio.positions[sym], 'size'):
                current = portfolio.positions[sym].size * close_prices[sym]
            proposed = order.size * close_prices[sym]
            new_pct = (current + proposed) / total_equity if total_equity > 0 else 0
            if new_pct <= self.max_pct:
                result.append(order)
        return result


class MaxLeverage:
    """Limit net exposure (long - short) as multiple of equity."""
    def __init__(self, limit: float = 1.5):
        self.limit = limit

    def apply(self, orders, portfolio, bar_data):
        close_prices = bar_data.get("close", {})
        net = 0
        for sym, p in portfolio.positions.items():
            if hasattr(p, 'size'):
                net += p.size * close_prices.get(sym, 0)
        for o in orders:
            sign = 1 if o.side == "buy" else -1
            net += sign * o.size * close_prices.get(o.symbol, 0)
        if portfolio.total_equity > 0 and abs(net) / portfolio.total_equity > self.limit:
            return []
        return orders


class GrossExposureLimit:
    """Limit total gross exposure (long + short) as multiple of equity."""
    def __init__(self, limit: float = 2.0):
        self.limit = limit

    def apply(self, orders, portfolio, bar_data):
        close_prices = bar_data.get("close", {})
        gross = 0
        for sym, p in portfolio.positions.items():
            if hasattr(p, 'size'):
                gross += abs(p.size * close_prices.get(sym, 0))
        for o in orders:
            gross += abs(o.size * close_prices.get(o.symbol, 0))
        if portfolio.total_equity > 0 and gross / portfolio.total_equity > self.limit:
            return []
        return orders


class SectorCap:
    def __init__(self, per_sector: float = 0.30, sectors=None):
        self.per_sector = per_sector
        self.sectors = sectors or {}

    def apply(self, orders, portfolio, bar_data):
        raise NotImplementedError("SectorCap.apply is not yet implemented")
