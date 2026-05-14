from datetime import datetime, timezone
import pandas as pd
from quality.main import check_completeness, check_sanity


def test_check_completeness_all_present():
    df = pd.DataFrame({
        "symbol": ["AAPL"] * 390,
        "timestamp": pd.date_range("2026-05-13 14:30", periods=390, freq="1min", tz="UTC"),
        "open": [100.0] * 390,
        "high": [101.0] * 390,
        "low": [99.0] * 390,
        "close": [100.5] * 390,
        "volume": [1000] * 390,
        "market": ["US"] * 390,
        "frequency": ["1m"] * 390,
    })
    issues = check_completeness(df, expected_bars=390)
    assert len(issues) == 0


def test_check_completeness_missing_bars():
    df = pd.DataFrame({
        "symbol": ["AAPL"] * 100,
        "timestamp": pd.date_range("2026-05-13 14:30", periods=100, freq="1min", tz="UTC"),
        "open": [100.0] * 100, "high": [101.0] * 100, "low": [99.0] * 100,
        "close": [100.5] * 100, "volume": [1000] * 100,
        "market": ["US"] * 100, "frequency": ["1m"] * 100,
    })
    issues = check_completeness(df, expected_bars=390)
    assert len(issues) > 0
    assert "expected 390" in issues[0].lower()


def test_check_sanity_high_less_than_low():
    df = pd.DataFrame({
        "symbol": ["AAPL"],
        "timestamp": [datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)],
        "open": [100.0], "high": [90.0],
        "low": [95.0], "close": [97.0], "volume": [1000],
        "market": ["US"], "frequency": ["1m"],
    })
    issues = check_sanity(df)
    assert len(issues) > 0
    assert any("high < low" in issue.lower() for issue in issues)


def test_check_sanity_negative_price():
    df = pd.DataFrame({
        "symbol": ["AAPL"],
        "timestamp": [datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)],
        "open": [-5.0], "high": [100.0], "low": [95.0], "close": [97.0],
        "volume": [1000], "market": ["US"], "frequency": ["1m"],
    })
    issues = check_sanity(df)
    assert len(issues) > 0
