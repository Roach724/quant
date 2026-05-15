from dataclasses import dataclass


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    slippage_bps: float = 5.0
    commission_bps: float = 1.0
    min_commission: float = 1.0
    benchmark_symbol: str = "SPY"
