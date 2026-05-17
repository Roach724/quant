"""Quantitative trading data SDK.

Provides a clean interface for querying market data from research notebooks.

Usage:
    import quant.data as qd
    df = qd.bars("AAPL", "2026-05-01", "2026-05-13")
"""

from quant.client import QuantClient
from quant.direct import bars_direct

_client = None


def _get_client():
    global _client
    if _client is None:
        api_url = __import__("os").environ.get("QUANT_API_URL", "http://localhost:8080")
        _client = QuantClient(base_url=api_url)
    return _client


def bars(
    symbols,
    start,
    end,
    market="us",
    frequency="1m",
    source="api",
    cache_dir=None,
):
    """Fetch OHLCV bars.

    Args:
        symbols: Ticker(s), e.g. "AAPL" or ["AAPL", "MSFT"]
        start: Start date/time as ISO string
        end: End date/time as ISO string
        market: Market code ("us", "cn", "hk", "crypto")
        frequency: Bar frequency ("1m", "5m", "1h", "1d")
        source: "api" (via Go query API) or "direct" (read from GCS/local)
        cache_dir: Local directory for caching Parquet files (direct source only).
            When set, reads from cache first, fetches from GCS on miss,
            and caches to disk. Set QUANT_CACHE_DIR env var as alternative.

    Returns:
        pd.DataFrame with columns [open, high, low, close, volume, market, frequency]
        indexed by (symbol, timestamp).
    """
    if source == "direct":
        return bars_direct(symbols, start, end, market=market, cache_dir=cache_dir)

    if isinstance(symbols, list):
        symbols = ",".join(symbols)
    return _get_client().bars(symbols, start, end, market=market, frequency=frequency)
