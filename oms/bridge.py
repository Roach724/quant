"""Bridge between quant.engine Strategy signals and quant.oms order management.

Connects the synchronous backtesting engine to the asynchronous OMS/broker layer.
The bridge handles: signal conversion, sync→async transition, side translation,
and portfolio reconciliation.
"""

import asyncio
from engine.strategy import Signal


def convert_signal(signal: Signal, portfolio) -> dict:
    """Convert an engine Signal to execution-algo-compatible dict format.

    Maps 'close'/'target' sides to 'buy'/'sell', computes qty from weight
    if not explicitly set on the signal.
    """
    side = signal.side
    if side == "close":
        side = "sell"
    elif side == "target":
        side = "buy"

    qty = signal.qty
    if qty is None:
        weight = signal.weight or 1.0
        qty = max(1.0, portfolio.total_equity * weight / 100.0)

    return {
        "symbol": signal.symbol,
        "side": side,
        "qty": qty,
        "order_type": signal.order_type,
        "limit_price": signal.limit_price,
        "signal_id": signal.signal_id,
    }


def forward_signal(signal_dict: dict, broker, order_manager,
                   execution_algo=None, position_tracker=None,
                   market_data=None):
    """Forward a converted signal dict through the OMS pipeline.

    If an execution algo is provided, slices the order via TWAP/VWAP.
    Otherwise submits directly through the OrderManager.

    Auto-detects Jupyter event loop: uses asyncio.run() in scripts,
    or nests the coroutine in a new thread when inside a running loop.
    Returns list of TrackedOrder objects.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _forward_async(signal_dict, broker, order_manager,
                           execution_algo, position_tracker, market_data)
        )

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(
            asyncio.run,
            _forward_async(signal_dict, broker, order_manager,
                           execution_algo, position_tracker, market_data)
        )
        return future.result()


async def _forward_async(signal_dict, broker, order_manager,
                         execution_algo, position_tracker, market_data):
    if execution_algo is not None:
        orders = await execution_algo.run(signal_dict, broker, market_data)
        tracked = []
        for o in orders:
            t = await order_manager.submit(
                o.symbol, o.side, o.qty,
                strategy_name=signal_dict.get("strategy_name", ""),
                signal_id=signal_dict.get("signal_id"),
                limit_price=signal_dict.get("limit_price"),
            )
            if position_tracker:
                position_tracker.record_fill(o.symbol, o.side, o.qty)
            tracked.append(t)
        return tracked

    t = await order_manager.submit(
        signal_dict["symbol"], signal_dict["side"], signal_dict["qty"],
        order_type=signal_dict.get("order_type", "market"),
        strategy_name=signal_dict.get("strategy_name", ""),
        signal_id=signal_dict.get("signal_id"),
        limit_price=signal_dict.get("limit_price"),
    )
    if position_tracker:
        position_tracker.record_fill(t.symbol, t.side, t.filled_qty)
    return [t]


class MarketDataBridge:
    """Feeds bar data from a DataSource into MarketDataStream callbacks.

    Used when running execution algos against historical data —
    the bridge pushes each bar through the stream's on_bar callbacks.
    """

    def __init__(self, market_data_stream, data_source):
        self.stream = market_data_stream
        self.data = data_source
        self._bar_index = 0

    async def feed_bars(self, start: int = 0, count: int = -1):
        """Push bars from the DataSource through the stream."""
        end = count if count > 0 else len(self.data)
        for i in range(start, min(start + end, len(self.data))):
            bar = self.data.iloc(i)
            for cb in self.stream._bar_callbacks:
                cb(bar)
            self._bar_index = i

    async def latest_bar(self, symbol: str) -> dict:
        """Get the latest actual bar for a symbol from the DataSource."""
        if self._bar_index < len(self.data):
            bar = self.data.iloc(self._bar_index)
            if symbol in bar.get("close", {}):
                return {
                    "symbol": symbol,
                    "close": bar["close"][symbol],
                    "timestamp": str(self.data.timestamp[self._bar_index]),
                }
        return {"symbol": symbol, "close": 100.0, "timestamp": None}


def reconcile(portfolio, position_tracker) -> list[str]:
    """Compare engine Portfolio positions with OMS PositionTracker positions.

    Returns list of discrepancy descriptions. Empty list means perfect match.
    """
    issues = []
    engine_pos = {}
    for sym, pos in portfolio.positions.items():
        if hasattr(pos, "size") and pos.size != 0:
            engine_pos[sym] = pos.size

    for sym, qty in engine_pos.items():
        oms_qty = position_tracker.positions.get(sym, 0)
        if qty != oms_qty:
            issues.append(f"{sym}: engine={qty}, oms={oms_qty}")

    for sym in position_tracker.positions:
        if sym not in engine_pos and position_tracker.positions[sym] != 0:
            issues.append(f"{sym}: missing from engine, oms={position_tracker.positions[sym]}")

    return issues
