"""Data provider — multi-source data acquisition with fallback.

Priority: BigQuery (own DB) → LLMQuant MCP (external) → "unavailable" marker.

BigQuery tables used (read-only):
  - quant.us_bars_1d     : daily OHLCV
  - quant.us_bars_5m     : 5-minute OHLCV (for intraday snapshots)
  - quant.factor_values  : pre-computed technical factors
  - quant.factor_registry: factor metadata

LLMQuant MCP (via @llmquant/data-mcp):
  - SEC filings: 10-K/10-Q/8-K browse + read (risk factors, MD&A)
  - 13F: institutional holdings, smart money flow
  - Macro: 50+ indicators snapshot + history
  - Fundamentals (PE, margins): Coming Soon per LLMQuant roadmap
"""

from __future__ import annotations

import logging
import os
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

# LLMQuant MCP API key — set via env or passed directly
# Generate at: https://llmquantdata.com/dashboard → API Keys
LLMQUANT_API_KEY = os.environ.get("LLMQUANT_API_KEY", "")

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
        needs_computed = any(f in COMPUTED_FIELDS for f in fields)

        # Bars are needed for computed fields too (MA, ATR, BB)
        if needs_computed and not needs_bars:
            needs_bars = True

        bars_df = None
        factor_df = None

        if needs_bars:
            bars_df = self._query_bars_1d(clean, lookback_days)
        if needs_factors:
            factor_df = self._query_factor_values(clean, lookback_days)

        # Compute on-the-fly fields from bars data
        computed = {}
        if needs_computed and bars_df is not None and not bars_df.empty:
            computed = self._compute_fields(bars_df, fields)

        for field in fields:
            if field in BAR_FIELDS:
                result[field] = self._extract_bar_field(bars_df, field)
            elif field in FACTOR_FIELD_MAP:
                factor_id = FACTOR_FIELD_MAP[field]
                result[field] = self._extract_factor(factor_df, factor_id)
            elif field in COMPUTED_FIELDS:
                result[field] = computed.get(field)
            else:
                result[field] = None  # not a BQ field

        return result

    def status(self, fields: list[str]) -> DataSourceStatus:
        """Report which fields are available from BigQuery."""
        available = [f for f in fields if f in BAR_FIELDS or f in FACTOR_FIELD_MAP or f in COMPUTED_FIELDS]
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

    @staticmethod
    def _compute_fields(bars_df: pd.DataFrame, fields: list[str]) -> dict[str, Any]:
        """Compute technical indicators on-the-fly from daily bar data.

        Bars are sorted DESC (newest first); reverse for rolling calculations.
        """
        result: dict[str, Any] = {}
        if bars_df is None or bars_df.empty:
            return result

        # Sort chronological for rolling window calculations
        df = bars_df.sort_values("timestamp", ascending=True).copy()
        close = df["close"].astype(float)

        needed = set(fields) & COMPUTED_FIELDS
        if not needed:
            return result

        # ── Moving Averages ──
        if "ma_20" in needed:
            ma20 = close.rolling(20, min_periods=20).mean()
            val = ma20.iloc[-1]
            result["ma_20"] = float(val) if pd.notna(val) else None

        if "ma_50" in needed:
            ma50 = close.rolling(50, min_periods=50).mean()
            val = ma50.iloc[-1]
            result["ma_50"] = float(val) if pd.notna(val) else None

        # ── ATR(14) ──
        if "atr_14" in needed:
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            prev_close = close.shift(1)

            tr = pd.concat([
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ], axis=1).max(axis=1)

            atr = tr.rolling(14, min_periods=14).mean()
            val = atr.iloc[-1]
            result["atr_14"] = float(val) if pd.notna(val) else None

        # ── Bollinger Bands (20-period, 2σ) ──
        if "bb_upper" in needed or "bb_lower" in needed:
            middle = close.rolling(20, min_periods=20).mean()
            std20 = close.rolling(20, min_periods=20).std()

            if "bb_upper" in needed:
                val = (middle + 2 * std20).iloc[-1]
                result["bb_upper"] = float(val) if pd.notna(val) else None

            if "bb_lower" in needed:
                val = (middle - 2 * std20).iloc[-1]
                result["bb_lower"] = float(val) if pd.notna(val) else None

        return result


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


# ── LLMQuant MCP Provider ────────────────────────────────────────

class LLMQuantMCPProvider:
    """External data provider via @llmquant/data-mcp MCP server.

    Provides SEC filing analysis, 13F institutional holdings, and
    macro indicators that complement BigQuery's technical data.

    MCP connection is established per-session (start/stop lifecycle)
    and reused across multiple fetch calls.
    """

    # Fields that LLMQuant can provide
    SEC_FIELDS = {"sec_filings"}       # 10-K/10-Q/8-K browse → risk factors
    FLOW_FIELDS = {"institutional_flow"}  # 13F holders → smart money data
    SENTIMENT_FIELDS = {"news_headlines"}  # 8-K events as news signals
    ALL_LLMQ_FIELDS = SEC_FIELDS | FLOW_FIELDS | SENTIMENT_FIELDS

    # Fields coming soon (LLMQuant roadmap)
    # "pe", "forward_pe", "revenue_growth", "net_margin", "debt_equity", "news_sentiment"

    def __init__(self, api_key: str | None = None):
        import os
        self.api_key = api_key or os.environ.get("LLMQUANT_API_KEY")
        self._session = None
        self._ctx = None
        self._read = None
        self._write = None

    async def _ensure_session(self):
        """Lazy-init MCP session (reused across calls)."""
        if self._session is not None:
            return

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        import asyncio

        params = StdioServerParameters(
            command="npx",
            args=["-y", "@llmquant/data-mcp"],
            env={"LLMQUANT_API_KEY": self.api_key or ""},
        )

        # Use proper async context manager
        self._ctx = stdio_client(params)
        self._read, self._write = await self._ctx.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await asyncio.sleep(1)  # let server settle
        await self._session.initialize()
        logger.info("LLMQuant MCP session initialized")

    async def close(self):
        """Close MCP session."""
        if self._session:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if hasattr(self, '_ctx') and self._ctx:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None
            self._read = None
            self._write = None

    async def fetch(self, symbol: str, fields: list[str]) -> dict[str, Any]:
        """Fetch data from LLMQuant MCP for a symbol.

        Args:
            symbol: e.g. "AAPL" (without market prefix)
            fields: list of field names

        Returns:
            dict mapping field → value (None if unavailable)
        """
        result: dict[str, Any] = {}
        clean = symbol.replace("US.", "").replace("HK.", "")

        needs_sec = "sec_filings" in fields
        needs_flow = "institutional_flow" in fields
        needs_news = "news_headlines" in fields

        if not (needs_sec or needs_flow or needs_news):
            return result

        await self._ensure_session()

        # ── News: 8-K events as proxy for headlines ──
        if needs_news:
            try:
                r = await self._session.call_tool("sec_filing_browse", {
                    "ticker": clean, "filing_type": "8-K", "limit": 5,
                })
                items = self._parse_items(r)
                result["news_headlines"] = [
                    {
                        "date": item.get("filingDate", ""),
                        "headline": f"8-K: {item.get('filingType', '')} filed",
                        "sentiment": 0.0,  # neutral default
                        "section_keys": item.get("sectionKeys", []),
                    }
                    for item in items
                ]
            except Exception as e:
                logger.warning("LLMQuant 8-K browse failed for %s: %s", clean, e)
                result["news_headlines"] = []

        # ── SEC filings: risk factors from latest 10-K ──
        if needs_sec:
            try:
                r = await self._session.call_tool("sec_filing_browse", {
                    "ticker": clean, "filing_type": "10-K", "limit": 1,
                })
                items = self._parse_items(r)
                if items:
                    filing = items[0]
                    # Read risk factors section (Item 1A)
                    try:
                        rr = await self._session.call_tool("sec_filing_read", {
                            "ticker": clean,
                            "accessionNumber": filing["accessionNumber"],
                            "sections": ["1A"],
                        })
                        risk_data = self._parse_items(rr)
                        result["sec_filings"] = {
                            "latest_10k": {
                                "filing_date": filing.get("filingDate"),
                                "report_date": filing.get("reportDate"),
                                "risk_factors": (
                                    risk_data[0].get("content", "")[:2000]
                                    if risk_data else ""
                                ),
                            }
                        }
                    except Exception:
                        result["sec_filings"] = {
                            "latest_10k": {"filing_date": filing.get("filingDate")}
                        }
            except Exception as e:
                logger.warning("LLMQuant SEC browse failed for %s: %s", clean, e)
                result["sec_filings"] = None

        # ── 13F institutional flow ──
        if needs_flow:
            try:
                from datetime import datetime as dt
                now = dt.now()
                # Approximate quarter
                quarter = (now.month - 1) // 3 + 1
                year = now.year
                if quarter == 1:
                    year -= 1
                    quarter = 4
                else:
                    quarter -= 1

                r = await self._session.call_tool("sec_13f_list_ticker_holders", {
                    "ticker": clean, "year": year, "quarter": quarter,
                })
                data = self._parse_json(r)
                holders = data.get("items", data.get("holders", []))
                result["institutional_flow"] = {
                    "total_holders": data.get("totalHoldersInScope", len(holders)),
                    "top_holders": [
                        {
                            "manager": h.get("managerName", ""),
                            "value_usd": h.get("valueUsd", 0),
                            "shares": h.get("shares", 0),
                        }
                        for h in holders[:5]
                    ],
                }
            except Exception as e:
                logger.warning("LLMQuant 13F failed for %s: %s", clean, e)
                result["institutional_flow"] = None

        # Fill remaining fields as unavailable
        for field in fields:
            if field not in result:
                result[field] = None

        return result

    def status(self, fields: list[str]) -> DataSourceStatus:
        """Report which fields LLMQuant can (eventually) provide."""
        available = [f for f in fields if f in self.ALL_LLMQ_FIELDS]
        missing = [f for f in fields if f not in available]
        return DataSourceStatus(
            source="llmquant",
            available=True,
            fields_available=available,
            fields_missing=missing,
        )

    @staticmethod
    def _parse_items(result) -> list[dict]:
        """Extract items from MCP tool response."""
        data = LLMQuantMCPProvider._parse_json(result)
        return data.get("items", data.get("data", []))

    @staticmethod
    def _parse_json(result) -> dict:
        """Parse JSON from MCP TextContent."""
        import json
        if hasattr(result, "content") and result.content:
            text = result.content[0].text
            return json.loads(text)
        return {}


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
        llmq: LLMQuantMCPProvider | None = None,
    ):
        self.bq = bq or BigQueryProvider()
        self.llmq = llmq or LLMQuantMCPProvider()

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

        # Fetch from BigQuery (sync for now)
        bq_data = {}
        if bq_fields:
            bq_data = self.bq.fetch(symbol, bq_fields)

        # Fetch from LLMQuant MCP (async)
        llmq_data = {}
        if llmq_fields:
            llmq_data = await self.llmq.fetch(symbol, llmq_fields)

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
            sec_filings=llmq_data.get("sec_filings"),
            institutional_flow=llmq_data.get("institutional_flow"),
            data_coverage=coverage,
        )

    async def get_multi(self, symbols: list[str], fields: list[str]) -> dict[str, MarketData]:
        """Fetch data for multiple symbols in parallel."""
        import asyncio

        tasks = [self.get_data(sym, fields) for sym in symbols]
        results = await asyncio.gather(*tasks)
        return {r.symbol: r for r in results}
