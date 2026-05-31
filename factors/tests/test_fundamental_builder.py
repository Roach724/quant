import numpy as np
import pandas as pd
from factors.fundamental_builder import FundamentalFactorBuilder


def test_builder_has_41_factors():
    fb = FundamentalFactorBuilder()
    assert len(fb.ALL_FACTOR_COLS) == 41


def test_compute_returns_dataframe():
    fb = FundamentalFactorBuilder()
    financials = pd.DataFrame({
        "roe": [0.25, 0.26, 0.24],
        "roa": [0.15, 0.16, 0.14],
        "gross_margin": [0.40, 0.41, 0.39],
    })
    data_map = {"financials": financials}
    result = fb.compute(["roe", "roa"], data_map)
    assert isinstance(result, pd.DataFrame)
    assert "roe" in result.columns
    assert len(result) == 3


def test_compute_empty_data():
    fb = FundamentalFactorBuilder()
    result = fb.compute(["roe"], {"financials": pd.DataFrame()})
    assert result.empty


def test_process_factors_standardizes():
    fb = FundamentalFactorBuilder()
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=100),
        "roe": rng.normal(0.25, 0.10, 100),
        "pe_percentile": rng.uniform(0, 1, 100),
    })
    processed = fb.process_factors(df)
    assert abs(processed["roe"].mean()) < 0.1
    assert 0.8 < processed["roe"].std() < 1.2
