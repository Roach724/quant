"""Composite factor scoring — QARP (Quality At a Reasonable Price).

Computes cross-sectional composite scores combining value and quality
metrics from BigQuery F10 tables.
"""
from __future__ import annotations

import logging
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# Scoring weights: value (60%) + quality (40%)
_VALUE_WEIGHT = 0.60
_QUALITY_WEIGHT = 0.40


def _bq_to_strategy_symbol(bq_symbol: str) -> str:
    """Convert BQ format (US_AAPL, HK_00700) to strategy format (US.AAPL, HK.00700)."""
    if "_" in bq_symbol:
        parts = bq_symbol.split("_", 1)
        return f"{parts[0]}.{parts[1]}"
    return bq_symbol


def compute_qarp_scores(market: str = "us", symbols: Optional[list[str]] = None) -> dict[str, float]:
    """Compute QARP composite score for a market.

    Combines:
      - Value: PE, PS, PB (lower → higher score)
      - Quality: Morningstar star_rating (higher → higher score)

    Returns dict mapping strategy symbol → composite score (float).
    Higher score = better QARP candidate.
    """
    from google.cloud import bigquery

    client = bigquery.Client()
    table_prefix = f"{market}_"

    # ── 1. Valuation data (PE, PS, PB) ──
    val_scores: dict[str, dict[str, float]] = {}
    try:
        val_rows = client.query(f"""
            SELECT symbol, valuation_type,
                   FIRST_VALUE(value IGNORE NULLS) OVER (
                       PARTITION BY symbol, valuation_type ORDER BY date DESC
                   ) AS latest_value
            FROM quant.{table_prefix}valuation
            WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
              AND valuation_type IN ('pe', 'ps', 'pb')
              AND value > 0
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, valuation_type ORDER BY date DESC
            ) = 1
        """).result()
        for row in val_rows:
            sym = _bq_to_strategy_symbol(row.symbol)
            if symbols and sym not in symbols:
                continue
            if sym not in val_scores:
                val_scores[sym] = {}
            val_scores[sym][row.valuation_type] = float(row.latest_value)
    except Exception:
        logger.warning("QARP: valuation query failed", exc_info=True)
        return {}

    if not val_scores:
        return {}

    # ── 2. Quality data (Morningstar star_rating) ──
    quality_scores: dict[str, float] = {}
    try:
        q_rows = client.query(f"""
            SELECT symbol, star_rating
            FROM quant.{table_prefix}morningstar
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY star_update_time DESC
            ) = 1
        """).result()
        for row in q_rows:
            sym = _bq_to_strategy_symbol(row.symbol)
            if symbols and sym not in symbols:
                continue
            if row.star_rating is not None:
                quality_scores[sym] = float(row.star_rating)
    except Exception:
        logger.warning("QARP: morningstar query failed (non-fatal)", exc_info=True)

    # ── 3. Cross-sectional z-score per factor ──
    composite: dict[str, float] = {}
    all_symbols = list(val_scores.keys())

    # Valuation factors (invert: lower PE/PS/PB = better)
    for factor in ("pe", "ps", "pb"):
        vals = {s: val_scores[s].get(factor) for s in all_symbols}
        vals = {s: v for s, v in vals.items() if v is not None and v > 0}
        if len(vals) < 3:
            continue
        arr = np.array(list(vals.values()), dtype=float)
        mu = np.mean(arr)
        std = np.std(arr)
        if std == 0:
            continue
        for s, v in vals.items():
            z = (v - mu) / std
            composite[s] = composite.get(s, 0.0) - z * _VALUE_WEIGHT / 3  # invert + weight

    # Quality factor (higher star_rating = better)
    if quality_scores:
        q_vals = {s: quality_scores.get(s) for s in all_symbols}
        q_vals = {s: v for s, v in q_vals.items() if v is not None}
        if len(q_vals) >= 3:
            arr = np.array(list(q_vals.values()), dtype=float)
            mu = np.mean(arr)
            std = np.std(arr)
            if std > 0:
                for s, v in q_vals.items():
                    z = (v - mu) / std
                    composite[s] = composite.get(s, 0.0) + z * _QUALITY_WEIGHT

    return composite


def compute_short_squeeze_scores(market: str = "hk", symbols: Optional[set[str]] = None) -> dict[str, float]:
    """Compute short squeeze potential scores.

    Combines:
      - Short ratio (high short volume / total volume → higher score)
      - Recent momentum (ret_5d from factor_values → higher score)

    Returns dict mapping strategy symbol → squeeze score.
    Higher = more squeeze potential.
    """
    from google.cloud import bigquery

    client = bigquery.Client()

    # ── 1. Latest daily short ratio per symbol ──
    short_ratios: dict[str, float] = {}
    try:
        rows = client.query(f"""
            SELECT symbol,
                   SAFE_DIVIDE(short_sell_shares_traded, shares_traded) AS ratio,
                   short_sell_shares_traded, shares_traded
            FROM quant.{market}_daily_short_volume
            WHERE shares_traded > 0 AND short_sell_shares_traded > 0
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY timestamp DESC
            ) = 1
        """).result()
        for row in rows:
            sym = _bq_to_strategy_symbol(row.symbol)
            if symbols and sym not in symbols:
                continue
            if row.ratio is not None and row.ratio > 0:
                short_ratios[sym] = float(row.ratio)
    except Exception:
        logger.warning("ShortSqueeze: short volume query failed", exc_info=True)
        return {}

    if not short_ratios:
        return {}

    # ── 2. Recent momentum (ret_5d) from factor_values ──
    momentum: dict[str, float] = {}
    try:
        rows = client.query(f"""
            SELECT symbol, value
            FROM quant.factor_values
            WHERE factor_id = 'ret_5d' AND symbol LIKE '{market.upper()}.%'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY date DESC
            ) = 1
        """).result()
        for row in rows:
            sym = _bq_to_strategy_symbol(row.symbol)
            if symbols and sym not in symbols:
                continue
            if row.value is not None and not (isinstance(row.value, float) and row.value != row.value):
                momentum[sym] = float(row.value)
    except Exception:
        logger.warning("ShortSqueeze: momentum query failed (non-fatal)", exc_info=True)

    # ── 3. Composite: short_ratio z-score + momentum z-score ──
    composite: dict[str, float] = {}

    # Short ratio (higher = more shorting = more squeeze potential)
    sr_symbols = list(short_ratios.keys())
    if len(sr_symbols) >= 3:
        arr = np.array([short_ratios[s] for s in sr_symbols], dtype=float)
        mu = np.mean(arr)
        std = np.std(arr)
        if std > 0:
            for s in sr_symbols:
                composite[s] = composite.get(s, 0.0) + 0.7 * (short_ratios[s] - mu) / std

    # Momentum (higher = more squeeze potential — price rising against shorts)
    if momentum:
        mom_symbols = [s for s in sr_symbols if s in momentum]
        if len(mom_symbols) >= 3:
            arr = np.array([momentum[s] for s in mom_symbols], dtype=float)
            mu = np.mean(arr)
            std = np.std(arr)
            if std > 0:
                for s in mom_symbols:
                    composite[s] = composite.get(s, 0.0) + 0.3 * (momentum[s] - mu) / std

    return composite
