from engine.config import BacktestConfig


def test_default_config():
    cfg = BacktestConfig()
    assert cfg.initial_capital == 100_000
    assert cfg.slippage_bps == 5
    assert cfg.commission_bps == 1
    assert cfg.min_commission == 1.0


def test_custom_config():
    cfg = BacktestConfig(initial_capital=50_000, slippage_bps=10)
    assert cfg.initial_capital == 50_000
    assert cfg.slippage_bps == 10
