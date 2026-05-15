from typing import Protocol


class RiskRule(Protocol):
    def apply(self, orders, portfolio, bar_data) -> list:
        ...
