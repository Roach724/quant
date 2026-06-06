"""Symbol normalization — canonical format for US and HK stock codes.

Usage:
    from common.normalize import normalize_symbol, queryize_symbol

    normalize_symbol("HK.00005", "hk")    # → "00005"
    normalize_symbol("5", "hk")           # → "00005"
    normalize_symbol("US.AAPL", "us")     # → "AAPL"
    normalize_symbol("AAPL", "us")        # → "AAPL"

    queryize_symbol("00005", "hk")        # → "HK.00005"
    queryize_symbol("AAPL", "us")         # → "US.AAPL"
"""


def normalize_symbol(s: str, market: str) -> str:
    """Normalize a symbol to canonical bare format.

    HK: strips HK./HK_ prefix, zero-pads to 5 digits.
        HK.00005 / 0005 / 5 → "00005"
    US: strips US./US_ prefix.
        US.AAPL / AAPL → "AAPL"

    Args:
        s: Raw symbol string (any format).
        market: "us" or "hk".

    Returns:
        Normalized symbol as a bare ticker string.
    """
    s = str(s)
    prefix = f"{market.upper()}."
    alt_prefix = f"{market.upper()}_"
    if s.startswith(prefix):
        s = s[len(prefix):]
    elif s.startswith(alt_prefix):
        s = s[len(alt_prefix):]
    if market == "hk":
        s = s.lstrip("0") or "0"
        s = s.zfill(5)
    return s


def queryize_symbol(s: str, market: str) -> str:
    """Convert a bare symbol to BQ query format with market prefix.

    Args:
        s: Symbol in any format.
        market: "us" or "hk".

    Returns:
        ``"HK.00005"`` or ``"US.AAPL"``.
    """
    return f"{market.upper()}.{normalize_symbol(s, market)}"


def normalize_symbol_series(series, market: str):
    """Normalize a pandas Series of symbols in-place."""
    return series.apply(lambda s: normalize_symbol(s, market))


def queryize_symbol_series(series, market: str):
    """Convert a pandas Series of bare symbols to BQ query format."""
    return series.apply(lambda s: queryize_symbol(s, market))
