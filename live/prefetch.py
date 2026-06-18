"""Prefetch historical bars from BigQuery for strategy lookback warmup.

Used by both live/runner.py and trading/runner.py to pre-fill the
_live_bars buffer on restart, so strategies with lookback requirements
(SimpleMomentum, MultiFactorRank, etc.) can trade immediately without
waiting N real-time bars.

Returns bar_data in the same format as BQDataSource._poll:
    [{"timestamp": ..., "close": {sym: price}, "open": ..., ...}, ...]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def prefetch_bars(
    symbols: list[str],
    market: str,
    n_bars: int,
    project: str = "deductive-notch-495015-c2",
) -> list[dict]:
    """Fetch the most recent n_bars 5-minute bars from BigQuery.

    Parameters
    ----------
    symbols : list[str]
        Canonical bare symbols (e.g. ["AAPL", "MSFT"] or ["00005", "09988"]).
    market : str
        "us", "hk", or "crypto".
    n_bars : int
        Number of most-recent bars to fetch.
    project : str
        GCP project ID.

    Returns
    -------
    list[dict]
        Bar data dicts sorted oldest-first, each containing:
        {"timestamp", "close", "open", "high", "low", "volume"}.
        Returns empty list on failure.
    """
    if n_bars <= 0 or not symbols:
        return []

    try:
        from google.cloud import bigquery
        import pandas as pd
        from common.normalize import queryize_symbol, normalize_symbol
    except ImportError:
        logger.error("prefetch_bars: missing dependencies")
        return []

    table = {"us": "us_bars_5m", "hk": "hk_bars_5m", "crypto": "crypto_bars_5m"}[market]
    query_symbols = [queryize_symbol(s, market) for s in symbols]

    # Get the Nth most recent distinct timestamp as the cutoff
    query = f"""
        WITH recent_ts AS (
            SELECT DISTINCT timestamp
            FROM `{project}.quant.{table}`
            WHERE symbol IN UNNEST(@symbols)
            ORDER BY timestamp DESC
            LIMIT @n_bars
        )
        SELECT t.symbol, t.timestamp, t.open, t.high, t.low, t.close, t.volume
        FROM `{project}.quant.{table}` t
        JOIN recent_ts r ON t.timestamp = r.timestamp
        WHERE t.symbol IN UNNEST(@symbols)
        ORDER BY t.timestamp ASC, t.symbol
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("symbols", "STRING", query_symbols),
            bigquery.ScalarQueryParameter("n_bars", "INT64", n_bars),
        ],
    )

    try:
        client = bigquery.Client(project=project)
        df = client.query(query, job_config=job_config).to_dataframe()
    except Exception:
        logger.exception("prefetch_bars: BQ query failed")
        return []

    if df.empty:
        logger.warning("prefetch_bars: no data returned (n_bars=%d)", n_bars)
        return []

    # Pivot into per-timestamp bar_data dicts (same format as BQDataSource._poll)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    bars: list[dict] = []

    for ts, group in df.groupby("timestamp", sort=True):
        bar_data: dict = {
            "close": {},
            "open": {},
            "high": {},
            "low": {},
            "volume": {},
            "timestamp": ts,
        }
        for _, row in group.iterrows():
            sym = normalize_symbol(str(row["symbol"]), market)
            bar_data["close"][sym] = float(row["close"])
            bar_data["open"][sym] = float(row["open"])
            bar_data["high"][sym] = float(row["high"])
            bar_data["low"][sym] = float(row["low"])
            bar_data["volume"][sym] = float(row["volume"])
        bars.append(bar_data)

    logger.info(
        "prefetch_bars: loaded %d bars (%d symbols, market=%s)",
        len(bars), len(symbols), market,
    )
    return bars
