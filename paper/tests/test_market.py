"""Tests for paper.market — market-hours logic and schedule definitions."""

import pytest
from datetime import datetime, timezone

from paper.market import (
    MARKET_SCHEDULES,
    MARKET_US,
    MARKET_HK,
    MARKET_CRYPTO,
    is_market_open,
    trading_hours_for,
    default_symbols_for,
)


class TestMarketSchedules:
    def test_us_schedule_exists(self):
        assert "us" in MARKET_SCHEDULES
        s = MARKET_SCHEDULES["us"]
        assert s["currency"] == "USD"
        assert s["min_commission"] == 1.0

    def test_hk_schedule_exists(self):
        assert "hk" in MARKET_SCHEDULES
        s = MARKET_SCHEDULES["hk"]
        assert s["currency"] == "HKD"
        assert s["min_commission"] == 15.0
        assert "lunch_start" in s

    def test_crypto_schedule_exists(self):
        assert "crypto" in MARKET_SCHEDULES
        s = MARKET_SCHEDULES["crypto"]
        assert s["currency"] == "USDT"
        assert s["weekdays_only"] is False


class TestIsMarketOpen:
    # ── US market (UTC 13:30–20:00 on weekdays) ──

    def test_us_market_open_mid_session(self):
        """Tuesday 15:00 UTC → open."""
        dt = datetime(2024, 6, 11, 15, 0, tzinfo=timezone.utc)  # Tue
        assert is_market_open("us", dt) is True

    def test_us_market_before_open(self):
        """Tuesday 10:00 UTC → closed."""
        dt = datetime(2024, 6, 11, 10, 0, tzinfo=timezone.utc)
        assert is_market_open("us", dt) is False

    def test_us_market_after_close(self):
        """Tuesday 22:00 UTC → closed."""
        dt = datetime(2024, 6, 11, 22, 0, tzinfo=timezone.utc)
        assert is_market_open("us", dt) is False

    def test_us_market_saturday(self):
        """Saturday → closed (even at 15:00 UTC)."""
        dt = datetime(2024, 6, 8, 15, 0, tzinfo=timezone.utc)  # Sat
        assert is_market_open("us", dt) is False

    def test_us_market_sunday(self):
        """Sunday → closed."""
        dt = datetime(2024, 6, 9, 15, 0, tzinfo=timezone.utc)
        assert is_market_open("us", dt) is False

    def test_us_market_monday(self):
        """Monday at 15:00 UTC → open."""
        dt = datetime(2024, 6, 10, 15, 0, tzinfo=timezone.utc)
        assert is_market_open("us", dt) is True

    # ── HK market (UTC 1:30–8:00, lunch 4:00–5:00 on weekdays) ──

    def test_hk_market_open_morning(self):
        """Tuesday 3:00 UTC → open (morning session)."""
        dt = datetime(2024, 6, 11, 3, 0, tzinfo=timezone.utc)
        assert is_market_open("hk", dt) is True

    def test_hk_market_lunch_closed(self):
        """Tuesday 4:30 UTC → closed (lunch)."""
        dt = datetime(2024, 6, 11, 4, 30, tzinfo=timezone.utc)
        assert is_market_open("hk", dt) is False

    def test_hk_market_afternoon(self):
        """Tuesday 6:00 UTC → open (afternoon session)."""
        dt = datetime(2024, 6, 11, 6, 0, tzinfo=timezone.utc)
        assert is_market_open("hk", dt) is True

    def test_hk_market_after_close(self):
        """Tuesday 10:00 UTC → closed."""
        dt = datetime(2024, 6, 11, 10, 0, tzinfo=timezone.utc)
        assert is_market_open("hk", dt) is False

    def test_hk_market_before_open(self):
        """Tuesday 0:30 UTC → closed (before 1:30 open)."""
        dt = datetime(2024, 6, 11, 0, 30, tzinfo=timezone.utc)
        assert is_market_open("hk", dt) is False

    def test_hk_market_saturday(self):
        """Saturday → closed."""
        dt = datetime(2024, 6, 8, 3, 0, tzinfo=timezone.utc)
        assert is_market_open("hk", dt) is False

    # ── Crypto market (always open) ──

    def test_crypto_always_open_weekday(self):
        dt = datetime(2024, 6, 11, 10, 0, tzinfo=timezone.utc)
        assert is_market_open("crypto", dt) is True

    def test_crypto_always_open_weekend(self):
        dt = datetime(2024, 6, 8, 10, 0, tzinfo=timezone.utc)  # Sat
        assert is_market_open("crypto", dt) is True

    def test_crypto_always_open_midnight(self):
        dt = datetime(2024, 6, 11, 0, 0, tzinfo=timezone.utc)
        assert is_market_open("crypto", dt) is True

    # ── Midnight timestamps (daily bars) ──

    def test_us_midnight_is_open_weekday(self):
        """Daily bar at 00:00 should be open on a weekday."""
        dt = datetime(2024, 6, 11, 0, 0, tzinfo=timezone.utc)  # Tue
        assert is_market_open("us", dt) is True

    def test_us_midnight_is_closed_weekend(self):
        """Daily bar at 00:00 should be closed on Saturday."""
        dt = datetime(2024, 6, 8, 0, 0, tzinfo=timezone.utc)  # Sat
        assert is_market_open("us", dt) is False

    def test_hk_midnight_is_open_weekday(self):
        """Daily HK bar at 00:00 should be open on a weekday."""
        dt = datetime(2024, 6, 11, 0, 0, tzinfo=timezone.utc)  # Tue
        assert is_market_open("hk", dt) is True

    # ── Edge cases ──

    def test_unknown_market_always_open(self):
        dt = datetime(2024, 6, 8, 0, 0, tzinfo=timezone.utc)  # Sat midnight
        assert is_market_open("xyz", dt) is True

    def test_us_market_open_exact_open(self):
        dt = datetime(2024, 6, 11, 13, 30, tzinfo=timezone.utc)  # Tue 13:30 UTC
        assert is_market_open("us", dt) is True

    def test_us_market_closed_exact_close(self):
        dt = datetime(2024, 6, 11, 20, 0, tzinfo=timezone.utc)
        assert is_market_open("us", dt) is True

    def test_us_market_closed_one_min_after_close(self):
        dt = datetime(2024, 6, 11, 20, 1, tzinfo=timezone.utc)
        assert is_market_open("us", dt) is False


class TestTradingHoursFor:
    def test_us(self):
        info = trading_hours_for("us")
        assert info["market"] == "us"
        assert info["currency"] == "USD"
        assert info["utc_open"] == "13:30"

    def test_hk(self):
        info = trading_hours_for("hk")
        assert info["currency"] == "HKD"

    def test_unknown(self):
        info = trading_hours_for("foobar")
        assert info["status"] == "unknown"


class TestDefaultSymbolsFor:
    def test_us_has_symbols(self):
        syms = default_symbols_for("us")
        assert len(syms) >= 4
        assert "AAPL" in syms

    def test_hk_has_symbols(self):
        syms = default_symbols_for("hk")
        assert len(syms) >= 4
        assert "0700" in syms

    def test_crypto_has_symbols(self):
        syms = default_symbols_for("crypto")
        assert len(syms) >= 4
        assert "BTC" in syms

    def test_unknown_empty(self):
        assert default_symbols_for("nothing") == []
