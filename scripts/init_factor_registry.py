#!/usr/bin/env python3.12
"""Seed the factor_registry BigQuery table with existing FactorBuilder factors.

Run once:
    python3.12 scripts/init_factor_registry.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from factors.builder import FactorBuilder
from factors.registry import FactorRegistry

CATEGORY_MAP = {
    # Specific prefixes must come BEFORE general ones (e.g. vol_ratio before vol_)
    "ret_": "return",
    "vol_ratio": "volume",
    "vol_trend": "volume",
    "corr_vp": "volume",
    "vol_": "volatility",
    "rsi": "momentum",
    "macd": "momentum",
    "bb_": "momentum",
    "price_position": "momentum",
    "streak": "momentum",
    "avg_turnover": "turnover",
    "turnover_": "turnover",
    "daily_range": "price_pattern",
    "upper_shadow": "price_pattern",
    "lower_shadow": "price_pattern",
    "gap": "price_pattern",
    "vp_divergence": "price_pattern",
    "low_vol_proxy": "volatility",
    "price_stability": "momentum",
    "skew": "higher_moment",
    "kurt": "higher_moment",
    "hk_": "hk_specific",
}


def classify_factor(name: str) -> str:
    """Classify factor name into a category based on prefix."""
    for prefix, category in CATEGORY_MAP.items():
        if name.startswith(prefix):
            return category
    return "other"


def main():
    # Initialize FactorBuilder and compute to populate factor_names
    fb = FactorBuilder()
    dummy = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=100, freq="B"),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000000,
    })
    fb.compute_factors(dummy)

    print(f"Found {len(fb.factor_names)} factors to register")

    registry = FactorRegistry()
    registered = 0
    for name in fb.factor_names:
        category = classify_factor(name)
        ok = registry.register(
            factor_id=f"us_{name}",
            name=name.replace("_", " ").title(),
            market="us",
            source="Alpha158",
            formula=f"factors/builder.py::FactorBuilder.compute_factors (group: {category})",
            category=category,
            description=f"Auto-registered from FactorBuilder. Category: {category}",
        )
        if ok:
            registered += 1

    print(f"Registered {registered}/{len(fb.factor_names)} factors successfully")


if __name__ == "__main__":
    main()
