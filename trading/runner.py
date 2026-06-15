"""交易运行器 — 数据源 → 策略信号 → Futu 下单

Supports single-session and multi-day modes.
Multi-day: state persistence across trading days with MarketCalendar awareness.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime

import pandas as pd
import yaml

from engine.data import DataFrameSource
from engine.portfolio import Portfolio
from engine.strategy import StrategyContext
from trading.adapter import StrategyAdapter
from trading.capital import CapitalManager
from trading.models import TradingStrategy as TSModel
from trading.signal_bridge import SignalBridge
from trading.state import TradingStateManager

logger = logging.getLogger(__name__)


class TradingRunner:
    """交易运行器 — 管理所有策略的生命周期。

    每个启用的策略在独立线程中运行数据轮询循环。
    """

    def __init__(
        self,
        broker,
        capital: CapitalManager,
        state: TradingStateManager,
        bridge: SignalBridge,
        strategies: list[TSModel],
        market: str = "us",
        bar_interval: int = 60,
        reconcile_every: int = 10,
    ):
        self.broker = broker
        self.capital = capital
        self.state = state
        self.bridge = bridge
        self.market = market
        self.bar_interval = bar_interval
        self.reconcile_every = reconcile_every
        self._strategies: dict[int, TSModel] = {s.id: s for s in strategies}
        self._adapters: dict[int, StrategyAdapter] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._stop_events: dict[int, threading.Event] = {}
        self._running = False

    # ── Lifecycle ──

    def start(self):
        """启动所有 running 状态的策略"""
        if self._running:
            return
        self._running = True
        for strat in self._strategies.values():
            if strat.status == "running":
                self._start_one(strat)
        logger.info(
            "TradingRunner started: %d strategies",
            len(self._threads),
        )

    def stop(self):
        """停止所有策略"""
        self._running = False
        for sid, stop in self._stop_events.items():
            stop.set()
        for sid, thread in self._threads.items():
            thread.join(timeout=30)
        self._threads.clear()
        self._stop_events.clear()
        logger.info("TradingRunner stopped")

    def start_strategy(self, strat: TSModel):
        """启动单个策略"""
        self._strategies[strat.id] = strat
        self._start_one(strat)

    def stop_strategy(self, strategy_id: int):
        """停止单个策略"""
        if strategy_id in self._stop_events:
            self._stop_events[strategy_id].set()

    # ── Internal ──

    def _start_one(self, strat: TSModel):
        """启动单个策略的轮询线程"""
        if strat.id in self._threads:
            return

        cfg = yaml.safe_load(strat.config_yaml) or {}
        strat_kwargs = cfg.get("strategy", {})
        kwargs = {k: v for k, v in strat_kwargs.items() if k != "name"}

        adapter = StrategyAdapter(strat.strategy_class, kwargs, strat.market)
        self._adapters[strat.id] = adapter

        self.capital.allocate(strat.id, strat.capital_allocated)

        stop = threading.Event()
        self._stop_events[strat.id] = stop

        thread = threading.Thread(
            target=self._run_loop,
            args=(strat.id, adapter, stop, cfg),
            daemon=True,
            name=f"trading-{strat.id}",
        )
        self._threads[strat.id] = thread
        thread.start()
        logger.info(
            "Started %s (#%d) with $%.0f",
            strat.name,
            strat.id,
            strat.capital_allocated,
        )

    def _run_loop(
        self,
        strategy_id: int,
        adapter: StrategyAdapter,
        stop: threading.Event,
        cfg: dict,
    ):
        """策略主循环 — 单日或多多日模式"""
        schedule = cfg.get("schedule", {})
        multi_day = schedule.get("multi_day", False)

        if multi_day:
            self._run_multi_day(strategy_id, adapter, stop, cfg)
        else:
            self._run_single_day(strategy_id, adapter, stop)

    @staticmethod
    def _resolve_bq_symbols(market: str) -> list[str]:
        """Resolve trading symbols from the BQ 5m bars table."""
        table = {"us": "us_bars_5m", "hk": "hk_bars_5m", "crypto": "crypto_bars_5m"}[market]
        from google.cloud import bigquery as bq

        client = bq.Client(project="deductive-notch-495015-c2")
        df = client.query(f"SELECT DISTINCT symbol FROM quant.{table} ORDER BY symbol").result().to_dataframe()
        from common.normalize import normalize_symbol

        symbols = [normalize_symbol(str(s), market) for s in df["symbol"].tolist()]
        return list(dict.fromkeys(symbols))  # deduplicate

    def _run_single_day(
        self,
        strategy_id: int,
        adapter: StrategyAdapter,
        stop: threading.Event,
    ):
        """单日轮询循环 — 使用 BQDataSource on_bar 回调模式"""
        try:
            from live.bq_datasource import BQDataSource

            market = self._strategies[strategy_id].market
            symbols = self._resolve_bq_symbols(market)
            logger.info(
                "Strategy %d: resolved %d symbols from BQ",
                strategy_id,
                len(symbols),
            )
            source = BQDataSource(
                symbols=symbols,
                market=market,
                poll_interval_sec=self.bar_interval,
            )

            _ctx = {"ctx": None}
            _bar_count = 0

            def _on_bar(bar_data: dict):
                nonlocal _bar_count
                close_prices = bar_data.get("close", {})
                symbols = list(close_prices.keys())

                if adapter._strategy is None and symbols:
                    _ctx["ctx"] = self._make_context(bar_data, symbols)
                    adapter.load(symbols, _ctx["ctx"])

                if adapter._strategy and _ctx["ctx"]:
                    _ctx["ctx"]._set_bar_data(bar_data)
                    signals = adapter.generate_signals(
                        _ctx["ctx"],
                        _bar_count,
                        strategy_id,
                    )
                    if signals:
                        logger.info(
                            "Strategy %d: %d signals at bar %d",
                            strategy_id,
                            len(signals),
                            _bar_count,
                        )
                        self._execute_signals(signals, bar_data)

                if _bar_count > 0 and _bar_count % self.reconcile_every == 0:
                    self.state.reconcile_and_continue(strategy_id)

                _bar_count += 1

            source.stop_check = lambda: stop.is_set()
            source.on_bar = _on_bar
            source.run()

        except Exception:
            logger.exception("Strategy %d loop fatal", strategy_id)
        finally:
            logger.info("Strategy %d loop exited", strategy_id)

    def _run_multi_day(
        self,
        strategy_id: int,
        adapter: StrategyAdapter,
        stop: threading.Event,
        cfg: dict,
    ):
        """多日交易循环 — BQDataSource on_bar 回调 + 跨日状态持久化"""
        schedule = cfg.get("schedule", {})
        state_cfg = cfg.get("state", {})
        risk_cfg = cfg.get("risk", {})
        max_trading_days = int(schedule.get("max_trading_days", 0))
        max_duration_per_day = int(schedule.get("max_duration_per_day", 390))
        _max_drawdown = float(risk_cfg.get("max_drawdown", 0.15))  # TODO: implement drawdown check
        bar_interval = int(schedule.get("bar_interval", self.bar_interval))

        try:
            from live.bq_datasource import BQDataSource
            from live.market_calendar import MarketCalendar
            from live.state import StateManager

            market = self._strategies[strategy_id].market
            calendar = MarketCalendar(market)

            # ── State persistence ──
            state_dir = state_cfg.get("dir", f"/var/data/trading/state/strategy_{strategy_id}/")
            state_mgr = None
            if state_cfg.get("enabled", True):
                state_mgr = StateManager(state_dir)

            # ── Load or init state ──
            trading_day = 0
            bar_count = 0
            peak_equity = 0.0
            if state_mgr and state_mgr.exists():
                try:
                    saved = state_mgr.load()
                    trading_day = saved.get("trading_day", 0)
                    bar_count = saved.get("live_state", {}).get("bar_count", 0)
                    peak_equity = saved.get("live_state", {}).get("peak_equity", 0.0)
                    logger.info(
                        "Strategy %d: restored multi-day state (day=%d, bars=%d)",
                        strategy_id,
                        trading_day,
                        bar_count,
                    )
                except Exception:
                    logger.warning(
                        "Strategy %d: failed to load state, starting fresh",
                        strategy_id,
                    )

            # ── BQ data source ──
            symbols = self._resolve_bq_symbols(market)
            logger.info(
                "Strategy %d: resolved %d symbols from BQ",
                strategy_id,
                len(symbols),
            )
            source = BQDataSource(
                symbols=symbols,
                market=market,
                poll_interval_sec=bar_interval,
            )

            _ctx = {"ctx": None}
            day_bars = 0
            day_start_ts = None
            day_stop_reason = None
            stop_reason = None

            def _on_bar(bar_data: dict):
                """Per-bar callback — called by BQDataSource._poll()"""
                nonlocal bar_count, day_bars, peak_equity, day_start_ts

                close_prices = bar_data.get("close", {})
                symbols = list(close_prices.keys())

                if adapter._strategy is None and symbols:
                    _ctx["ctx"] = self._make_context(bar_data, symbols)
                    adapter.load(symbols, _ctx["ctx"])
                    day_start_ts = bar_data.get("timestamp")

                if adapter._strategy and _ctx["ctx"]:
                    _ctx["ctx"]._set_bar_data(bar_data)
                    signals = adapter.generate_signals(
                        _ctx["ctx"],
                        bar_count,
                        strategy_id,
                    )
                    if signals:
                        logger.info(
                            "Strategy %d: %d signals at bar %d (day %d)",
                            strategy_id,
                            len(signals),
                            bar_count,
                            trading_day,
                        )
                        self._execute_signals(signals, bar_data)

                bar_count += 1
                day_bars += 1

                # Heartbeat every 30 bars to confirm the loop is alive
                if bar_count % 30 == 0:
                    logger.debug(
                        "Strategy %d: heartbeat — bar=%d day_bars=%d",
                        strategy_id,
                        bar_count,
                        day_bars,
                    )

                # Periodic checkpoint
                checkpoint_interval = int(state_cfg.get("checkpoint_interval", 300))
                if state_mgr and bar_count > 0 and bar_count % checkpoint_interval == 0:
                    try:
                        state_mgr.save_checkpoint(
                            None,
                            {
                                "trading_day": trading_day,
                                "bar_count": bar_count,
                                "peak_equity": peak_equity,
                            },
                        )
                    except Exception:
                        pass

            source.on_bar = _on_bar

            acct = self.capital.get_account(strategy_id)
            cash = acct.cash if acct else 0.0
            positions = self.capital.get_positions(strategy_id)

            logger.info(
                "Strategy %d: multi-day loop starting — day=%d cash=$%.2f positions=%d max_days=%d",
                strategy_id,
                trading_day + 1,
                cash,
                len(positions),
                max_trading_days,
            )

            while not stop.is_set():
                trading_day += 1

                # Check max trading days
                if max_trading_days > 0 and trading_day > max_trading_days:
                    logger.info(
                        "Strategy %d: max_trading_days=%d reached",
                        strategy_id,
                        max_trading_days,
                    )
                    stop_reason = "MAX_TRADING_DAYS"
                    break

                # Wait for market open
                self._wait_for_market_open(calendar, bar_interval)
                if stop.is_set():
                    break

                # ── Resumed after market open ──
                logger.info(
                    "Strategy %d: Day %d market open — resuming",
                    strategy_id,
                    trading_day,
                )

                # Check if checkpoint exists (crash recovery)
                if state_mgr and state_mgr.checkpoint_exists():
                    try:
                        cp = state_mgr.load_checkpoint()
                        if cp:
                            bar_count = cp.get("bar_count", bar_count)
                            peak_equity = cp.get("peak_equity", peak_equity)
                            logger.info(
                                "Strategy %d: recovered from checkpoint",
                                strategy_id,
                            )
                    except Exception:
                        logger.warning(
                            "Strategy %d: failed to load checkpoint",
                            strategy_id,
                        )

                # ── Reset day state ──
                day_bars = 0
                day_start_ts = None
                day_stop_reason = None
                max_bars_per_day = max_duration_per_day * 60 // bar_interval if bar_interval > 0 else 390

                # Get current account state
                acct = self.capital.get_account(strategy_id)
                cash = acct.cash if acct else 0.0
                positions = self.capital.get_positions(strategy_id)
                pos_count = len(positions)

                logger.info(
                    "Strategy %d: ── Day %d starting ── cash=$%.2f positions=%d max_bars=%d",
                    strategy_id,
                    trading_day,
                    cash,
                    pos_count,
                    max_bars_per_day,
                )

                # ── Set up stop conditions for this day ──
                def _should_stop():
                    nonlocal day_stop_reason
                    if stop.is_set():
                        day_stop_reason = "stopped"
                        return True
                    if not calendar.is_open_now():
                        day_stop_reason = "market_close"
                        return True
                    if day_bars >= max_bars_per_day:
                        day_stop_reason = f"MAX_BARS ({max_bars_per_day})"
                        logger.warning("Strategy %d: %s", strategy_id, day_stop_reason)
                        return True
                    return False

                source.stop_check = _should_stop
                source.on_bar = _on_bar

                # ── Run one trading day (blocking) ──
                try:
                    source.run()
                    day_stop_reason = day_stop_reason or "market_close"
                except Exception:
                    logger.exception(
                        "Strategy %d: BQDataSource.run() raised at day %d",
                        strategy_id,
                        trading_day,
                    )
                    day_stop_reason = day_stop_reason or "exception"

                # ── End of day: save state ──
                logger.info(
                    "Strategy %d: ── Day %d ended (%s) ── bars=%d total=%d",
                    strategy_id,
                    trading_day,
                    day_stop_reason or "unknown",
                    day_bars,
                    bar_count,
                )

                if state_mgr:
                    try:
                        state_mgr.save(
                            None,
                            None,
                            {
                                "trading_day": trading_day,
                                "bar_count": bar_count,
                                "peak_equity": peak_equity,
                                "stop_reason": day_stop_reason,
                                "last_bq_ts": str(datetime.now(UTC)),
                            },
                        )
                        state_mgr.clear_checkpoint()
                        logger.debug(
                            "Strategy %d: state saved for day %d",
                            strategy_id,
                            trading_day,
                        )
                    except Exception:
                        logger.exception(
                            "Strategy %d: failed to save state",
                            strategy_id,
                        )

                # Check non-market stop reasons
                if day_stop_reason and day_stop_reason not in ("market_close",):
                    stop_reason = day_stop_reason
                    break

            logger.info(
                "Strategy %d: multi-day loop complete (%s)",
                strategy_id,
                stop_reason or "stopped",
            )

        except Exception:
            logger.exception("Strategy %d: multi-day loop fatal", strategy_id)
        finally:
            logger.info("Strategy %d: multi-day loop exited", strategy_id)

    @staticmethod
    def _wait_for_market_open(calendar, poll_sec: int = 60):
        """Sleep until market opens, polling every poll_sec seconds."""
        _logged = False
        while True:
            if calendar.is_open_now():
                return
            next_open = calendar.next_open_datetime()
            if next_open:
                wait_sec = max((next_open - datetime.now(UTC)).total_seconds(), poll_sec)
            else:
                wait_sec = poll_sec
            if not _logged:
                logger.info(
                    "Waiting for market open — next open at %s (%s)",
                    next_open.isoformat() if next_open else "unknown",
                    _format_duration(int(wait_sec)),
                )
                _logged = True
            else:
                logger.debug("Waiting for market open — sleeping %d s", int(wait_sec))
            time.sleep(min(wait_sec, poll_sec))

    def _make_context(
        self,
        bar_data: dict,
        symbols: list[str],
    ) -> StrategyContext:
        """构造策略上下文"""
        close = pd.DataFrame([bar_data.get("close", {})])
        src = DataFrameSource(close=close)
        pf = Portfolio(initial_capital=0)
        return StrategyContext(
            data=src,
            portfolio=pf,
            config={"symbols": symbols},
        )

    def _execute_signals(self, signals, bar_data: dict):
        """执行信号列表"""
        close_prices = bar_data.get("close", {})
        for sig in signals:
            current_price = close_prices.get(sig.symbol, 0)
            if current_price <= 0:
                continue
            try:
                asyncio.run(self.bridge.execute(sig, current_price))
            except Exception:
                logger.exception(
                    "Execute failed for %s",
                    sig.symbol,
                )


def _format_duration(seconds: int) -> str:
    """Format seconds as human-readable duration string."""
    if seconds < 120:
        return f"{seconds}s"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"
