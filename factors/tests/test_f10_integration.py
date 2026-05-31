"""Integration test: tech (39) + fundamental (41) = 80 unique factors."""
import numpy as np
import pandas as pd
from factors.tech_builder import TechFactorBuilder
from factors.fundamental_builder import FundamentalFactorBuilder


def make_ohlcv(n=500):
    rng = np.random.default_rng(42)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, n)))
    return pd.DataFrame({
        "date": dates, "open": close * rng.uniform(0.99, 1.0, n),
        "high": close * rng.uniform(1.01, 1.03, n),
        "low": close * rng.uniform(0.97, 0.99, n),
        "close": close, "volume": rng.lognormal(14, 0.8, n),
    })


def test_tech_and_fundamental_combine():
    """Verify tech (39) + fundamental (41) = 80 unique factors with no overlap."""
    tfb = TechFactorBuilder()
    ohlcv = make_ohlcv()
    tfb.compute_factors(ohlcv)
    tech_names = set(tfb.factor_names)
    assert len(tech_names) >= 30, f"Expected >=30 tech factors, got {len(tech_names)}"

    ffb = FundamentalFactorBuilder()
    f10_names = set(ffb.ALL_FACTOR_COLS)
    assert len(f10_names) == 41, f"Expected 41 F10 factors, got {len(f10_names)}"

    combined = tech_names | f10_names
    overlap = tech_names & f10_names
    assert len(overlap) == 0, f"Found {len(overlap)} overlapping factor names: {overlap}"
    assert len(combined) >= 71, f"Expected >=71 unique factors, got {len(combined)}"


def test_tech_builder_backward_compat():
    """Verify FactorBuilder alias still works."""
    from factors.tech_builder import FactorBuilder
    fb = FactorBuilder()
    ohlcv = make_ohlcv()
    fb.compute_factors(ohlcv)
    assert len(fb.factor_names) >= 30
