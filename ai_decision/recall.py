"""Recall Layer — aggregate signals from all enabled strategies.

For each enabled strategy, loads the strategy class, creates a minimal
context from BQ daily bars, calls on_bar() at the latest bar, and collects
all produced signals into a unified list.

Output: list[StrategySignal] — one per (symbol, strategy) pair.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import yaml
from google.cloud import bigquery
from pathlib import Path

from engine.data import DataFrameSource
from engine.strategy import StrategyContext, Signal as EngineSignal
from ai_decision.schemas import StrategySignal

logger = logging.getLogger(__name__)

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"
BARS_1D_TABLE = f"{PROJECT}.{DATASET}.us_bars_1d"
QUANT_HOME = Path("/opt/quant") if Path("/opt/quant").exists() else Path("/opt/quant-dev")
SYMBOLS_CONFIG = str(QUANT_HOME / "config/symbols.yaml")

# Minimum lookback bars required for most strategies
MIN_LOOKBACK_DAYS = 120  # enough for 20-bar momentum, 50-bar MA, etc.


def load_us_symbols(config_path: str = SYMBOLS_CONFIG) -> list[str]:
    """Load US symbols from config/symbols.yaml.

    Returns list of normalized symbols without 'US.' prefix
    (e.g., ['AAPL', 'MSFT', ...]).
    """
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    us_symbols = cfg.get("markets", {}).get("us", {}).get("symbols", [])
    cleaned = []
    for s in us_symbols:
        raw = str(s)
        for prefix in ("US.", "HK.", "CRYPTO."):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        cleaned.append(raw)
    
    logger.info("Loaded %d US symbols from %s", len(cleaned), config_path)
    return cleaned


def query_daily_bars(
    symbols: list[str],
    lookback_days: int = MIN_LOOKBACK_DAYS,
    project: str = PROJECT,
) -> dict[str, list]:
    """Query BigQuery us_bars_1d for recent daily bars.

    Returns a dict suitable for building a DataFrameSource:
        {
            "close": {symbol: [values...]},
            "open": {symbol: [values...]},
            ...
            "timestamp": [datetime...]
        }

    Bars are returned in chronological order.
    """
    client = bigquery.Client(project=project)

    # Build the symbol filter with US. prefix
    bq_symbols = [f"US.{s}" for s in symbols]

    # Query — with IN UNNEST on the symbol list + timestamp range
    query = f"""
        SELECT symbol, timestamp, open, high, low, close, volume
        FROM `{BARS_1D_TABLE}`
        WHERE symbol IN UNNEST(@symbols)
          AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {lookback_days} DAY)
        ORDER BY timestamp, symbol
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("symbols", "STRING", bq_symbols),
        ],
    )

    import pandas as pd
    df = client.query(query, job_config=job_config).to_dataframe()
    df["symbol"] = df["symbol"].str.replace("US.", "", regex=False)

    logger.info(
        "Query returned %d rows for %d unique symbols over %d days",
        len(df), df["symbol"].nunique(), lookback_days,
    )

    # Build per-field dicts keyed by timestamp+symbol
    timestamps = sorted(df["timestamp"].unique())
    result = {
        "close": {},
        "open": {},
        "high": {},
        "low": {},
        "volume": {},
        "timestamp": timestamps,
    }

    for field in ("close", "open", "high", "low", "volume"):
        pivot = df.pivot(index="timestamp", columns="symbol", values=field)
        # Ensure all symbols have columns (fill missing with NaN)
        for sym in symbols:
            if sym not in pivot.columns:
                pivot[sym] = float("nan")
        result[field] = pivot

    return result


class RecallEngine:
    """Aggregates signals from all enabled strategies.

    Usage:
        engine = RecallEngine(enabled_strategies=["SimpleMomentum", ...])
        results = engine.run()

        for r in results:
            print(f"{r.symbol}: {r.aggregate_score:.2f} "
                  f"({r.hitting_count} strategies)")
    """

    def __init__(
        self,
        enabled_strategies: list[str],
        symbols: list[str] | None = None,
        lookback_days: int = MIN_LOOKBACK_DAYS,
        market: str = "us",
    ):
        self.enabled_strategies = enabled_strategies
        self.symbols = symbols or load_us_symbols()
        self.lookback_days = lookback_days
        self.market = market
        self._data_source: DataFrameSource | None = None
        self._context: StrategyContext | None = None

    def run(self) -> list[StrategySignal]:
        """Execute recall: load data → run strategies → collect signals.

        Returns:
            List of StrategySignal, one per (symbol, strategy) pair
            where the strategy produced a non-empty signal list.
        """
        import pandas as pd

        # ── 1. Load bar data ──
        bar_dict = query_daily_bars(self.symbols, self.lookback_days)
        timestamps = bar_dict["timestamp"]

        if not timestamps:
            logger.warning("No bar data found for any symbol")
            return []

        # ── 2. Build DataFrameSource ──
        self._data_source = DataFrameSource(
            close=bar_dict["close"],
            open=bar_dict["open"],
            high=bar_dict["high"],
            low=bar_dict["low"],
            volume=bar_dict["volume"],
        )

        # Create a minimal portfolio stub
        from engine.portfolio import Portfolio
        portfolio = Portfolio(initial_capital=100000.0)

        self._context = StrategyContext(
            data=self._data_source,
            portfolio=portfolio,
            config={"market": self.market},
        )

        # ── 3. Run each strategy at the latest bar ──
        latest_bar = len(timestamps) - 1
        logger.info(
            "Running recall: %d strategies at bar %d (%d symbols)",
            len(self.enabled_strategies), latest_bar, len(self.symbols),
        )

        all_signals: list[StrategySignal] = []

        for strategy_name in self.enabled_strategies:
            signals = self._run_strategy(strategy_name, latest_bar)
            if signals:
                all_signals.extend(signals)
                logger.debug(
                    "%s: %d signals", strategy_name, len(signals),
                )
            else:
                logger.debug("%s: no signals", strategy_name)

        logger.info(
            "Recall complete: %d total signals from %d strategies",
            len(all_signals),
            sum(1 for s in self.enabled_strategies
                if any(sig.strategy == s for sig in all_signals)),
        )

        return all_signals

    def _run_strategy(
        self, strategy_name: str, bar: int,
    ) -> list[StrategySignal]:
        """Instantiate a strategy, run on_bar, return its signals."""
        from strategies import get_strategy

        try:
            cls = get_strategy(strategy_name)
        except ValueError:
            logger.warning("Unknown strategy: %s", strategy_name)
            return []

        # ── Instantiate ──
        try:
            instance = cls()
        except Exception:
            logger.exception("Failed to instantiate %s", strategy_name)
            return []

        # ── on_init ──
        try:
            instance.on_init(self._context)
        except Exception:
            logger.exception("%s.on_init failed", strategy_name)
            return []

        # ── on_bar ──
        try:
            raw_signals: list[EngineSignal] = instance.on_bar(self._context, bar)
        except Exception:
            logger.exception("%s.on_bar failed at bar %d", strategy_name, bar)
            return []

        if not raw_signals:
            return []

        # ── Convert to StrategySignal ──
        results = []
        for s in raw_signals:
            direction = s.side
            if direction not in ("buy", "sell"):
                # Skip non-directional signals (close/target)
                continue

            raw = s.score if s.score else 0.0
            # Fallback: use weight when score is 0 (some strategies only set weight)
            if raw == 0.0 and s.weight is not None and s.weight > 0:
                raw = float(s.weight)
            # Normalize: buy→positive, sell→negative
            if direction == "sell":
                raw = -abs(raw) if raw >= 0 else raw

            score = raw
            # Confidence: sigmoid → [0, 1]
            confidence = 2.0 / (1.0 + math.exp(-abs(raw))) - 1.0

            results.append(StrategySignal(
                symbol=s.symbol,
                strategy=strategy_name,
                direction=direction,
                score=score,
                confidence=min(confidence, 1.0),
                timestamp=datetime.now(timezone.utc),
            ))

        return results

    @property
    def total_symbols(self) -> int:
        return len(self.symbols)

    @property
    def latest_timestamp(self):
        if self._data_source is not None and len(self._data_source) > 0:
            return self._data_source.timestamp[-1]
        return None
