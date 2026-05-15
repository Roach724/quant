class VolatilityTarget:
    def __init__(self, annual: float = 0.15):
        self.annual = annual

    def apply(self, orders, portfolio, bar_data):
        return orders
