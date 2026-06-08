#!/usr/bin/env python3
"""Paper Runner — multi-market paper-trading replay system.

Usage
-----
    python run_paper.py --market hk --capital 1000000 --strategy BuyHold
    python run_paper.py --market us --strategy SimpleMomentum --lookback 20 --top-k 5
    python run_paper.py --config paper/config.yaml
    python run_paper.py --list-strategies
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from tqdm import tqdm

# Ensure the project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.data import DataFrameSource
from engine.strategy import Strategy, StrategyContext
from engine.portfolio import Portfolio
from engine.report import generate as generate_html_report
from engine.metrics import summary as engine_metrics_summary
from engine.orders import Order, simulate_fills
from engine.risk import RiskEngine

from oms.broker import PaperBroker
from oms.manager import OrderManager
from oms.position import PositionTracker
from oms.alerting import AlertManager, ConsoleHandler
from oms.risk_gateway import RiskGateway
from oms.bridge import convert_signal

from experiment.investment_record import InvestmentRecord

from paper.market import (
    MARKET_SCHEDULES, default_symbols_for, is_market_open,
    MARKET_US, MARKET_HK, MARKET_CRYPTO,
)
from strategies import get_strategy, list_strategies

log = logging.getLogger("paper.runner")

# ── Result wrapper (mirrors engine.Result pattern) ──


class PaperResult:
    """Wrapper holding the results of a paper run — used by report generation."""

    def __init__(self, portfolio: Portfolio, config: dict, strategy_name: str = ""):
        self.portfolio = portfolio
        self.config = config
        self.strategy_name = strategy_name


# ── Core Paper Runner ──


class PaperRunner:
    """Orchestrates a paper-trading replay from historical bar data.

    Creates broker, order-manager, risk gateway, position tracker, alert manager,
    and investment record; loads data; runs the strategy bar-by-bar; generates
    performance metrics, HTML report, and full investment archive.

    Parameters
    ----------
    config : dict
        Configuration dictionary with keys:
        - market (str): "us", "hk", "crypto"
        - capital (float): initial cash
        - strategy (str): qualified class name
        - strategy_kwargs (dict): parameter overrides for the strategy
        - start (str): YYYY-MM-DD
        - end (str): YYYY-MM-DD
        - symbols (list[str]): universe
        - data_source (str): "simulated", "parquet", "sdk"
        - data_dir (str): path for parquet data
        - output (str): output directory
        - realtime (bool): sleep between bars
        - realtime_interval (float): seconds to sleep per bar
    """

    def __init__(self, config: dict):
        self.config = config
        self.market = config["market"]
        market_cfg = MARKET_SCHEDULES.get(self.market, {})
        capital = float(config.get("capital", 100_000))

        # ── OMS / execution layer ──
        self.broker = PaperBroker(initial_capital=capital)
        self.order_manager = OrderManager(self.broker)
        self.position_tracker = PositionTracker(self.broker)
        self.alert_manager = AlertManager()
        self.alert_manager.on_alert(ConsoleHandler())

        # ── Risk rules (empty by default; strategy can add via add_risk) ──
        self.risk_gateway = RiskGateway(
            rules=[], broker=self.broker, alert_manager=self.alert_manager,
        )

        # ── Portfolio (engine-style, for equity curve + metrics) ──
        self.portfolio = Portfolio(initial_capital=capital)

        # ── Investment record (archive output) ──
        self.investment_record = InvestmentRecord(
            strategy_name=config.get("strategy", "unknown"),
            config={
                "market": self.market,
                "capital": capital,
                "start": config.get("start"),
                "end": config.get("end"),
                "symbols": config.get("symbols", []),
                **config.get("strategy_kwargs", {}),
            },
        )

        # Derived costs from market schedule
        self.slippage_bps = float(market_cfg.get("slippage_bps", 5.0))
        self.commission_bps = float(market_cfg.get("commission_bps", 1.0))
        self.min_commission = float(market_cfg.get("min_commission", 1.0))

    # ── Data loading ──

    def load_data(
        self,
        source: str,
        symbols: list[str],
        start: str,
        end: str,
        data_dir: str = "",
    ) -> DataFrameSource:
        """Load historical OHLCV data and return a DataFrameSource.

        Supported sources:
        - "simulated" : generate synthetic random-walk prices (useful for testing)
        - "parquet"   : load from local parquet directory
        - "sdk"       : use quant.data.bars() from GCS or API (requires SDK installed)
        """
        if source == "simulated":
            return self._simulated_data(symbols, start, end)

        if source == "parquet":
            return self._parquet_data(symbols, start, end, data_dir)

        if source in ("sdk", "bq"):
            return self._sdk_data(symbols, start, end)

        if source == "bq_5m":
            return self._bq_5m_data(symbols, start, end)

        raise ValueError(f"Unknown data source: {source!r}")

    def _simulated_data(self, symbols: list[str], start: str, end: str) -> DataFrameSource:
        """Generate random-walk prices for rapid testing."""
        log.info("Generating simulated data for %d symbols from %s to %s",
                 len(symbols), start, end)

        dates = pd.date_range(start, end, freq="B")  # business days
        if len(dates) < 2:
            raise ValueError(f"Need at least 2 dates, got {len(dates)} from {start}→{end}")

        n = len(dates)
        np_random = __import__("numpy").random
        rng = np_random.default_rng(42)

        # Random-walk starting at 100
        base = 100.0
        returns = rng.normal(0.0002, 0.015, size=(n, len(symbols)))
        prices = base * (1 + returns).cumprod(axis=0)

        np_max = __import__("numpy").maximum
        np_min = __import__("numpy").minimum

        close = pd.DataFrame(prices, index=dates, columns=symbols)
        open_df = close.copy() * (1 + rng.normal(0, 0.002, size=(n, len(symbols))))
        high_raw = pd.DataFrame(np_max(close.values, open_df.values), index=dates, columns=symbols)
        high = high_raw * (1 + abs(rng.normal(0, 0.005, size=(n, len(symbols)))))
        low_raw = pd.DataFrame(np_min(close.values, open_df.values), index=dates, columns=symbols)
        low = low_raw * (1 - abs(rng.normal(0, 0.005, size=(n, len(symbols)))))

        # Volume in thousands
        vol = pd.DataFrame(
            rng.integers(100, 1000, size=(n, len(symbols))).astype(float) * 1000,
            index=dates, columns=symbols,
        )

        log.info("Simulated data: %d bars × %d symbols", n, len(symbols))
        return DataFrameSource(close=close, open=open_df, high=high, low=low, volume=vol)

    def _parquet_data(self, symbols: list[str], start: str, end: str, data_dir: str) -> DataFrameSource:
        """Load OHLCV from a directory of per-symbol parquet files."""
        data_path = Path(data_dir)
        if not data_path.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")

        frames = {}
        for sym in symbols:
            fpath = data_path / f"{sym}.parquet"
            if not fpath.exists():
                log.warning("Parquet file not found for %s: %s", sym, fpath)
                continue
            df = pd.read_parquet(fpath)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")
            df = df.loc[start:end]
            frames[sym] = df

        if not frames:
            raise ValueError("No parquet data loaded for any symbol")

        # Align to common index
        common_idx = None
        for sym, df in frames.items():
            if common_idx is None:
                common_idx = df.index
            else:
                common_idx = common_idx.union(df.index)
        common_idx = common_idx.sort_values()

        close = pd.DataFrame(index=common_idx, columns=symbols, dtype=float)
        for sym in symbols:
            if sym in frames:
                close[sym] = frames[sym]["close"].reindex(common_idx)
        close = close.ffill()

        log.info("Parquet data: %d bars × %d symbols", len(close), len(symbols))
        return DataFrameSource(close=close)

    def _sdk_data(self, symbols: list[str], start: str, end: str) -> DataFrameSource:
        """Load OHLCV from BigQuery for paper-trading replay.

        Queries {market}_bars_1d table with symbol prefix mapping.
        US symbols get "US." prefix, HK symbols get "HK." prefix.
        """
        from google.cloud import bigquery

        client = bigquery.Client()
        table = f"deductive-notch-495015-c2.quant.{self.market}_bars_1d"

        prefix = "US." if self.market == "us" else "HK."
        bq_symbols = [f"{prefix}{s}" for s in symbols]

        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM `{table}`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp BETWEEN @start AND @end
            ORDER BY timestamp, symbol
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("symbols", "STRING", bq_symbols),
            bigquery.ScalarQueryParameter("start", "STRING", start),
            bigquery.ScalarQueryParameter("end", "STRING", end),
        ])
        df = client.query(query, job_config=job_config).to_dataframe()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["symbol"] = df["symbol"].str.replace(prefix, "")

        close = df.pivot_table(index="timestamp", columns="symbol", values="close").ffill()
        open_df = df.pivot_table(index="timestamp", columns="symbol", values="open").ffill()
        high = df.pivot_table(index="timestamp", columns="symbol", values="high").ffill()
        low = df.pivot_table(index="timestamp", columns="symbol", values="low").ffill()
        volume = df.pivot_table(index="timestamp", columns="symbol", values="volume").fillna(0)

        log.info("BQ data: %d bars x %d symbols", len(close), len(symbols))
        return DataFrameSource(close=close, open=open_df, high=high, low=low, volume=volume)

    def _bq_5m_data(self, symbols: list[str], start: str, end: str) -> DataFrameSource:
        """Load 5-minute OHLCV from BigQuery for paper-trading replay.

        Uses BigQuery5mSource.load_all() to query us_bars_5m and return
        wide-format DataFrameSource.
        """
        from engine.data import BigQuery5mSource

        # Use US. prefix for BQ query
        bq_symbols = [f"US.{s}" for s in symbols]
        src = BigQuery5mSource(
            market=self.market,
            start=start,
            end=end,
            symbols=bq_symbols,
        )
        return src.load_all()

    # ── Main run loop ──

    def run(self) -> dict:
        """Execute the paper-trading replay and return a result dict.

        Flow:
        1. Load data → DataFrameSource
        2. Instantiate and init strategy
        3. Loop bar-by-bar:
           a. Check market open
           b. Call strategy.on_bar() → signals
           c. Convert signals → orders via bridge
           d. Risk-gateway pre-trade check
           e. Update broker prices
           f. Submit orders → fills
           g. Track positions
           h. Record trades, equity, signals
        4. Compute performance metrics
        5. Generate HTML report
        6. Save full investment archive
        7. Print summary, return dict
        """
        cfg = self.config
        strategy_name = cfg.get("strategy", "unknown")
        start = cfg["start"]
        end = cfg["end"]
        symbols = cfg.get("symbols", default_symbols_for(self.market))
        source = cfg.get("data_source", "simulated")
        data_dir = cfg.get("data_dir", "")

        # 1. Load data
        data_source = self.load_data(source, symbols, start, end, data_dir)
        log.info("Data loaded: %d bars, %d symbols", len(data_source), len(symbols))

        # 2. Strategy
        strategy_cls = self._resolve_strategy(strategy_name)
        strategy = self._instantiate_strategy(strategy_cls, cfg.get("strategy_kwargs", {}))

        ctx = StrategyContext(data=data_source, portfolio=self.portfolio, config=cfg)
        strategy.on_init(ctx)

        # Risk-engine from strategy rules
        risk_engine = RiskEngine(strategy.risk_rules)
        self.risk_gateway.engine = risk_engine

        n_bars = len(data_source)
        dt_index = data_source.timestamp
        log.info("Starting paper run: %d bars [%s → %s]", n_bars, start, end)

        # ── Bar loop ──
        pbar = tqdm(total=n_bars, desc="Paper replay", unit="bar")
        skipped_bars = 0

        for bar_idx in range(n_bars):
            current_ts = dt_index[bar_idx]
            bar_data = data_source.iloc(bar_idx)
            ctx._set_bar_data(bar_data)

            # ---- Market-hours check ----
            if not is_market_open(self.market, current_ts.to_pydatetime()):
                self.portfolio.mark_and_record(current_ts, bar_data)
                self.investment_record.record_equity(
                    current_ts, self.portfolio._mark_to_market(bar_data)
                )
                skipped_bars += 1
                pbar.update(1)
                continue

            try:
                # a) Strategy signals
                signals = strategy.on_bar(ctx, bar_idx)
                if not signals:
                    self.portfolio.mark_and_record(current_ts, bar_data)
                    self.investment_record.record_equity(
                        current_ts, self.portfolio._mark_to_market(bar_data)
                    )
                    pbar.update(1)
                    continue

                # b) Convert signals → OMS-compatible dicts
                signal_dicts = [convert_signal(sig, self.portfolio) for sig in signals]

                # c) Build engine Orders for risk check
                engine_orders = self._signals_to_orders(signals, bar_data)

                # d) Risk-gateway pre-trade
                # risk_engine.check returns the approved subset (list)
                approved = risk_engine.check(engine_orders, self.portfolio, bar_data)

                # Normalize buy-signal weights to 1/n to avoid overallocation
                n_buy = sum(1 for s in signals if s.side in ("buy", "target"))
                if n_buy > 0:
                    for s in signals:
                        if s.side in ("buy", "target") and s.weight is None:
                            s.weight = 1.0 / n_buy

                for sig, order in zip(signals, engine_orders):
                    if order not in approved:
                        self.alert_manager.fire(
                            "warning",
                            f"Rejected {sig.symbol} {sig.side}",
                            {"symbol": sig.symbol, "side": sig.side},
                        )
                        continue

                    # e) Update broker price
                    price = bar_data["close"].get(sig.symbol, 100.0)
                    self.broker.update_price(sig.symbol, price)

                    # f) Submit via OrderManager (async → sync bridge)
                    sd = convert_signal(sig, self.portfolio, price_est=price)
                    slippage = price * self.slippage_bps / 10000
                    exec_price = price + slippage if sd["side"] == "buy" else price - slippage

                    # Cash constraint: cap buy qty to available cash
                    if sd["side"] == "buy":
                        max_affordable = max(0, int(
                            self.portfolio.cash / (exec_price * (1 + self.commission_bps / 10000))
                        ))
                        sd["qty"] = min(sd["qty"], max_affordable)
                        if sd["qty"] <= 0:
                            continue

                    commission = max(
                        self.min_commission,
                        self.commission_bps / 10000 * sd["qty"] * exec_price,
                    )

                    tracked = asyncio.run(
                        self.order_manager.submit(
                            sd["symbol"], sd["side"], sd["qty"],
                            order_type=sd.get("order_type", "market"),
                            strategy_name=strategy_name,
                            signal_id=sd.get("signal_id"),
                            limit_price=sd.get("limit_price"),
                        )
                    )

                    # g) Position tracking
                    if tracked and tracked.filled_qty > 0:
                        self.position_tracker.record_fill(
                            tracked.symbol, tracked.side, tracked.filled_qty,
                        )

                        # h) Investment record
                        self.investment_record.record_trade(
                            current_ts,
                            tracked.symbol,
                            tracked.side,
                            int(tracked.filled_qty),
                            tracked.avg_fill_price or exec_price,
                            commission,
                        )

                        # i) Update portfolio
                        pos = self.portfolio.positions.get(tracked.symbol)
                        if pos is None:
                            from engine.portfolio import Position
                            pos = Position(symbol=tracked.symbol)
                            self.portfolio.positions[tracked.symbol] = pos
                        delta = tracked.filled_qty if tracked.side == "buy" else -tracked.filled_qty
                        pos.add(delta, exec_price)
                        if tracked.side == "buy":
                            self.portfolio.cash -= (exec_price * tracked.filled_qty + commission)
                        else:
                            self.portfolio.cash += (exec_price * tracked.filled_qty - commission)

                    # Record signals
                    rank = 1
                    for sig in signals:
                        score = bar_data.get("pred", {}).get(sig.symbol, 0.0)
                        self.investment_record.record_signal(current_ts, sig.symbol, float(score), rank)
                        rank += 1

                # Mark equity (both portfolio and investment record)
                self.portfolio.mark_and_record(current_ts, bar_data)
                self.investment_record.record_equity(
                    current_ts, self.portfolio._mark_to_market(bar_data)
                )

            except Exception:
                log.exception("Bar %d (%s) failed — skipping", bar_idx, current_ts)
                self.portfolio.mark_and_record(current_ts, bar_data)
                self.investment_record.record_equity(
                    current_ts, self.portfolio._mark_to_market(bar_data)
                )

            pbar.update(1)

            # Realtime mode sleep
            if cfg.get("realtime") and cfg.get("realtime_interval", 0) > 0:
                _time.sleep(cfg["realtime_interval"])

        pbar.close()

        if skipped_bars:
            log.info("Skipped %d bars (market closed)", skipped_bars)

        # 4. Performance metrics
        result_obj = PaperResult(
            portfolio=self.portfolio, config=cfg, strategy_name=strategy_name,
        )
        metrics = engine_metrics_summary(result_obj)

        # 5. Output directory
        output_dir = cfg.get("output") or self._default_output_dir()
        os.makedirs(output_dir, exist_ok=True)

        # 6. Save archive
        self.investment_record.save(output_dir)

        # HTML report
        report_path = os.path.join(output_dir, "report.html")
        try:
            generate_html_report(result_obj, report_path)
        except Exception:
            log.warning("HTML report generation failed", exc_info=True)

        # 6b. Register in ExperimentTracker if experiment.id is configured
        exp_cfg = cfg.get("experiment", {})
        exp_id = exp_cfg.get("id", "")
        if exp_id:
            try:
                from experiment.tracker import ExperimentTracker
                tracker = ExperimentTracker()
                tracker.register_experiment(
                    exp_id=exp_id,
                    name=exp_cfg.get("name", exp_id),
                    hypothesis=exp_cfg.get("hypothesis", ""),
                    changes=exp_cfg.get("changes", []),
                )
                session_id = os.path.basename(output_dir)
                tracker.record_session(
                    exp_id=exp_id,
                    session_id=session_id,
                    session_type="paper_trading",
                    path=output_dir,
                )
                log.info("Session %s registered in experiment %s", session_id, exp_id)
            except Exception:
                log.warning("Experiment tracking failed (non-fatal)", exc_info=True)

        # 7. Summary
        self.print_summary(metrics, output_dir)

        return {
            "metrics": metrics,
            "output_dir": output_dir,
            "n_bars": n_bars,
            "skipped_bars": skipped_bars,
            "n_trades": len(self.investment_record._trades),
        }

    # ── Helpers ──

    def _resolve_strategy(self, name: str) -> type:
        """Resolve a strategy by qualified name.

        Tries in order:
        1. strategies.get_strategy(name)  (built-in)
        2. importlib dynamic import             (user module)
        """
        # Try built-in first
        try:
            return get_strategy(name)
        except ValueError:
            pass

        # Dynamic import: "my_module.MyStrategy"
        if "." in name:
            mod_name, cls_name = name.rsplit(".", 1)
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name, None)
            if cls is None:
                raise ValueError(f"Class {cls_name!r} not found in {mod_name!r}")
            return cls

        raise ValueError(
            f"Cannot resolve strategy {name!r}. "
            f"Use a built-in name ({', '.join(s['name'] for s in list_strategies())}) "
            f"or a qualified path like 'my_module.MyStrategy'."
        )

    def _instantiate_strategy(self, cls: type, kwargs: dict) -> Strategy:
        """Instantiate the strategy, applying kwargs overrides to annotated params."""
        instance = cls()

        # Override annotated parameters from kwargs
        for k in cls.__annotations__:
            if k in kwargs and not k.startswith("_"):
                setattr(instance, k, kwargs[k])

        return instance

    def _signals_to_orders(self, signals: list, bar_data: dict) -> list:
        """Convert Strategy signals to engine Order objects for risk checking."""
        from engine.strategy import Signal
        from engine.orders import Order as EngineOrder

        orders = []
        for sig in signals:
            if isinstance(sig, Signal):
                if sig.side in ("close", "sell"):
                    pos = self.portfolio.positions.get(sig.symbol)
                    size = pos.size if pos and hasattr(pos, "size") else 0
                    orders.append(EngineOrder(symbol=sig.symbol, side="sell", size=size))
                else:
                    weight = sig.weight or 1.0
                    close_prices = bar_data.get("close", {})
                    price_est = close_prices.get(sig.symbol, 100.0)
                    size = max(1, int(self.portfolio.total_equity * weight / price_est))
                    orders.append(EngineOrder(symbol=sig.symbol, side="buy", size=size))
        return orders

    def _default_output_dir(self) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(_PROJECT_ROOT / "output" / f"paper_{self.market}_{ts}")

    def print_summary(self, metrics: dict, output_dir: str):
        """Print a human-readable performance summary."""
        print()
        print("=" * 62)
        print(f"  📊  Paper Runner — {self.config.get('strategy', 'unknown')}")
        print("=" * 62)
        print(f"  Market:         {self.market.upper()}")
        print(f"  Capital:        {self.config.get('capital', 0):,.0f}")
        print(f"  Data source:    {self.config.get('data_source', 'simulated')}")
        print(f"  Period:         {self.config['start']} → {self.config['end']}")
        print("─" * 62)
        for k, v in metrics.items():
            if isinstance(v, float):
                if k.endswith("_bps") or k.endswith("_drawdown") or k.endswith("_return"):
                    print(f"  {k:<20s} {v * 100:>8.2f}%")
                elif "sharpe" in k or "sortino" in k or "calmar" in k:
                    print(f"  {k:<20s} {v:>8.2f}")
                else:
                    print(f"  {k:<20s} {v:>8.4f}")
            else:
                print(f"  {k:<20s} {v!s:>8}")
        print("─" * 62)
        print(f"  Output:         {output_dir}")
        print("=" * 62)
        print()


# ── CLI ──


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Paper Runner — multi-market paper-trading replay system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_paper.py --market hk --capital 1000000 --strategy BuyHold
  python run_paper.py --market us --strategy SimpleMomentum --lookback 20 --top-k 5
  python run_paper.py --market crypto --strategy MeanReversion
  python run_paper.py --config paper/config.yaml
  python run_paper.py --list-strategies
        """,
    )

    p.add_argument("--market", choices=["us", "hk", "crypto"],
                   help="Trading market")
    p.add_argument("--capital", type=float, default=100_000,
                   help="Initial capital (default: 100000)")
    p.add_argument("--strategy", type=str,
                   help="Strategy: built-in name (BuyHold, SimpleMomentum, MeanReversion) "
                        "or qualified path (mymod.MyStrategy)")
    p.add_argument("--start", type=str,
                   help="Start date YYYY-MM-DD (default: 1 year ago)")
    p.add_argument("--end", type=str,
                   help="End date YYYY-MM-DD (default: yesterday)")
    p.add_argument("--symbols", type=str, nargs="*",
                   help="Symbol list (default: market default pool)")
    p.add_argument("--universe", type=str, default="static",
                   help="Universe source: static, plate:XXX, bq:factor_id")
    p.add_argument("--data-source", type=str, default="simulated",
                   choices=["simulated", "parquet", "sdk", "bq"],
                   help="Data source type (default: simulated)")
    p.add_argument("--data-dir", type=str, default="./data",
                   help="Local data directory for parquet mode")
    p.add_argument("--output", type=str,
                   help="Output directory (default: auto-generated)")
    p.add_argument("--config", type=str,
                   help="YAML/JSON config file path")
    p.add_argument("--list-strategies", action="store_true",
                   help="List available built-in strategies and exit")
    p.add_argument("--realtime", action="store_true",
                   help="Enable realtime mode (sleep between bars)")
    p.add_argument("--realtime-interval", type=float, default=0.5,
                   help="Sleep seconds per bar in realtime mode (default: 0.5)")
    # Strategy parameter overrides (passed through argparse extras)
    p.add_argument("--lookback", type=int, help="Momentum / MeanReversion lookback period")
    p.add_argument("--top-k", type=int, help="Max positions (momentum / mean-reversion)")
    p.add_argument("--rebalance-every", type=int, help="Rebalance frequency in bars")
    p.add_argument("--allocation", type=float, help="Per-position allocation fraction")
    p.add_argument("--weight-per-symbol", type=float, help="BuyHold weight per symbol")
    p.add_argument("--entry-threshold", type=float, help="Z-score entry (MeanReversion)")
    p.add_argument("--exit-threshold", type=float, help="Z-score exit (MeanReversion)")
    # Experiment tracking
    p.add_argument("--experiment-id", type=str, help="ExperimentTracker experiment ID")
    p.add_argument("--experiment-name", type=str, help="Experiment name for tracking")

    return p


def _config_from_args(args) -> dict:
    """Build config dict from parsed CLI args."""

    # Determine market
    if not args.market:
        # Default to us for --list-strategies runs, but require it for actual runs
        raise ValueError("--market is required (us, hk, crypto)")

    market = args.market

    # Dates: default to 1 year ago → yesterday
    today = datetime.now()
    if args.end:
        end = args.end
    else:
        end = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    if args.start:
        start = args.start
    else:
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        start = (end_dt - timedelta(days=365)).strftime("%Y-%m-%d")

    symbols = args.symbols if args.symbols else default_symbols_for(market)

    # Resolve --universe
    universe_flag = getattr(args, "universe", "static") or "static"
    if universe_flag != "static":
        from paper.market import UniverseBuilder
        if universe_flag.startswith("bq:"):
            factor_id = universe_flag[3:]
            symbols = UniverseBuilder.from_bq(market, end, factor_id)
        elif universe_flag.startswith("plate:"):
            plate_name = universe_flag[6:]
            # Plate lookup: use the plate name as a symbol filter
            # For now, resolve plate as static symbols — extend as needed
            log.warning("Plate universe not yet backed by BQ; using static fallback")
            symbols = default_symbols_for(market)
        elif universe_flag == "static":
            symbols = args.symbols if args.symbols else default_symbols_for(market)
        else:
            log.warning("Unknown universe flag %r; using static", universe_flag)
            symbols = args.symbols if args.symbols else default_symbols_for(market)

    # Strategy kwargs from CLI overrides
    strategy_kwargs: dict = {}
    for key in [
        "lookback", "top_k", "rebalance_every", "allocation",
        "weight_per_symbol", "entry_threshold", "exit_threshold",
    ]:
        val = getattr(args, key.replace("-", "_"), None)
        if val is not None:
            strategy_kwargs[key] = val

    return {
        "market": market,
        "capital": args.capital,
        "strategy": args.strategy or "BuyHold",
        "strategy_kwargs": strategy_kwargs,
        "start": start,
        "end": end,
        "symbols": symbols,
        "data_source": args.data_source,
        "data_dir": args.data_dir,
        "output": args.output or None,
        "realtime": args.realtime,
        "realtime_interval": args.realtime_interval,
        "experiment": {
            "id": getattr(args, "experiment_id", None) or "",
            "name": getattr(args, "experiment_name", None) or "",
        },
    }


def _load_config_file(path: str) -> dict:
    """Load config from YAML (if PyYAML available) or JSON."""
    with open(path) as f:
        if path.endswith(".yaml") or path.endswith(".yml"):
            try:
                import yaml
                return yaml.safe_load(f)
            except ImportError:
                raise ImportError("PyYAML required for YAML config. Install: pip install pyyaml")
        return json.load(f)


def main():
    parser = build_parser()
    args = parser.parse_args()

    # --list-strategies
    if args.list_strategies:
        strategies = list_strategies()
        print("\n  Built-in Paper Strategies\n  " + "=" * 50)
        for s in strategies:
            print(f"  {s['name']:<20s} {s['doc']}")
            for k, v in s["parameters"].items():
                print(f"    {k}: {v}")
            print()
        return

    # --config file
    if args.config:
        config = _load_config_file(args.config)
        # CLI args override config file
        if args.market:
            config["market"] = args.market
        if args.capital != 100_000:
            config["capital"] = args.capital
        if args.strategy:
            config["strategy"] = args.strategy
        if args.start:
            config["start"] = args.start
        if args.end:
            config["end"] = args.end
        if args.symbols:
            config["symbols"] = args.symbols
        if args.data_source != "simulated":
            config["data_source"] = args.data_source
        if args.output:
            config["output"] = args.output
        if getattr(args, "experiment_id", None):
            config.setdefault("experiment", {})["id"] = args.experiment_id
        if getattr(args, "experiment_name", None):
            config.setdefault("experiment", {})["name"] = args.experiment_name
        config.setdefault("output", None)
        config.setdefault("strategy_kwargs", {})
    else:
        if not args.market:
            parser.error("--market is required (or use --config)")
        config = _config_from_args(args)

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    runner = PaperRunner(config)
    result = runner.run()

    if result["metrics"].get("total_return", 0) >= 0:
        log.info("✅ Paper run complete — positive return: %.2f%%",
                 result["metrics"]["total_return"] * 100)
    else:
        log.warning("⚠️  Paper run complete — negative return: %.2f%%",
                    result["metrics"]["total_return"] * 100)

    return result


if __name__ == "__main__":
    main()
