"""Real-time market data source via Futu OpenD WebSocket.

Provides LiveDataSource that subscribes to K_5M bars and feeds them
to a callback handler. Handles connection, subscription, and reconnection.

Usage:
    source = LiveDataSource(
        symbols=["US.AAPL", "US.MSFT", ...],
        host="127.0.0.1", port=11111,
    )
    source.on_bar = lambda bar: strategy.on_bar(bar)
    source.run()  # blocking until market close or stop()
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from futu import (
    CurKlineHandlerBase, OpenQuoteContext, RET_OK, SubType,
)

logger = logging.getLogger(__name__)

# ── Market hours (lite — avoids heavy dependency) ──

_MARKET_HOURS = {
    "us": {"open": (13, 30), "close": (20, 0)},
    "hk": {"open": (1, 30), "close": (8, 0),
           "lunch_start": (4, 0), "lunch_end": (5, 0)},
}


class LiveKlineHandler(CurKlineHandlerBase):
    """Futu K-line callback — forwards completed bars to the data source."""

    def __init__(self, datasource: "LiveDataSource"):
        super().__init__()
        self._src = datasource

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK or data.empty:
            return ret, data
        try:
            self._src._on_kline_update(data)
        except Exception:
            logger.exception("LiveKlineHandler: callback failed")
        return ret, data


class LiveDataSource:
    """Real-time 5m K-line data source via Futu OpenD.

    Subscribes to K_5M and invokes ``on_bar`` for each completed bar.
    """

    def __init__(
        self,
        symbols: list[str],
        host: str = "127.0.0.1",
        port: int = 11111,
        market: str = "us",
    ):
        self.symbols = symbols
        self.host = host
        self.port = port
        self.market = market
        self._ctx: OpenQuoteContext | None = None
        self._running = False
        self._connected = False
        # Callback — set by LiveRunner before run()
        self.on_bar: Callable[[dict], None] | None = None

    def run(self):
        """Blocking event loop — subscribes and waits until market close."""
        self._running = True
        self._connect_and_subscribe()
        logger.info("LiveDataSource: running — %d symbols", len(self.symbols))
        try:
            while self._running and self._is_market_open():
                time.sleep(10)
        except KeyboardInterrupt:
            logger.info("LiveDataSource: interrupted")
        finally:
            self.stop()

    def stop(self):
        """Stop and close the OpenD connection."""
        self._running = False
        if self._ctx:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
        self._connected = False
        logger.info("LiveDataSource: stopped")

    def is_connected(self) -> bool:
        return self._connected

    # ── internals ──

    def _connect_and_subscribe(self):
        """Connect to OpenD and subscribe to K_5M for all symbols."""
        max_retries = 30
        for attempt in range(1, max_retries + 1):
            try:
                logger.info("LiveDataSource: connecting %s:%d (attempt %d)",
                            self.host, self.port, attempt)
                self._ctx = OpenQuoteContext(host=self.host, port=self.port)
                self._ctx.set_handler(LiveKlineHandler(self))

                ret, _ = self._ctx.subscribe(self.symbols, [SubType.K_5M])
                if ret != RET_OK:
                    logger.error("LiveDataSource: subscribe failed ret=%d", ret)
                    self._ctx.close()
                    self._ctx = None
                    time.sleep(10)
                    continue

                self._connected = True
                logger.info("LiveDataSource: connected + subscribed")
                return
            except Exception:
                logger.exception("LiveDataSource: connect failed (attempt %d)", attempt)
                if self._ctx:
                    try:
                        self._ctx.close()
                    except Exception:
                        pass
                    self._ctx = None
                time.sleep(10)

        raise RuntimeError(f"LiveDataSource: failed after {max_retries} retries")

    def _on_kline_update(self, data):
        """Called by LiveKlineHandler when a new 5m bar completes."""
        if self.on_bar is None:
            return
        for _, row in data.iterrows():
            bar = {
                "symbol": str(row.get("code", "")),
                "timestamp": str(row.get("time_key", "")),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
            }
            try:
                self.on_bar(bar)
            except Exception:
                logger.exception("LiveDataSource: on_bar callback failed")

    def _is_market_open(self) -> bool:
        """Check if market is currently open."""
        hours = _MARKET_HOURS.get(self.market)
        if not hours:
            return True
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return False
        t = now.time()
        import datetime as _dt
        open_t = _dt.time(*hours["open"])
        close_t = _dt.time(*hours["close"])
        if "lunch_start" in hours:
            ls = _dt.time(*hours["lunch_start"])
            le = _dt.time(*hours["lunch_end"])
            if ls <= t < le:
                return False
        return open_t <= t <= close_t
