"""LiveRunner — unified paper/live trading loop.

Orchestrates a full trading loop over either:
- Paper mode: historical BigQuery data replayed bar-by-bar
- Live mode (single-day): real-time BQ polling for one trading day
- Live mode (multi-day): BQ polling across multiple trading days with
  state persistence, overnight position carry, and holiday-aware sleep.

Dependencies (already built):
- live/config.py → load_config(path)
- live/observer.py → Observer
- live/reporter.py → Reporter
- live/state.py → StateManager
- live/calendar.py → MarketCalendar
- live/bq_datasource.py → BQDataSource
- oms/broker/__init__.py → PaperBroker
- oms/manager.py → OrderManager
- oms/position.py → PositionTracker
- oms/bridge.py → convert_signal
- engine/strategy.py → Strategy, StrategyContext, Signal
- engine/portfolio.py → Portfolio, Position
- engine/data.py → DataFrameSource
- strategies/ml_pred.py → MLPredStrategy
- strategies/SimpleMomentum.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time as _time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from google.cloud import bigquery

from engine.data import DataFrameSource
from engine.portfolio import Portfolio, Position
from engine.strategy import StrategyContext
from live.market_calendar import MarketCalendar
from live.config import load_config
from live.observer import Observer
from dashboard.observer import DashboardObserver
from live.reporter import Reporter
from live.state import StateManager
from oms.bridge import convert_signal
from oms.manager import OrderManager
from oms.position import PositionTracker

logger = logging.getLogger(__name__)


class LiveRunner:
    """Unified orchestrator for paper and live trading loops.

    Parameters
    ----------
    config_path : str
        Path to YAML config file (see live/config.py for schema).
    """

    def __init__(self, config_path: str, config: dict | None = None):
        self.config_path = config_path
        self.config = config if config is not None else load_config(config_path)

        self.broker = None
        self.strategy = None
        self.order_manager = None
        self.position_tracker = None
        self.observer = None
        self.reporter = None

        self._mode = self.config.get("live", {}).get("mode", "paper")
        self._market = self.config.get("live", {}).get("market", "us")
        self._output_dir = self.config.get("_output_dir", "output/live/")

        self._slippage_bps = 5.0
        self._commission_bps = 1.0
        self._min_commission = 1.0

        # Multi-day state
        self._state_manager: StateManager | None = None
        self._calendar: MarketCalendar | None = None
        self._symbols: list[str] = []

        # Live run tracking (used by both single-day and multi-day modes)
        self._live_bar_count: int = 0
        self._live_peak_equity: float = 0.0
        self._live_bars: list[dict] = []
        self._live_start_time: datetime | None = None
        self._live_stop_reason: str | None = None
        self._live_daily_start_equity: float = 0.0

    # ── Main entry point ──────────────────────────────────────────────

    def run(self):
        """Run the trading loop (paper, live single-day, or live multi-day)."""
        multi_day = self.config.get("schedule", {}).get("multi_day", False)
        mode_label = f"{self._mode}" + (" (multi-day)" if multi_day else "")
        logger.info("LiveRunner starting — mode=%s market=%s", mode_label, self._market)

        # ── Experiment lifecycle integration ──
        exp_cfg = self.config.get("experiment", {})
        exp_id = exp_cfg.get("id", "")
        run_id = None

        if exp_id:
            from live.experiment_manager import ExperimentManager
            mgr = ExperimentManager()
            try:
                exp = mgr.get(exp_id)
                if exp.has_active_run:
                    # Run was pre-created by exp_cli.py — reuse it
                    run_id = exp.current_run
                    logger.info("Experiment %s using pre-created run %s", exp_id, run_id)
                    self.config["_run_id"] = run_id
                else:
                    run_id = mgr.start(exp_id)
                    logger.info("Experiment %s started -> run %s", exp_id, run_id)
                    self.config["_run_id"] = run_id
            except KeyError:
                # Not registered - auto-register
                _type = exp_cfg.get("type", "live")
                market = exp_cfg.get("market", "us")
                strategy = exp_cfg.get("strategy", "ml")
                version = exp_cfg.get("version", 1)
                config_path = getattr(self, '_config_path', '')
                mgr.register(_type, market, strategy, version, config_path,
                             name=exp_cfg.get("name", ""))
                run_id = mgr.start(exp_id)
                logger.info("Auto-registered %s -> run %s", exp_id, run_id)
                self.config["_run_id"] = run_id

        # ── Per‑run file logging header ──
        if exp_id and run_id:
            logger.info("=== Run %s started (mode=%s market=%s) ===",
                        run_id, mode_label, self._market)
            strat_name = self.config.get("strategy", {}).get("name", "unknown")
            logger.info("Config: strategy=%s experiment=%s", strat_name, exp_id)

        self._init_components()

        # Record experiment metadata early (before main loop) so Dashboard sees it
        try:
            from live.config import record_experiment
            record_experiment(self.config, self._output_dir)
        except Exception:
            pass

        try:
            if self._mode == "paper":
                self._run_paper_loop()
            elif self._mode == "live" and multi_day:
                self._run_live_multi_day_loop()
            elif self._mode == "live":
                self._run_live_loop()
            else:
                raise ValueError(f"Unknown mode: {self._mode}")
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.exception("Fatal error in run loop")
            if exp_id:
                from live.experiment_manager import ExperimentManager as EM
                try:
                    EM().fail(exp_id, notes=str(e))
                except Exception:
                    pass
            raise
        finally:
            # ── Experiment lifecycle cleanup ──
            if exp_id and run_id:
                import logging as _log
                try:
                    mgr = EM()
                    exp = mgr.get(exp_id)
                    if exp.has_active_run:
                        mgr.stop_run(exp_id, run_id)
                        _log.getLogger(__name__).info("Experiment %s run %s cleaned up", exp_id, run_id)
                except Exception:
                    _log.getLogger(__name__).exception("Failed to cleanup experiment %s", exp_id)
            self._shutdown()

    # ── Component initialisation ─────────────────────────────────────

    def _init_components(self):
        """Create broker, strategy, observer, reporter, order manager."""
        broker_cfg = self.config.get("broker", {})

        if self._mode == "paper":
            paper_cfg = broker_cfg.get("paper", {})
            initial_capital = float(paper_cfg.get("initial_capital", 100_000))
            self._slippage_bps = float(paper_cfg.get("slippage_bps", 5))
            self._commission_bps = float(paper_cfg.get("commission_bps", 1))
            self._min_commission = float(paper_cfg.get("min_commission", 1.0))
            from oms.broker import PaperBroker
            self.broker = PaperBroker(initial_capital=initial_capital)
            logger.info("PaperBroker initialised — capital=%.0f", initial_capital)
        else:
            live_cfg = broker_cfg.get("live", {})
            broker_type = live_cfg.get("type", "futu_stock")
            self._slippage_bps = float(live_cfg.get("slippage_bps", 5))
            self._commission_bps = float(live_cfg.get("commission_bps", 1))
            self._min_commission = float(live_cfg.get("min_commission", 1.0))
            if broker_type == "futu_stock":
                from oms.broker.futu_stock_broker import FutuStockBroker
                self.broker = FutuStockBroker(
                    host=live_cfg.get("host", "127.0.0.1"),
                    port=int(live_cfg.get("port", 11111)),
                )
            elif broker_type == "paper":
                from oms.broker import PaperBroker
                capital = float(live_cfg.get("initial_capital", 100_000))
                self.broker = PaperBroker(initial_capital=capital)
            else:
                raise ValueError(f"Unknown live broker type: {broker_type}")
            logger.info("LiveBroker initialised: %s", broker_type)

        # OrderManager + PositionTracker
        self.order_manager = OrderManager(self.broker)
        self.position_tracker = PositionTracker(self.broker)

        # Observer
        obs_cfg = self.config.get("observer", {})
        self.observer = Observer(
            output_dir=self._output_dir,
            snapshot_interval=int(obs_cfg.get("snapshot_interval", 60)),
        )

        # Dashboard BQ observer
        exp_id = self.config.get("experiment", {}).get("id", "unknown")
        self._dash_observer = DashboardObserver(exp_id, self._market)

        # Save config copy to output dir
        self._save_config_copy()

        # Reporter
        self.reporter = Reporter(output_dir=self._output_dir)

        # Strategy
        self._init_strategy()

        # Multi-day: init state manager + calendar
        state_cfg = self.config.get("state", {})
        if state_cfg.get("enabled", True):
            exp_id = self.config.get("experiment", {}).get("id", "unknown")
            run_id = self.config.get("_run_id", "unknown")
            state_dir = f"/var/quant/state/{exp_id}/{run_id}"
            self._state_manager = StateManager(state_dir)
        self._calendar = MarketCalendar(self._market)

        # Resolve symbols once
        self._resolve_symbols()

    def _resolve_symbols(self):
        """Resolve trading symbols from the 5m bars table (not factor_values).

        Factor_values includes stocks with only daily data — unusable for
        intraday trading.  The 5m bars table is the ground truth for which
        symbols have live intraday data.
        """
        strat_cfg = self.config.get("strategy", {})
        symbols = strat_cfg.get("symbols", [])
        if not symbols and self._mode == "live":
            market = self.config.get("live", {}).get("market", "us")
            table = {"us": "us_bars_5m", "hk": "hk_bars_5m", "crypto": "crypto_bars_5m"}[market]
            from google.cloud import bigquery as bq
            client = bq.Client(project="deductive-notch-495015-c2")
            df = client.query(
                f"SELECT DISTINCT symbol FROM quant.{table} ORDER BY symbol"
            ).result().to_dataframe()
            symbols = df["symbol"].tolist()
            # Normalize to canonical bare format (handles prefix + padding for both markets)
            from common.normalize import normalize_symbol
            symbols = [normalize_symbol(s, market) for s in symbols]
            symbols = list(dict.fromkeys(symbols))
        self._symbols = symbols
        if symbols:
            logger.info("Resolved %d symbols", len(symbols))

    def _init_strategy(self):
        """Instantiate the configured strategy."""
        strat_cfg = self.config.get("strategy", {})
        strat_name = strat_cfg.get("name", "SimpleMomentum")

        if strat_name == "MLPredStrategy":
            from strategies.ml_pred import MLPredStrategy
            self.strategy = MLPredStrategy()
            self.strategy.market = strat_cfg.get("market", self._market)
            self.strategy.top_k = int(strat_cfg.get("top_k", 10))
            self.strategy.rebalance_every = int(strat_cfg.get("rebalance_every", 5))
            self.strategy.model_type = strat_cfg.get("model_type", "lightgbm")
            self.strategy.train_start = strat_cfg.get("train_start", "2020-01-01")
            self.strategy.train_end = strat_cfg.get("train_end", "2025-12-31")
            # Model registry: load pre-trained model by name/version
            self.strategy.model_name = strat_cfg.get("model_name", "momentum_lgbm")
            self.strategy.model_version = strat_cfg.get("model_version", "latest")
        elif strat_name == "SimpleMomentum":
            from strategies import SimpleMomentum
            self.strategy = SimpleMomentum()
            self.strategy.lookback = int(strat_cfg.get("lookback", 20))
            self.strategy.top_k = int(strat_cfg.get("top_k", 5))
            self.strategy.rebalance_every = int(strat_cfg.get("rebalance_every", 5))
            self.strategy.allocation = float(strat_cfg.get("allocation", 0.0))
        else:
            raise ValueError(f"Unknown strategy name: {strat_name}")

        logger.info("Strategy initialised: %s", strat_name)

    def _save_config_copy(self):
        """Save the resolved config as YAML in the output directory."""
        try:
            out_dir = Path(self._output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = out_dir / "config.yaml"
            cfg_path.write_text(yaml.dump(self.config, default_flow_style=False))
            logger.info("Config saved to %s", cfg_path)
        except Exception:
            logger.exception("Failed to save config copy")

    # ── Paper trading loop ────────────────────────────────────────────

    def _run_paper_loop(self):
        """Run paper trading over historical BigQuery daily bar data.

        Date range: from config live.start_date / live.end_date,
        or fallback to Jan 1 of current year → today.
        """
        # 1. Date range (configurable, with fallback)
        today = datetime.now(timezone.utc)
        start_str = self.config.get("live", {}).get("start_date") or \
            datetime(today.year, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%d")
        end_str = self.config.get("live", {}).get("end_date") or \
            today.strftime("%Y-%m-%d")
        logger.info("Paper loop date range: %s → %s", start_str, end_str)

        # 2. Load BQ data
        symbol_filter = self.config.get("data", {}).get("symbols", None)
        bq_data = self._load_bq_data(start_str, end_str, symbol_filter)
        if bq_data is None:
            logger.error("No data loaded from BigQuery — aborting")
            return

        # 3. DataFrameSource
        src = DataFrameSource(
            close=bq_data["close"],
            open=bq_data.get("open"),
            high=bq_data.get("high"),
            low=bq_data.get("low"),
            volume=bq_data.get("volume"),
        )
        logger.info("DataFrameSource: %d bars x %d symbols", len(src), len(src.universe))

        if len(src) == 0:
            logger.error("Empty data source — aborting")
            return

        # 4. Portfolio
        broker_cfg = self.config.get("broker", {})
        paper_cfg = broker_cfg.get("paper", {})
        initial_capital = float(paper_cfg.get("initial_capital", 100_000))
        portfolio = Portfolio(initial_capital=initial_capital)

        # 5. StrategyContext
        ctx = StrategyContext(
            data=src,
            portfolio=portfolio,
            config={
                "symbols": list(src.universe),
                "market": self._market,
            },
        )

        # 6. Strategy on_init
        logger.info("Calling strategy.on_init() …")
        try:
            self.strategy.on_init(ctx, symbols=self._symbols)
        except TypeError:
            self.strategy.on_init(ctx)
        logger.info("Strategy initialised, entering bar loop (%d bars)", len(src))

        # 7. Bar loop
        last_progress_pct = -1
        total_bars = len(src)
        for bar_idx in range(total_bars):
            bar_data = src.iloc(bar_idx)
            timestamp = src.timestamp[bar_idx]

            # Progress logging every ~10%
            if total_bars > 100:
                pct = (bar_idx * 10) // total_bars
                if pct > last_progress_pct:
                    last_progress_pct = pct
                    logger.info("Paper progress: %d%% (%d/%d bars)", pct*10, bar_idx, total_bars)

            # 7a. Mark & record
            equity = portfolio.mark_and_record(timestamp, bar_data)
            ctx._set_bar_data(bar_data)

            # Update broker prices for current bar
            for sym in src.universe:
                price = bar_data["close"].get(sym, 100.0)
                self.broker.update_price(sym, price)

            # 7b. Snapshot
            if self.observer.snapshot_due(timestamp):
                self._snapshot_positions(portfolio, timestamp, bar_data)

            # 7c. Strategy signals
            try:
                signals = self.strategy.on_bar(ctx, bar_idx)
            except Exception:
                logger.exception("Strategy.on_bar failed at bar %d", bar_idx)
                signals = []

            # Log signals when they fire
            if signals:
                buys = [s for s in signals if s.side in ("buy", "target")]
                sells = [s for s in signals if s.side in ("sell", "close")]
                logger.info(
                    "Signals @ bar=%d: %d buy, %d sell — %s",
                    bar_idx, len(buys), len(sells),
                    ", ".join(f"{s.symbol}({s.side})" for s in signals[:10])
                )

            # 7d. No signals → record equity and continue
            if not signals:
                self.observer.record_bar(
                    timestamp,
                    equity=portfolio._mark_to_market(bar_data),
                    cash=portfolio.cash,
                    return_pct=0.0,
                )
                if hasattr(self, '_dash_observer'):
                    self._dash_observer.record_equity(
                        bar=bar_idx,
                        equity=portfolio._mark_to_market(bar_data),
                        cash=portfolio.cash,
                        portfolio_value=portfolio._mark_to_market(bar_data),
                        daily_pnl=getattr(portfolio, 'daily_pnl', 0),
                        drawdown=getattr(portfolio, 'drawdown', 0),
                        run_id=self.config.get("_run_id", ""),
                    )
                continue

            # 7e. Process signals
            buy_signals = [s for s in signals if s.side in ("buy", "target")]
            sell_close_signals = [s for s in signals if s.side in ("sell", "close")]

            # Normalise buy weights
            n_buy = len(buy_signals)
            if n_buy > 0:
                buy_weight = 1.0 / n_buy
            else:
                buy_weight = 1.0

            # Process sell/close signals first (free up cash)
            for sig in sell_close_signals:
                try:
                    self._process_signal(sig, portfolio, bar_data, timestamp,
                                         weight=1.0)
                except Exception:
                    logger.exception("Failed to process sell/close signal: %s %s",
                                     sig.symbol, sig.side)

            # Process buy/target signals
            for sig in buy_signals:
                try:
                    self._process_signal(sig, portfolio, bar_data, timestamp,
                                         weight=buy_weight)
                except Exception:
                    logger.exception("Failed to process buy signal: %s", sig.symbol)

            # 7h. Record equity bar
            final_equity = portfolio._mark_to_market(bar_data)
            self.observer.record_bar(
                timestamp,
                equity=final_equity,
                cash=portfolio.cash,
                return_pct=0.0,
            )
            if hasattr(self, '_dash_observer'):
                self._dash_observer.record_equity(
                    bar=bar_idx,
                    equity=final_equity,
                    cash=portfolio.cash,
                    portfolio_value=final_equity,
                    daily_pnl=getattr(portfolio, 'daily_pnl', 0),
                    drawdown=getattr(portfolio, 'drawdown', 0),
                    run_id=self.config.get("_run_id", ""),
                )

        # 8. End
        final_equity = portfolio._mark_to_market(bar_data if 'bar_data' in dir() else {})
        pnl = final_equity - initial_capital
        pnl_pct = (pnl / initial_capital * 100) if initial_capital else 0
        logger.info("Paper loop complete — equity=%.2f PnL=%.2f (%.2f%%) positions=%d total_bars=%d",
                    final_equity, pnl, pnl_pct, len(portfolio.positions), total_bars)

    def _process_signal(self, sig, portfolio, bar_data, timestamp, weight: float):
        """Process a single strategy signal: convert → execute → record.

        Parameters
        ----------
        sig : Signal
            Strategy signal from engine.strategy.
        portfolio : Portfolio
            Engine portfolio.
        bar_data : dict
            Current bar OHLCV + pred data.
        timestamp : datetime
            Current bar timestamp.
        weight : float
            Allocation weight for buy signals (used when qty is None).
        """
        symbol = sig.symbol
        if symbol not in bar_data.get("close", {}):
            logger.warning("Order skipped: %s not in bar_data — no price available", symbol)
            return
        price = bar_data["close"][symbol]

        # Convert signal
        converted = convert_signal(sig, portfolio, price_est=price)
        side = converted["side"]
        qty = converted["qty"]

        if qty <= 0:
            return

        # Apply slippage
        if side == "buy":
            exec_price = price * (1.0 + self._slippage_bps / 10000.0)
        else:
            exec_price = price * (1.0 - self._slippage_bps / 10000.0)

        # Commission
        notional = qty * exec_price
        commission = max(notional * self._commission_bps / 10000.0, self._min_commission)

        # Cash constraint for buys
        if side == "buy":
            cost = notional + commission
            if cost > portfolio.cash:
                qty = int(portfolio.cash / (exec_price + self._min_commission))
                if qty <= 0:
                    logger.debug("Insufficient cash for %s buy (cash=%.2f)", symbol, portfolio.cash)
                    return

        qty = max(1, int(qty))

        # Submit to broker via OrderManager (async → sync)
        logger.info("ORDER %s %s %d @ ~%.2f (notional=%.2f commission=%.2f)",
                    side.upper(), symbol, qty, exec_price, qty * exec_price, commission)
        tracked = asyncio.run(
            self.order_manager.submit(
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="market",
                strategy_name=type(self.strategy).__name__,
                signal_id=sig.signal_id,
            )
        )

        # Record signal
        self.observer.record_signal(
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            score=getattr(sig, "weight", 0.0) or 0.0,
            rank=0,
        )

        # Process fill
        if tracked.state in ("FILLED", "filled") and tracked.filled_qty > 0:
            fill_qty = int(tracked.filled_qty)
            fill_price = float(tracked.avg_fill_price or exec_price)
            logger.info("FILLED %s %s qty=%d price=%.2f (prior fill qty pre-calced=%d)",
                        side.upper(), symbol, fill_qty, fill_price, qty)

            if side == "buy":
                # Deduct cash
                portfolio.cash -= fill_qty * fill_price + commission
                # Update position
                if symbol not in portfolio.positions:
                    from engine.portfolio import Position
                    portfolio.positions[symbol] = Position(symbol=symbol, entry_price=fill_price)
                portfolio.positions[symbol].add(fill_qty, fill_price)
            else:  # sell
                # Add proceeds
                portfolio.cash += fill_qty * fill_price - commission
                # Update position
                if symbol in portfolio.positions and portfolio.positions[symbol].size > 0:
                    portfolio.positions[symbol].add(-fill_qty, fill_price)
                    # Clean up zero-size positions
                    if portfolio.positions[symbol].size == 0:
                        del portfolio.positions[symbol]

            # Record trade
            self.observer.record_trade(
                timestamp=timestamp,
                symbol=symbol,
                side=side,
                qty=fill_qty,
                price=fill_price,
                commission=commission,
            )
            if hasattr(self, '_dash_observer'):
                self._dash_observer.record_trade(
                    bar=getattr(self, '_live_bar_count', 0),
                    symbol=symbol,
                    side=side,
                    qty=fill_qty,
                    price=fill_price,
                    commission=commission,
                    run_id=self.config.get("_run_id", ""),
                )

            # Record in position tracker
            self.position_tracker.record_fill(symbol, side, fill_qty)

    def _snapshot_positions(self, portfolio, timestamp, bar_data):
        """Write current positions to observer snapshot."""
        positions_list = []
        for sym, pos in portfolio.positions.items():
            if pos.size == 0:
                continue
            close_price = bar_data["close"].get(sym, pos.avg_entry)
            mkt_value = pos.size * close_price
            cost_basis = pos.avg_entry * pos.size
            pnl_pct = ((close_price / pos.avg_entry) - 1.0) * 100.0 if pos.avg_entry > 0 else 0.0
            positions_list.append({
                "symbol": sym,
                "qty": pos.size,
                "price": close_price,
                "cost_basis": cost_basis,
                "mkt_value": mkt_value,
                "pnl_pct": pnl_pct,
            })
        self.observer.snapshot_portfolio(timestamp, positions_list)

    def _load_bq_data(self, start: str, end: str, symbols: list[str] | None = None):
        """Load daily bar data from BigQuery.

        Returns dict of DataFrames: {close, open, high, low, volume}
        or None if no data.
        """
        try:
            client = bigquery.Client()
        except Exception:
            logger.exception("Failed to create BigQuery client")
            return None

        table = f"quant.{self._market}_bars_1d"

        # If symbols not provided, query DISTINCT symbols first
        if symbols is None:
            sym_query = f"""
                SELECT DISTINCT symbol
                FROM `{table}`
                WHERE DATE(timestamp) BETWEEN @start AND @end
                ORDER BY symbol
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("start", "STRING", start),
                    bigquery.ScalarQueryParameter("end", "STRING", end),
                ]
            )
            sym_rows = client.query(sym_query, job_config=job_config).result()
            symbols = [row.symbol for row in sym_rows]
            logger.info("Discovered %d symbols from BQ", len(symbols))

        if not symbols:
            logger.warning("No symbols found for %s → %s", start, end)
            return None

        # Load bars
        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM `{table}`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp BETWEEN @start AND @end
            ORDER BY timestamp, symbol
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("symbols", "STRING", symbols),
                bigquery.ScalarQueryParameter("start", "STRING", start),
                bigquery.ScalarQueryParameter("end", "STRING", end),
            ]
        )
        df = client.query(query, job_config=job_config).to_dataframe()

        if df.empty:
            logger.warning("No bar data for %s → %s", start, end)
            return None

        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        # Normalize symbols to canonical bare format for engine compatibility
        from common.normalize import normalize_symbol_series
        df["symbol"] = normalize_symbol_series(df["symbol"], self._market)

        close = df.pivot_table(index="timestamp", columns="symbol", values="close").ffill()
        open_df = df.pivot_table(index="timestamp", columns="symbol", values="open").ffill()
        high = df.pivot_table(index="timestamp", columns="symbol", values="high").ffill()
        low = df.pivot_table(index="timestamp", columns="symbol", values="low").ffill()
        volume = df.pivot_table(index="timestamp", columns="symbol", values="volume").fillna(0)

        logger.info("Loaded BQ data: %d bars × %d symbols", len(close), len(symbols))
        return {"close": close, "open": open_df, "high": high, "low": low, "volume": volume}

    # ══════════════════════════════════════════════════════════════════
    # Live Trading — Single Day
    # ══════════════════════════════════════════════════════════════════

    def _run_live_loop(self):
        """Live trading for a single trading day.

        Creates a fresh portfolio and runs one day of BQ polling.
        For multi-day runs, use _run_live_multi_day_loop() instead.
        """
        broker_cfg = self.config.get("broker", {})
        live_cfg = broker_cfg.get("live", broker_cfg.get("paper", {}))
        initial_capital = float(live_cfg.get("initial_capital", 100_000))
        portfolio = Portfolio(initial_capital=initial_capital)

        live_state = self._fresh_live_state()
        symbols = self._symbols

        if not symbols:
            logger.error("No symbols resolved — cannot run live loop")
            return

        logger.info("Live mode (BQ poll): %d symbols — single day", len(symbols))
        stop_reason = self._run_one_live_day(portfolio, live_state, symbols)
        logger.info("Single-day live loop ended: %s", stop_reason)

    # ══════════════════════════════════════════════════════════════════
    # Live Trading — Multi-Day
    # ══════════════════════════════════════════════════════════════════

    def _run_live_multi_day_loop(self):
        """Multi-day live trading with state persistence.

        Outer loop:
        1. Load or create portfolio + live_state
        2. For each trading day:
           a. Wait until market opens
           b. Run one trading day (_run_one_live_day)
           c. Save state at end of day
           d. Check stop conditions (max_days, risk)
        3. Generate final report
        """
        schedule_cfg = self.config.get("schedule", {})
        max_trading_days = int(schedule_cfg.get("max_trading_days", 0))
        risk_cfg = self.config.get("risk", {})
        max_drawdown = float(risk_cfg.get("max_drawdown", 0.15))

        symbols = self._symbols
        if not symbols:
            logger.error("No symbols resolved — cannot run multi-day loop")
            return

        # ── 1. Load or create state ──
        portfolio, live_state = self._load_or_create_portfolio()
        state_mgr = self._state_manager

        trading_day = live_state.get("trading_day", 0)
        peak_equity = live_state.get("peak_equity", 0.0)

        logger.info(
            "Multi-day live loop: starting day %d, cash=%.2f, %d positions, %d symbols",
            trading_day + 1, portfolio.cash, len(portfolio.positions), len(symbols),
        )

        # ── 2. Strategy init (once) ──
        self._init_strategy_for_portfolio(portfolio, symbols)

        # ── 3. Per-day loop ──
        self._live_stop_reason = None
        self._live_bars: list[dict] = getattr(self, "_live_bars", [])
        self._live_start_time = datetime.now(timezone.utc)
        self._live_bar_count = live_state.get("bar_count", 0)
        self._live_peak_equity = peak_equity
        self._live_daily_start_equity = 0.0

        while True:
            trading_day += 1
            live_state["trading_day"] = trading_day

            # Check max days
            if max_trading_days > 0 and trading_day > max_trading_days:
                logger.info("Reached max_trading_days=%d — stopping", max_trading_days)
                self._live_stop_reason = "MAX_TRADING_DAYS"
                break

            # Wait for market open
            self._wait_for_market_open(trading_day)

            # Reset daily state
            self._live_daily_start_equity = 0.0
            self._live_bar_count = live_state.get("bar_count", self._live_bar_count)

            logger.info("── Day %d starting ── cash=%.2f positions=%d",
                        trading_day, portfolio.cash, len(portfolio.positions))

            # Run one trading day
            day_stop_reason = self._run_one_live_day(portfolio, live_state, symbols)
            logger.info("── Day %d ended: %s ── cash=%.2f positions=%d",
                        trading_day, day_stop_reason, portfolio.cash, len(portfolio.positions))

            # Update live_state from runner state
            live_state["bar_count"] = self._live_bar_count
            live_state["peak_equity"] = self._live_peak_equity
            live_state["last_bq_ts"] = getattr(self, '_bq_source', None) and self._bq_source.last_ts or live_state.get("last_bq_ts", "")

            # Save end-of-day state
            if state_mgr:
                state_mgr.save(portfolio, self.position_tracker, live_state)
                state_mgr.clear_checkpoint()

            # Stop if risk triggered (not just market close)
            if day_stop_reason and day_stop_reason != "market_close":
                self._live_stop_reason = day_stop_reason
                break

            # Check cumulative drawdown
            current_dd = (
                (self._live_peak_equity - portfolio._mark_to_market({}))
                / max(self._live_peak_equity, 1)
            )
            if current_dd >= max_drawdown:
                self._live_stop_reason = f"MAX_DRAWDOWN ({current_dd*100:.1f}%)"
                logger.warning("Cumulative drawdown stop: %s", self._live_stop_reason)
                break

        logger.info("Multi-day loop complete: %s", self._live_stop_reason or "normal")

    def _load_or_create_portfolio(self):
        """Load portfolio from saved state or create fresh.

        Returns (portfolio, live_state_dict).
        """
        broker_cfg = self.config.get("broker", {})
        live_cfg = broker_cfg.get("live", broker_cfg.get("paper", {}))
        initial_capital = float(live_cfg.get("initial_capital", 100_000))

        # Check for checkpoint first (crash recovery)
        if self._state_manager and self._state_manager.checkpoint_exists():
            cp = self._state_manager.load_checkpoint()
            if cp:
                portfolio = StateManager.restore_portfolio(
                    cp["portfolio_data"], Portfolio, Position
                )
                live_state = cp["live_state"]
                live_state["trading_day"] = cp.get("trading_day", 0)
                logger.info("Recovered from checkpoint: day %d, cash=%.2f",
                            live_state["trading_day"], portfolio.cash)
                return portfolio, live_state

        # Try full state
        if self._state_manager and self._state_manager.exists():
            state = self._state_manager.load()
            portfolio = StateManager.restore_portfolio(
                state["portfolio_data"], Portfolio, Position
            )
            live_state = state["live_state"]
            live_state["trading_day"] = state.get("trading_day", 0)

            # Restore position tracker
            for sym, qty in state.get("tracker_data", {}).items():
                self.position_tracker._positions[sym] = qty

            logger.info("Loaded state: day %d, cash=%.2f, %d positions",
                        live_state["trading_day"], portfolio.cash,
                        len(portfolio.positions))
            return portfolio, live_state

        # Fresh start
        portfolio = Portfolio(initial_capital=initial_capital)
        live_state = self._fresh_live_state()
        logger.info("Fresh portfolio: capital=%.0f", initial_capital)
        return portfolio, live_state

    @staticmethod
    def _fresh_live_state() -> dict:
        return {
            "trading_day": 0,
            "peak_equity": 0.0,
            "daily_start_equity": 0.0,
            "bar_count": 0,
            "last_bq_ts": "",
            "stop_reason": None,
        }

    def _init_strategy_for_portfolio(self, portfolio, symbols):
        """Initialize strategy with a (potentially non-empty) portfolio.

        Creates a DataFrameSource with proper symbol columns so that
        ctx.universe returns actual stock symbols (needed by strategies
        that query BQ for training data during on_init).
        """
        strat_cfg = self.config.get("strategy", {})
        # DataFrameSource expects: close=DataFrame(columns=symbols, index=timestamps)
        close = pd.DataFrame({sym: [float("nan")] for sym in symbols})
        src = DataFrameSource(close=close)
        ctx = StrategyContext(data=src, portfolio=portfolio, config={
            "symbols": symbols, **strat_cfg,
        })
        try:
            self.strategy.on_init(ctx, symbols=symbols)
        except TypeError:
            self.strategy.on_init(ctx)
        logger.info("Strategy on_init complete — %d symbols in universe", len(symbols))

    def _wait_for_market_open(self, trading_day: int):
        """Sleep until next market open. Logs progress every ~10 min."""
        if not self._calendar:
            return

        if self._calendar.is_open_now():
            logger.info("Market already open — proceeding with day %d", trading_day)
            return

        wait_sec = self._calendar.time_until_open()
        if wait_sec <= 0:
            return

        next_open = self._calendar.next_open_datetime()
        wait_min = wait_sec / 60
        hours = int(wait_min // 60)
        mins = int(wait_min % 60)

        logger.info(
            "Day %d: market closed — sleeping %dh%dm until %s UTC",
            trading_day, hours, mins,
            next_open.strftime("%Y-%m-%d %H:%M"),
        )

        # Sleep in 10-minute chunks with progress logging
        remaining = wait_sec
        chunk = 600  # 10 minutes
        while remaining > 0:
            sleep_dur = min(chunk, remaining)
            _time.sleep(sleep_dur)
            remaining -= sleep_dur

            if remaining > 0:
                # Check if still a trading day (weekends pass, holidays stay closed)
                if self._calendar.is_open_now():
                    logger.info("Market opened early — proceeding")
                    break

                hrs_left = int(remaining // 3600)
                min_left = int((remaining % 3600) // 60)
                if hrs_left > 0 or min_left % 30 == 0:
                    logger.debug("Day %d: %dh%dm until open …", trading_day, hrs_left, min_left)

        logger.info("Day %d: market open — resuming", trading_day)

    # ══════════════════════════════════════════════════════════════════
    # Live Trading — Core Daily Loop
    # ══════════════════════════════════════════════════════════════════

    def _run_one_live_day(self, portfolio, live_state, symbols):
        """Run one trading day of BQ polling.

        Parameters
        ----------
        portfolio : Portfolio
            Current portfolio (may have positions from previous days).
        live_state : dict
            Runtime state (peak_equity, bar_count, last_bq_ts, etc.).
        symbols : list[str]
            Trading symbols.

        Returns
        -------
        str
            Stop reason: "market_close", "max_drawdown", "daily_loss",
            "max_duration", "bq_failures", or None.
        """
        from live.bq_datasource import BQDataSource
        from engine.strategy import StrategyContext
        from engine.data import DataFrameSource
        from oms.bridge import convert_signal

        strat_cfg = self.config.get("strategy", {})
        risk_cfg = self.config.get("risk", {})
        schedule_cfg = self.config.get("schedule", {})

        poll_interval = int(schedule_cfg.get("bar_interval", 60))
        max_duration_min = int(schedule_cfg.get("max_duration_per_day",
                                schedule_cfg.get("max_duration_minutes", 390)))
        max_drawdown = float(risk_cfg.get("max_drawdown", 0.15))
        max_daily_loss = float(risk_cfg.get("max_daily_loss", 0.05))
        max_bq_failures = int(risk_cfg.get("max_consecutive_failures", 10))
        checkpoint_interval = int(self.config.get("state", {}).get("checkpoint_interval", 300))

        day_stop_reason: str | None = None
        day_start_time = datetime.now(timezone.utc)

        # Create or reuse BQDataSource
        if not hasattr(self, '_bq_source') or self._bq_source is None:
            self._bq_source = BQDataSource(
                symbols=symbols,
                market=self._market,
                poll_interval_sec=poll_interval,
            )
        else:
            # Resume from previous day — restore last_ts
            if live_state.get("last_bq_ts"):
                self._bq_source.last_ts = live_state["last_bq_ts"]
            self._bq_source.failure_count = 0

        source = self._bq_source
        source.stop_check = None  # will be re-set below

        # Rolling bar buffer per day
        self._live_bars: list[dict] = getattr(self, "_live_bars", [])

        # Ensure strategy context is set up with proper symbol columns
        close = pd.DataFrame({sym: [float("nan")] for sym in symbols})
        src_init = DataFrameSource(close=close)
        ctx_init = StrategyContext(data=src_init, portfolio=portfolio, config={
            "symbols": symbols, **strat_cfg,
        })
        # on_init is called once before the first day; subsequent days just need ctx rebuild

        def _rebuild_ctx() -> StrategyContext:
            """Rebuild StrategyContext from accumulated live bars.

            Converts the list-of-dicts format into proper wide-format
            DataFrames where columns = symbols.
            """
            n = len(self._live_bars)
            # Pivot bars into wide format: {symbol: [values across bars]}
            # Ensure all arrays have length n (pad missing with NaN)
            close_cols: dict = {sym: [float("nan")] * n for sym in symbols}
            open_cols: dict = {sym: [float("nan")] * n for sym in symbols}
            high_cols: dict = {sym: [float("nan")] * n for sym in symbols}
            low_cols: dict = {sym: [float("nan")] * n for sym in symbols}
            volume_cols: dict = {sym: [0.0] * n for sym in symbols}
            for i in range(n):
                bar = self._live_bars[i]
                bar_close = bar.get("close", {})
                bar_open = bar.get("open", {})
                bar_high = bar.get("high", {})
                bar_low = bar.get("low", {})
                bar_vol = bar.get("volume", {})
                for sym in symbols:
                    close_cols[sym][i] = bar_close.get(sym, float("nan"))
                    open_cols[sym][i] = bar_open.get(sym, float("nan"))
                    high_cols[sym][i] = bar_high.get(sym, float("nan"))
                    low_cols[sym][i] = bar_low.get(sym, float("nan"))
                    volume_cols[sym][i] = bar_vol.get(sym, 0.0)
            close_df = pd.DataFrame(close_cols)
            open_df = pd.DataFrame(open_cols)
            high_df = pd.DataFrame(high_cols)
            low_df = pd.DataFrame(low_cols)
            volume_df = pd.DataFrame(volume_cols)
            src2 = DataFrameSource(
                close=close_df, open=open_df, high=high_df,
                low=low_df, volume=volume_df,
            )
            src2.timestamp = [self._live_bars[i].get("timestamp", "") for i in range(n)]
            return StrategyContext(data=src2, portfolio=portfolio, config={
                "symbols": symbols, **strat_cfg,
            })

        last_checkpoint_time = day_start_time

        def on_live_bar(bar_data: dict):
            """Callback: BQDataSource feeds pre-batched bar_data."""
            nonlocal day_stop_reason, last_checkpoint_time
            try:
                ts = bar_data.get("timestamp", "")
                n_syms = len(bar_data.get("close", {}))
                if self._live_bar_count < 3:
                    logger.info("on_live_bar: bar #%d ts=%s syms=%d", self._live_bar_count + 1, ts, n_syms)

                # Append to rolling buffer
                self._live_bars.append(bar_data)
                if len(self._live_bars) > 500:
                    self._live_bars = self._live_bars[-500:]
                self._live_bar_count += 1

                portfolio.mark_and_record(ts, bar_data)
                eq = portfolio._mark_to_market(bar_data)

                # ── Drawdown tracking (cross-day) ──
                if eq > self._live_peak_equity:
                    self._live_peak_equity = eq
                    live_state["peak_equity"] = eq
                current_dd = (self._live_peak_equity - eq) / max(self._live_peak_equity, 1)

                # ── Daily loss tracking (reset per day) ──
                if self._live_daily_start_equity == 0:
                    self._live_daily_start_equity = eq
                daily_loss = (self._live_daily_start_equity - eq) / max(self._live_daily_start_equity, 1)

                # ── Risk stop checks ──
                if current_dd >= max_drawdown:
                    day_stop_reason = f"MAX_DRAWDOWN ({current_dd*100:.1f}%)"
                    logger.warning("Risk stop: %s", day_stop_reason)
                    source.stop()
                elif daily_loss >= max_daily_loss:
                    day_stop_reason = f"DAILY_LOSS ({daily_loss*100:.1f}%)"
                    logger.warning("Risk stop: %s", day_stop_reason)
                    source.stop()

                # ── Strategy ──
                live_ctx = _rebuild_ctx()
                bar_idx = len(self._live_bars) - 1
                signals = self.strategy.on_bar(live_ctx, bar_idx)

                if signals:
                    n_buy = sum(1 for s in signals if s.side in ("buy", "target"))
                    if n_buy > 0:
                        for s in signals:
                            if s.side in ("buy", "target") and s.weight is None:
                                s.weight = 1.0 / n_buy

                    for sig in signals:
                        last_prices = getattr(portfolio, '_last_prices', {})
                        if sig.symbol not in bar_data.get("close", {}):
                            fallback = last_prices.get(sig.symbol)
                            if not fallback or fallback <= 0:
                                logger.warning("Signal skipped: %s not in bar_data — no price available", sig.symbol)
                                continue
                            price = fallback
                            logger.info("Signal: %s using last_price=%.2f (not in bar_data)", sig.symbol, price)
                        else:
                            price = bar_data["close"][sig.symbol]
                        sd = convert_signal(sig, portfolio, price_est=price)

                        if sd["side"] == "buy":
                            max_qty = max(0, int(portfolio.cash / (price * 1.0001)))
                            sd["qty"] = min(sd["qty"], max_qty)
                            if sd["qty"] <= 0:
                                continue

                        tracked = asyncio.run(
                            self.order_manager.submit(
                                sd["symbol"], sd["side"], sd["qty"],
                                strategy_name=type(self.strategy).__name__,
                                signal_id=sd.get("signal_id"),
                            )
                        )
                        self.observer.record_signal(ts, sd["symbol"], sd["side"], sd.get("score", 0), sd.get("rank", 0))

                        if tracked and tracked.filled_qty > 0:
                            pos = portfolio.positions.get(tracked.symbol)
                            if pos is None:
                                pos = Position(symbol=tracked.symbol)
                                portfolio.positions[tracked.symbol] = pos
                            delta = tracked.filled_qty if tracked.side == "buy" else -tracked.filled_qty
                            pos.add(delta, price)
                            if tracked.side == "buy":
                                portfolio.cash -= price * tracked.filled_qty
                            else:
                                portfolio.cash += price * tracked.filled_qty
                            self.observer.record_trade(
                                ts, tracked.symbol, tracked.side,
                                int(tracked.filled_qty), price,
                            )
                            if hasattr(self, '_dash_observer'):
                                self._dash_observer.record_trade(
                                    bar=self._live_bar_count,
                                    symbol=tracked.symbol,
                                    side=tracked.side,
                                    qty=tracked.filled_qty,
                                    price=price,
                                    commission=getattr(tracked, 'commission', 0),
                                    run_id=self.config.get("_run_id", ""),
                                )

                # Periodic snapshot
                if self.observer.snapshot_due(ts):
                    pos_list = []
                    for sym, pos in portfolio.positions.items():
                        if hasattr(pos, "size") and pos.size > 0:
                            px = bar_data["close"].get(sym, 0)
                            cb = getattr(pos, "cost_basis", 0) or getattr(pos, "avg_price", 0)
                            pos_list.append({
                                "symbol": sym, "qty": pos.size,
                                "price": px, "cost_basis": cb,
                                "mkt_value": pos.size * px,
                                "pnl_pct": (px / cb - 1) * 100 if cb > 0 else 0,
                            })
                    self.observer.snapshot_portfolio(ts, pos_list)

                self.observer.record_bar(ts, eq, portfolio.cash, 0.0)

                if hasattr(self, '_dash_observer'):
                    self._dash_observer.record_equity(
                        bar=self._live_bar_count,
                        equity=eq,
                        cash=portfolio.cash,
                        portfolio_value=eq,
                        daily_pnl=eq - self._live_daily_start_equity if self._live_daily_start_equity > 0 else 0,
                        drawdown=current_dd,
                        run_id=self.config.get("_run_id", ""),
                    )

                # ── Intraday checkpoint ──
                now = datetime.now(timezone.utc)
                if (now - last_checkpoint_time).total_seconds() >= checkpoint_interval:
                    if self._state_manager:
                        # snapshot live_state first
                        live_state["bar_count"] = self._live_bar_count
                        live_state["peak_equity"] = self._live_peak_equity
                        live_state["last_bq_ts"] = source.last_ts or ""
                        self._state_manager.save_checkpoint(portfolio, live_state)
                    last_checkpoint_time = now

            except Exception:
                logger.exception("Live bar callback failed")

        def _should_stop() -> bool:
            """Check all termination conditions."""
            nonlocal day_stop_reason
            if day_stop_reason:
                return True
            # Duration limit
            if max_duration_min > 0:
                elapsed = (datetime.now(timezone.utc) - day_start_time).total_seconds() / 60
                if elapsed >= max_duration_min:
                    day_stop_reason = f"MAX_DURATION ({max_duration_min}min)"
                    logger.warning("Stop: %s", day_stop_reason)
                    return True
            # Consecutive BQ failures
            if source.failure_count >= max_bq_failures:
                day_stop_reason = f"BQ_FAILURES ({source.failure_count} consecutive)"
                logger.error("Stop: %s", day_stop_reason)
                return True
            return False

        source.stop_check = _should_stop
        source.on_bar = on_live_bar

        try:
            source.run()
        except Exception:
            logger.exception("BQDataSource.run() raised")
            day_stop_reason = day_stop_reason or "exception"

        # Determine final stop reason
        if day_stop_reason:
            return day_stop_reason
        if not self._calendar.is_open_now():
            return "market_close"
        return day_stop_reason or "stopped"

    # ── Shutdown ──────────────────────────────────────────────────────

    def _shutdown(self):
        """Close observer, generate report, clean up."""
        logger.info("Shutting down …")

        if self.observer:
            try:
                self.observer.close()
                logger.info("Observer closed")
            except Exception:
                logger.exception("Failed to close observer")

        if self.reporter:
            try:
                reason = getattr(self, '_live_stop_reason', None) or ""
                self.reporter.generate(stop_reason=reason)
                logger.info("Report generated")
            except Exception:
                logger.exception("Failed to generate report")

        # Register in ExperimentTracker if experiment.id is configured
        try:
            from live.config import record_experiment
            record_experiment(self.config, self._output_dir)
        except Exception:
            logger.exception("Failed to record experiment (non-fatal)")

        reason = getattr(self, '_live_stop_reason', None) or "normal"
        logger.info("LiveRunner shutdown complete — reason: %s", reason)
