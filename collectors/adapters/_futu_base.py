"""Shared base class for all F10 Futu data adapters.

Note: Futu F10 API return formats are inconsistent (DataFrame / dict / multi-value tuple).
Each adapter's _call_api() returns the raw API response; _parse() normalizes into pd.DataFrame.
"""
from __future__ import annotations

import logging
import os
import time as _time
from typing import Optional, Any

import pandas as pd
from futu import OpenQuoteContext

logger = logging.getLogger(__name__)

_RATE_LIMIT_GAP = 30.0 / 60  # 60 req/30s → 0.5s


class FutuBaseAdapter:
    """Base class for F10 data adapters — one per F10 data type.

    Subclass and override:
        DATA_TYPE: str — unique data type identifier
        _call_api(self, symbol: str) → Any — call the Futu API (return raw response)
        _parse(self, symbol: str, raw: Any) → pd.DataFrame — normalize API response
    """

    DATA_TYPE: str = ""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        symbols: Optional[list[str]] = None,
    ):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self.symbols = symbols or self._default_symbols()
        self._ctx: Optional[OpenQuoteContext] = None
        self._last_request_time = 0.0

    def _get_ctx(self) -> OpenQuoteContext:
        if self._ctx is None:
            self._ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self._ctx

    def _rate_limit(self):
        """Enforce 60 req/30s rate limit."""
        elapsed = _time.time() - self._last_request_time
        if elapsed < _RATE_LIMIT_GAP:
            _time.sleep(_RATE_LIMIT_GAP - elapsed)
        self._last_request_time = _time.time()

    @staticmethod
    def _market_from_code(code: str) -> str:
        if code.startswith("HK."):
            return "hk"
        if code.startswith("US."):
            return "us"
        return "unknown"

    # ── Subclass override points ───────────────────────────────────

    def _call_api(self, symbol: str) -> Any:
        """Call the Futu API. Override in subclass."""
        raise NotImplementedError

    def _parse(self, symbol: str, raw: Any) -> pd.DataFrame:
        """Parse raw API response into a DataFrame. Override in subclass."""
        raise NotImplementedError

    # ── Helpers for parsing ────────────────────────────────────────

    @staticmethod
    def _dict_to_dataframe(d: dict) -> pd.DataFrame:
        """Convert flat dict (like analyst consensus) to single-row DataFrame."""
        return pd.DataFrame([d])

    @staticmethod
    def _report_list_to_dataframe(structure_list: list, report_list: list) -> pd.DataFrame:
        """Convert financials structure_list + report_list → DataFrame."""
        cols = [s.get("name", f"col_{i}") for i, s in enumerate(structure_list)]
        return pd.DataFrame(report_list, columns=cols)

    # ── Main fetch loop ────────────────────────────────────────────

    def fetch_all(self) -> dict[str, pd.DataFrame]:
        """Fetch and parse data for all symbols. Returns {symbol: DataFrame}."""
        results: dict[str, pd.DataFrame] = {}
        for i, sym in enumerate(self.symbols):
            try:
                self._rate_limit()
                raw = self._call_api(sym)
                df = self._parse(sym, raw)
                if df is not None and len(df) > 0:
                    df["symbol"] = sym.replace(".", "_")
                    results[sym] = df
            except Exception:
                logger.debug("Fetch/parse failed for %s", sym, exc_info=True)
            if (i + 1) % 50 == 0:
                logger.info("  %s: %d/%d symbols", self.DATA_TYPE, i + 1, len(self.symbols))
        return results

    def close(self):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    def _default_symbols(self) -> list[str]:
        """Load symbol pool from FutuStockAdapter static list (no OpenD needed)."""
        try:
            from collectors.adapters.futu_stock_adapter import FutuStockAdapter
            syms = list(FutuStockAdapter._DEFAULT_SYMBOLS)
            us_count = sum(1 for s in syms if s.startswith("US."))
            hk_count = sum(1 for s in syms if s.startswith("HK."))
            logger.info("Loaded %d symbols (%d US + %d HK) from static list", len(syms), us_count, hk_count)
            return syms
        except Exception:
            logger.warning("Cannot load symbols; using static fallback")
            return [
                "HK.00700", "HK.09988", "HK.00941", "HK.00005", "HK.00388",
                "HK.01299", "HK.02318", "HK.01810",
                "US.AAPL", "US.MSFT", "US.NVDA", "US.AMZN", "US.META", "US.GOOGL",
                "US.AVGO", "US.TSLA", "US.COST", "US.NFLX", "US.ADBE", "US.AMD",
                "US.JPM", "US.V", "US.UNH", "US.XOM", "US.MA", "US.JNJ", "US.WMT",
                "US.PG", "US.HD", "US.BAC", "US.CVX",
            ]
