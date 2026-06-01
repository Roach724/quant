"""LiveRunner — unified paper/live trading loop.

Orchestrates a full trading loop over either:
- Paper mode: historical BigQuery data replayed bar-by-bar
- Live mode: real-time WebSocket data feed (stub — NotImplementedError)

Dependencies (already built):
- live/config.py → load_config(path)
- live/observer.py → Observer
- live/reporter.py → Reporter
- oms/broker/__init__.py → PaperBroker
- oms/manager.py → OrderManager
- oms/position.py → PositionTracker
- oms/bridge.py → convert_signal
- engine/strategy.py → Strategy, StrategyContext, Signal
- engine/portfolio.py → Portfolio, Position
- engine/data.py → DataFrameSource
- strategies/ml_pred.py → MLPredStrategy
- paper/strategies.py → SimpleMomentum
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from google.cloud import bigquery

from engine.data import DataFrameSource
from engine.portfolio import Portfolio
from engine.strategy import StrategyContext
from live.config import load_config
from live.observer import Observer
from live.reporter import Reporter
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

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = load_config(config_path)

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

        self._dashboard_thread = None

    # ── Main entry point ──────────────────────────────────────────────

    def run(self):
        """Run the trading loop (paper or live, per config)."""
        logger.info("LiveRunner starting — mode=%s market=%s", self._mode, self._market)
        self._init_components()

        try:
            if self._mode == "paper":
                self._run_paper_loop()
            elif self._mode == "live":
                self._run_live_loop()
            else:
                raise ValueError(f"Unknown mode: {self._mode}")
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception:
            logger.exception("Fatal error in run loop")
            raise
        finally:
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

        # Save config copy to output dir
        self._save_config_copy()

        # Reporter
        self.reporter = Reporter(output_dir=self._output_dir)

        # Strategy
        self._init_strategy()

        # Dashboard (optional)
        dash_cfg = self.config.get("dashboard", {})
        if dash_cfg.get("websocket", False):
            self._start_dashboard(int(dash_cfg.get("port", 8090)))

    def _init_strategy(self):
        """Instantiate the configured strategy."""
        strat_cfg = self.config.get("strategy", {})
        strat_name = strat_cfg.get("name", "SimpleMomentum")

        if strat_name == "MLPredStrategy":
            from strategies.ml_pred import MLPredStrategy
            self.strategy = MLPredStrategy(
                market=strat_cfg.get("market", self._market),
                top_k=int(strat_cfg.get("top_k", 10)),
                rebalance_every=int(strat_cfg.get("rebalance_every", 5)),
                model_type=strat_cfg.get("model_type", "lightgbm"),
                train_start=strat_cfg.get("train_start", "2020-01-01"),
                train_end=strat_cfg.get("train_end", "2025-12-31"),
            )
        elif strat_name == "SimpleMomentum":
            from paper.strategies import SimpleMomentum
            self.strategy = SimpleMomentum(
                lookback=int(strat_cfg.get("lookback", 20)),
                top_k=int(strat_cfg.get("top_k", 5)),
                rebalance_every=int(strat_cfg.get("rebalance_every", 5)),
                allocation=float(strat_cfg.get("allocation", 0.0)),
            )
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

    def _start_dashboard(self, port: int):
        """Start a FastAPI + WebSocket dashboard in a daemon thread."""
        try:
            from live.dashboard import app
            import uvicorn

            def _serve():
                uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

            self._dashboard_thread = threading.Thread(
                target=_serve, daemon=True, name="dashboard"
            )
            self._dashboard_thread.start()
            logger.info("Dashboard started on port %d", port)
        except ImportError:
            logger.warning("Dashboard dependencies not installed; skipping")
        except Exception:
            logger.exception("Failed to start dashboard")

    # ── Paper trading loop ────────────────────────────────────────────

    def _run_paper_loop(self):
        """Run paper trading over historical BigQuery daily bar data.

        Date range: Jan 1 of current year → today (UTC).
        Data source: quant.us_bars_1d (filtered by symbol list if provided).
        """
        # 1. Date range
        today = datetime.now(timezone.utc)
        year_start = datetime(today.year, 1, 1, tzinfo=timezone.utc)
        start_str = year_start.strftime("%Y-%m-%d")
        end_str = today.strftime("%Y-%m-%d")
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
        self.strategy.on_init(ctx)
        logger.info("Strategy initialised, entering bar loop (%d bars)", len(src))

        # 7. Bar loop
        for bar_idx in range(len(src)):
            bar_data = src.iloc(bar_idx)
            timestamp = src.timestamp[bar_idx]

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

            # 7d. No signals → record equity and continue
            if not signals:
                self.observer.record_bar(
                    timestamp,
                    equity=portfolio._mark_to_market(bar_data),
                    cash=portfolio.cash,
                    return_pct=0.0,
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

        # 8. End
        final_equity = portfolio._mark_to_market(bar_data if 'bar_data' in dir() else {})
        logger.info("Paper loop complete — final equity: %.2f", final_equity)

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
        price = bar_data["close"].get(symbol, 100.0)

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

        # Strip market prefix (US., HK.) from symbols for engine compatibility
        df["symbol"] = df["symbol"].str.replace(r"^(?:US\.|HK\.)", "", regex=True)

        close = df.pivot_table(index="timestamp", columns="symbol", values="close").ffill()
        open_df = df.pivot_table(index="timestamp", columns="symbol", values="open").ffill()
        high = df.pivot_table(index="timestamp", columns="symbol", values="high").ffill()
        low = df.pivot_table(index="timestamp", columns="symbol", values="low").ffill()
        volume = df.pivot_table(index="timestamp", columns="symbol", values="volume").fillna(0)

        logger.info("Loaded BQ data: %d bars × %d symbols", len(close), len(symbols))
        return {"close": close, "open": open_df, "high": high, "low": low, "volume": volume}

    # ── Live trading loop (stub) ──────────────────────────────────────

    def _run_live_loop(self):
        """Live trading via BQ polling of us_bars_5m.

        Polls BigQuery every 60s for new 5m bars (fed by ws_collector → GCS →
        BQ loader). Accumulates bars in a rolling buffer, rebuilds
        StrategyContext each bar, runs strategy.on_bar(), processes signals,
        and submits orders through the live broker.

        Termination conditions (besides market close):
        - Max drawdown exceeded (config: risk.max_drawdown)
        - Daily loss limit (config: risk.max_daily_loss)
        - Max duration reached (config: schedule.max_duration_minutes)
        - Consecutive BQ poll failures (config: risk.max_consecutive_failures)
        """
        from live.bq_datasource import BQDataSource
        import pandas as pd
        import asyncio
        from datetime import datetime, timezone

        # Timing & risk state
        self._live_start_time = datetime.now(timezone.utc)
        self._live_bar_count = 0
        self._live_bq_failures = 0
        self._live_peak_equity = 0.0
        self._live_daily_start_equity = 0.0
        self._live_stop_reason: str | None = None

        # Resolve symbols
        strat_cfg = self.config.get("strategy", {})
        symbols = strat_cfg.get("symbols", [])
        if not symbols:
            from google.cloud import bigquery
            bq = bigquery.Client(project="deductive-notch-495015-c2")
            df = bq.query(
                "SELECT DISTINCT symbol FROM quant.factor_values "
                "WHERE source_builder = 'tech' ORDER BY symbol "
            ).result().to_dataframe()
            symbols = df["symbol"].tolist()

        logger.info("Live mode (BQ poll): %d symbols", len(symbols))

        # State
        from engine.portfolio import Portfolio, Position
        from engine.strategy import StrategyContext
        from engine.data import DataFrameSource
        from oms.bridge import convert_signal

        portfolio = Portfolio(initial_capital=100_000)
        self._live_bars: list[dict] = []

        # Strategy init
        empty_df = pd.DataFrame({
            "close": [{}], "open": [{}], "high": [{}], "low": [{}], "volume": [{}],
        })
        src = DataFrameSource(empty_df)
        ctx = StrategyContext(data=src, portfolio=portfolio, config={
            "symbols": symbols, **strat_cfg,
        })
        self.strategy.on_init(ctx)

        def _rebuild_ctx() -> StrategyContext:
            """Rebuild StrategyContext from accumulated live bars."""
            n = len(self._live_bars)
            df = pd.DataFrame({
                "close": [self._live_bars[i].get("close", {}) for i in range(n)],
                "open": [self._live_bars[i].get("open", {}) for i in range(n)],
                "high": [self._live_bars[i].get("high", {}) for i in range(n)],
                "low": [self._live_bars[i].get("low", {}) for i in range(n)],
                "volume": [self._live_bars[i].get("volume", {}) for i in range(n)],
            })
            src2 = DataFrameSource(df)
            src2.timestamp = [self._live_bars[i].get("timestamp", "") for i in range(n)]
            return StrategyContext(data=src2, portfolio=portfolio, config={
                "symbols": symbols, **strat_cfg,
            })

        def on_live_bar(bar_data: dict):
            """Callback: BQDataSource feeds pre-batched bar_data (all symbols at once)."""
            try:
                ts = bar_data.get("timestamp", "")

                # Append to rolling buffer (already batched by timestamp)
                self._live_bars.append(bar_data)
                if len(self._live_bars) > 500:
                    self._live_bars = self._live_bars[-500:]
                self._live_bar_count += 1

                portfolio.mark_and_record(ts, bar_data)
                eq = portfolio._mark_to_market(bar_data)

                # ── Drawdown tracking ──
                if eq > self._live_peak_equity:
                    self._live_peak_equity = eq
                current_dd = (self._live_peak_equity - eq) / max(self._live_peak_equity, 1)

                # ── Daily loss tracking ──
                ts_dt = None
                try:
                    from datetime import datetime as _dt
                    ts_dt = _dt.fromisoformat(str(ts).replace("Z", "+00:00"))
                except Exception:
                    pass
                if ts_dt and (self._live_daily_start_equity == 0 or ts_dt.time().hour == 0):
                    self._live_daily_start_equity = eq
                daily_loss = (self._live_daily_start_equity - eq) / max(self._live_daily_start_equity, 1)

                # ── Risk stop checks ──
                initial_capital = float(self.config.get("broker", {}).get("live", {}).get("initial_capital", 100_000))
                if current_dd >= max_drawdown:
                    self._live_stop_reason = f"MAX_DRAWDOWN ({current_dd*100:.1f}%)"
                    logger.warning("Risk stop: %s", self._live_stop_reason)
                elif daily_loss >= max_daily_loss:
                    self._live_stop_reason = f"DAILY_LOSS ({daily_loss*100:.1f}%)"
                    logger.warning("Risk stop: %s", self._live_stop_reason)

                # Rebuild context and run strategy
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
                        price = bar_data["close"].get(sig.symbol, 100)
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
                        self.observer.record_signal(ts, sd["symbol"], sd["side"], 0, 1)

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

            except Exception:
                logger.exception("Live bar callback failed")

        poll_interval = self.config.get("schedule", {}).get("bar_interval", 60)
        max_duration_min = self.config.get("schedule", {}).get("max_duration_minutes", 0)
        risk_cfg = self.config.get("risk", {})
        max_drawdown = float(risk_cfg.get("max_drawdown", 0.15))
        max_daily_loss = float(risk_cfg.get("max_daily_loss", 0.05))
        max_bq_failures = int(risk_cfg.get("max_consecutive_failures", 10))

        source = BQDataSource(
            symbols=symbols,
            market=self._market,
            poll_interval_sec=poll_interval,
        )

        def _should_stop() -> bool:
            """Check all termination conditions. Returns True if should stop."""
            # Risk stop triggered in on_live_bar
            if self._live_stop_reason:
                return True
            # Duration limit
            if max_duration_min > 0:
                elapsed = (datetime.now(timezone.utc) - self._live_start_time).total_seconds() / 60
                if elapsed >= max_duration_min:
                    self._live_stop_reason = f"MAX_DURATION ({max_duration_min}min)"
                    logger.warning("Stop: %s", self._live_stop_reason)
                    return True
            # Consecutive BQ failures
            if source.failure_count >= max_bq_failures:
                self._live_stop_reason = f"BQ_FAILURES ({source.failure_count} consecutive)"
                logger.error("Stop: %s", self._live_stop_reason)
                return True
            return False

        source.stop_check = _should_stop
        source.on_bar = on_live_bar
        source.run()

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
            record_experiment(self.config, self.output_dir)
        except Exception:
            logger.exception("Failed to record experiment (non-fatal)")

        reason = getattr(self, '_live_stop_reason', None) or "normal"
        logger.info("LiveRunner shutdown complete — reason: %s", reason)
