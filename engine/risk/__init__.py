class RiskEngine:
    def __init__(self, rules=None):
        self.rules = rules or []

    def check(self, orders, portfolio, bar_data):
        result = list(orders)
        for rule in self.rules:
            result = rule.apply(result, portfolio, bar_data)
            if not result:
                break
        return result
