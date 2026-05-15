import pandas as pd
import numpy as np
from engine.factors import Factor, compute_ic, factor_returns
from engine.data import DataFrameSource


def make_data():
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=252, freq="D")
    close = pd.DataFrame({
        "AAPL": 100 + np.cumsum(np.random.randn(252) * 1.5 + 0.02),
        "MSFT": 300 + np.cumsum(np.random.randn(252) * 1.5 - 0.01),
        "GOOGL": 150 + np.cumsum(np.random.randn(252) * 1.5 + 0.005),
    }, index=dates)
    return DataFrameSource(close=close)


def test_factor_definition():
    momentum = Factor("momentum", lambda df: df.pct_change(20))
    assert momentum.name == "momentum"
    assert callable(momentum.fn)


def test_compute_ic():
    data = make_data()
    momentum = Factor("momentum", lambda df: df.pct_change(20))
    volatility = Factor("volatility", lambda df: df.pct_change().rolling(20).std())

    fwd_ret = data.close.pct_change(5).shift(-5)
    results = compute_ic([momentum, volatility], fwd_ret, data)

    assert len(results) >= 1
    for name, ic_series in results.items():
        assert isinstance(ic_series, pd.Series)


def test_factor_returns():
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=100, freq="D")
    port_ret = pd.Series(np.random.randn(100) * 0.01, index=dates, name="portfolio")
    exposures = pd.DataFrame({
        "momentum": np.random.randn(100),
        "volatility": np.abs(np.random.randn(100)),
    }, index=dates)

    result = factor_returns(port_ret, exposures)
    assert "alpha" in result
    assert "betas" in result
    assert "r_squared" in result
    assert len(result["betas"]) == 2
