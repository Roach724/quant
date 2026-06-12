"""交易运行器 — 数据源 → 策略信号 → Futu 下单"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from engine.data import DataFrameSource
from engine.portfolio import Portfolio
from engine.strategy import StrategyContext
from trading.adapter import StrategyAdapter
from trading.capital import CapitalManager
from trading.state import TradingStateManager
from trading.signal_bridge import SignalBridge
from trading.models import TradingStrategy as TSModel

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
        self._strategies: dict[int, TSModel] = {
            s.id: s for s in strategies
        }
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

        import yaml
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
            args=(strat.id, adapter, stop),
            daemon=True,
            name=f"trading-{strat.id}",
        )
        self._threads[strat.id] = thread
        thread.start()
        logger.info(
            "Started %s (#%d) with $%.0f",
            strat.name, strat.id, strat.capital_allocated,
        )

    def _run_loop(
        self,
        strategy_id: int,
        adapter: StrategyAdapter,
        stop: threading.Event,
    ):
        """策略主循环"""
        try:
            from live.bq_datasource import BQDataSource
            from common.normalize import normalize_symbol

            market = self._strategies[strategy_id].market
            # Load symbols from BQ discovery
            source = BQDataSource(
                symbols=[],  # empty = auto-discover
                market=market,
                poll_interval_sec=self.bar_interval,
            )
            source.start()
            time.sleep(3)  # wait for initial data

            # Build initial context
            ctx = None
            bar_count = 0

            while not stop.is_set():
                try:
                    bar_data = source.get_latest()
                    if bar_data is None:
                        time.sleep(self.bar_interval)
                        continue

                    close_prices = bar_data.get("close", {})
                    symbols = list(close_prices.keys())

                    if adapter._strategy is None and symbols:
                        ctx = self._make_context(bar_data, symbols)
                        adapter.load(symbols, ctx)

                    if adapter._strategy and ctx:
                        ctx._set_bar_data(bar_data)
                        signals = adapter.generate_signals(
                            ctx, bar_count, strategy_id,
                        )
                        if signals:
                            logger.info(
                                "Strategy %d: %d signals at bar %d",
                                strategy_id, len(signals), bar_count,
                            )
                            self._execute_signals(signals, bar_data)

                    # Periodic reconciliation
                    if bar_count > 0 and bar_count % self.reconcile_every == 0:
                        self.state.reconcile_and_continue(strategy_id)

                    bar_count += 1
                    time.sleep(self.bar_interval)

                except Exception:
                    logger.exception(
                        "Strategy %d loop error at bar %d",
                        strategy_id, bar_count,
                    )
                    time.sleep(self.bar_interval)

        except Exception:
            logger.exception("Strategy %d loop fatal", strategy_id)
        finally:
            logger.info("Strategy %d loop exited", strategy_id)

    def _make_context(
        self, bar_data: dict, symbols: list[str],
    ) -> StrategyContext:
        """构造策略上下文"""
        close = pd.DataFrame([bar_data.get("close", {})])
        src = DataFrameSource(close=close)
        pf = Portfolio(initial_capital=0)
        return StrategyContext(
            data=src, portfolio=pf, config={"symbols": symbols},
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
                    "Execute failed for %s", sig.symbol,
                )
