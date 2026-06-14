"""Data provider — multi-source data acquisition with fallback.

Priority: BigQuery (own DB) → LLMQuant (external) → "unavailable" marker.

BigQuery tables used (read-only):
  - quant.us_bars_1d     : daily OHLCV
  - quant.us_bars_5m     : 5-minute OHLCV (for intraday snapshots)
  - quant.factor_values  : pre-computed technical factors
  - quant.factor_registry: factor metadata

LLMQuant data capabilities (integration TBD):
  - Fundamentals: P/E, revenue growth, net margin, debt/equity
  - Sentiment: news headlines, sentiment scores
  - Institutional: 13F filings, institutional ownership
  - SEC filings: 10-K/10-Q text analysis
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from google.cloud import bigquery

from ai_decision.schemas import DataSourceStatus, MarketData

logger = logging.getLogger(__name__)

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"
BARS_1D_TABLE = f"{PROJECT}.{DATASET}.us_bars_1d"
FACTOR_VALUES_TABLE = f"{PROJECT}.{DATASET}.factor_values"
FACTOR_REGISTRY_TABLE = f"{PROJECT}.{DATASET}.factor_registry"


# ── BigQuery Provider ────────────────────────────────────────────

class BigQueryProvider:
    """Read technical market data from BigQuery.

    Data owned by the quant pipeline — no external dependency.
    """

    def __init__(self, project: str = PROJECT):
        self.project = project
        self.client = bigquery.Client(project=project)
        self._cache: dict[str, pd.DataFrame] = {}

    # ── Public API ──

    def fetch(
        self, symbol: str, fields: list[str], lookback_days: int = 120
    ) -> dict[str, Any]:
        """Fetch technical data for a symbol.

        Args:
            symbol: e.g. "AAPL" or "US.AAPL"
            fields: list of field names (see FIELD_MAP)
            lookback_days: how many days of history to query

        Returns:
            dict mapping field → value (or None if unavailable)
        """
        clean = self._clean_symbol(symbol)
        result: dict[str, Any] = {}

        # Determine which queries we need to run
        needs_bars = any(f in BAR_FIELDS for f in fields)
        needs_factors = any(f in FACTOR_FIELD_MAP for f in fields)

        bars_df = None
        factor_df = None

        if needs_bars:
            bars_df = self._query_bars_1d(clean, lookback_days)
        if needs_factors:
            factor_df = self._query_factor_values(clean, lookback_days)

        for field in fields:
            if field in BAR_FIELDS:
                result[field] = self._extract_bar_field(bars_df, field)
            elif field in FACTOR_FIELD_MAP:
                factor_id = FACTOR_FIELD_MAP[field]
                result[field] = self._extract_factor(factor_df, factor_id)
            else:
                result[field] = None  # not a BQ field

        return result

    def status(self, fields: list[str]) -> DataSourceStatus:
        """Report which fields are available from BigQuery."""
        available = [f for f in fields if f in BAR_FIELDS or f in FACTOR_FIELD_MAP]
        missing = [f for f in fields if f not in available]
        return DataSourceStatus(
            source="bigquery",
            available=True,
            fields_available=available,
            fields_missing=missing,
        )

    # ── Internal ──

    @staticmethod
    def _clean_symbol(symbol: str) -> str:
        """Strip market prefix for BQ queries."""
        for prefix in ("US.", "HK.", "CRYPTO."):
            if symbol.startswith(prefix):
                return symbol[len(prefix):]
        return symbol

    def _query_bars_1d(self, symbol: str, lookback_days: int) -> pd.DataFrame | None:
        try:
            query = f"""
                SELECT symbol, timestamp, open, high, low, close, volume
                FROM `{BARS_1D_TABLE}`
                WHERE symbol = 'US.{symbol}'
                  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {lookback_days} DAY)
                ORDER BY timestamp DESC
            """
            df = self.client.query(query).to_dataframe()
            if df.empty:
                logger.debug("No bars found for %s in us_bars_1d", symbol)
                return None
            return df
        except Exception as e:
            logger.warning("BQ bars query failed for %s: %s", symbol, e)
            return None

    def _query_factor_values(self, symbol: str, lookback_days: int) -> pd.DataFrame | None:
        """Query factor_values for the given symbol.

        The factor_values table stores (symbol, factor_id, date, value).
        We fetch all active US factors for this symbol within the lookback window.
        """
        try:
            query = f"""
                SELECT fv.symbol, fv.date, fv.factor_id, fv.value
                FROM `{FACTOR_VALUES_TABLE}` fv
                WHERE fv.symbol = '{symbol}'
                  AND fv.factor_id IN (
                    SELECT factor_id FROM `{FACTOR_REGISTRY_TABLE}`
                    WHERE market = 'us' AND is_active = TRUE
                  )
                  AND fv.date >= DATE_SUB(CURRENT_DATE(), INTERVAL {lookback_days} DAY)
                ORDER BY fv.date DESC
            """
            df = self.client.query(query).to_dataframe()
            if df.empty:
                logger.debug("No factor values found for %s", symbol)
                return None
            return df
        except Exception as e:
            logger.warning("BQ factor query failed for %s: %s", symbol, e)
            return None

    @staticmethod
    def _extract_bar_field(df: pd.DataFrame | None, field: str) -> Any:
        if df is None or df.empty:
            return None
        try:
            if field == "price":
                return float(df.iloc[0]["close"])
            col = field  # open/high/low/close/volume
            if col in df.columns:
                val = df.iloc[0][col]
                return int(val) if col == "volume" else float(val)
            return None
        except (IndexError, KeyError, ValueError):
            return None

    @staticmethod
    def _extract_factor(df: pd.DataFrame | None, factor_id: str) -> Any:
        if df is None or df.empty:
            return None
        try:
            match = df[df["factor_id"] == factor_id]
            if match.empty:
                return None
            return float(match.iloc[0]["value"])
        except (IndexError, KeyError, ValueError):
            return None


# ── Field Mappings ───────────────────────────────────────────────

# Fields directly available from us_bars_1d
BAR_FIELDS = {"price", "open", "high", "low", "close", "volume"}

# Fields available from factor_values (schema_field → actual BQ factor_id)
FACTOR_FIELD_MAP = {
    "rsi_14":      "us_rsi_14",
    "macd":        "us_macd",
    "macd_signal": "us_macd_signal",
    "bb_position": "us_bb_position",
    "bb_width":    "us_bb_width",
}

# Fields computed on-the-fly from bars data (not in factor_values)
# ma_20/ma_50: rolling mean of close
# atr_14: average true range
# bb_upper/bb_lower: computed from close + bb_width + bb_position
COMPUTED_FIELDS = {"ma_20", "ma_50", "atr_14", "bb_upper", "bb_lower"}

# All known BQ fields (for status reporting) — includes computed fields
ALL_BQ_FIELDS = set(BAR_FIELDS) | set(FACTOR_FIELD_MAP.keys()) | COMPUTED_FIELDS


# ── LLMQuant Provider (stub) ─────────────────────────────────────

class LLMQuantProvider:
    """External data provider via LLMQuant skills.

    Provides fundamental, sentiment, and SEC filing data that BigQuery
    doesn't cover.  Falls back to None when data is unavailable.

    NOTE: Integration point — actual HTTP/MCP calls to LLMQuant
    endpoints will be implemented when the LLMQuant API is available.
    Currently returns stub data with appropriate status tracking.
    """

    # Fields that LLMQuant can potentially provide
    FUNDAMENTAL_FIELDS = {"pe", "forward_pe", "revenue_growth", "net_margin", "debt_equity"}
    SENTIMENT_FIELDS = {"news_sentiment", "news_headlines"}
    ALL_LLMQ_FIELDS = FUNDAMENTAL_FIELDS | SENTIMENT_FIELDS

    def fetch(self, symbol: str, fields: list[str]) -> dict[str, Any]:
        """Fetch data from LLMQuant for a symbol.

        Args:
            symbol: e.g. "AAPL"
            fields: list of field names

        Returns:
            dict mapping field → value (None if unavailable)
        """
        result: dict[str, Any] = {}
        clean = symbol.replace("US.", "").replace("HK.", "")

        for field in fields:
            if field in self.FUNDAMENTAL_FIELDS:
                # TODO: call llmquant-equities for fundamental data
                result[field] = None
            elif field in self.SENTIMENT_FIELDS:
                # TODO: call llmquant-data / llmquant-market-intelligence for sentiment
                result[field] = [] if field == "news_headlines" else None
            else:
                result[field] = None

        logger.debug("LLMQuant fetch for %s: all fields pending integration", clean)
        return result

    def status(self, fields: list[str]) -> DataSourceStatus:
        """Report which fields LLMQuant could provide."""
        available = [f for f in fields if f in self.ALL_LLMQ_FIELDS]
        missing = [f for f in fields if f not in available]
        return DataSourceStatus(
            source="llmquant",
            available=False,  # pending integration
            fields_available=available,
            fields_missing=missing,
            error="LLMQuant integration not yet implemented",
        )


# ── Orchestrator ─────────────────────────────────────────────────

class DataProvider:
    """Multi-source data provider with fallback.

    Priority: BigQuery → LLMQuant → unavailable marker.

    Usage:
        provider = DataProvider()
        data = await provider.get_data("AAPL", [
            "price", "ma_20", "rsi_14", "pe", "news_sentiment"
        ])
        # data.price → from BQ
        # data.pe → from LLMQuant (or None if unavailable)
    """

    def __init__(
        self,
        bq: BigQueryProvider | None = None,
        llmq: LLMQuantProvider | None = None,
    ):
        self.bq = bq or BigQueryProvider()
        self.llmq = llmq or LLMQuantProvider()

    async def get_data(self, symbol: str, fields: list[str]) -> MarketData:
        """Fetch all requested fields for a symbol, with fallback.

        Args:
            symbol: e.g. "US.AAPL" or "AAPL"
            fields: list of field names (see MarketData model)

        Returns:
            MarketData with available fields populated, None for unavailable.
        """
        # Determine which fields each source can serve
        bq_fields = [f for f in fields if f in ALL_BQ_FIELDS]
        llmq_fields = [f for f in fields if f not in ALL_BQ_FIELDS]

        # Fetch from BigQuery
        bq_data = {}
        if bq_fields:
            bq_data = self.bq.fetch(symbol, bq_fields)

        # Fetch from LLMQuant for fields BQ can't cover
        llmq_data = {}
        if llmq_fields:
            llmq_data = self.llmq.fetch(symbol, llmq_fields)

        # Build coverage map
        coverage = {}
        all_data = {**bq_data, **llmq_data}
        for field in fields:
            coverage[field] = all_data.get(field) is not None

        return MarketData(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            price=bq_data.get("price"),
            open=bq_data.get("open"),
            high=bq_data.get("high"),
            low=bq_data.get("low"),
            close=bq_data.get("close"),
            volume=bq_data.get("volume"),
            ma_20=bq_data.get("ma_20"),
            ma_50=bq_data.get("ma_50"),
            rsi_14=bq_data.get("rsi_14"),
            macd=bq_data.get("macd"),
            macd_signal=bq_data.get("macd_signal"),
            bb_upper=bq_data.get("bb_upper"),
            bb_lower=bq_data.get("bb_lower"),
            bb_position=bq_data.get("bb_position"),
            bb_width=bq_data.get("bb_width"),
            atr_14=bq_data.get("atr_14"),
            pe=llmq_data.get("pe"),
            forward_pe=llmq_data.get("forward_pe"),
            revenue_growth=llmq_data.get("revenue_growth"),
            net_margin=llmq_data.get("net_margin"),
            debt_equity=llmq_data.get("debt_equity"),
            news_sentiment=llmq_data.get("news_sentiment"),
            news_headlines=llmq_data.get("news_headlines", []),
            data_coverage=coverage,
        )

    async def get_multi(self, symbols: list[str], fields: list[str]) -> dict[str, MarketData]:
        """Fetch data for multiple symbols in parallel."""
        import asyncio

        tasks = [self.get_data(sym, fields) for sym in symbols]
        results = await asyncio.gather(*tasks)
        return {r.symbol: r for r in results}
