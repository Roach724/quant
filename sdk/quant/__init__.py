"""Quantitative trading data SDK.

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
):
    if source == "direct":
        return bars_direct(symbols, start, end, market=market)

    if isinstance(symbols, list):
        symbols = ",".join(symbols)
    return _get_client().bars(symbols, start, end, market=market, frequency=frequency)
