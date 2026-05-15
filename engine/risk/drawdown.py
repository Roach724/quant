class MaxDrawdown:
    def __init__(self, limit: float = 0.20):
        self.limit = limit

    def apply(self, orders, portfolio, bar_data):
        if portfolio.initial_capital > 0:
            dd = (portfolio.total_equity - portfolio.initial_capital) / portfolio.initial_capital
            if dd < -self.limit:
                return []
        return orders
