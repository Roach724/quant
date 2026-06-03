#!/usr/bin/env python3
"""WebSocket 5m K-line collector — systemd daemon.

Subscribes to OpenD SubType.K_5M for HK/US/Crypto across market hours,
buffers completed bars, and flushes to GCS via existing storage.py.

Env vars:
    GCS_BUCKET: GCS bucket name (required)
    OPEND_HOST: OpenD host (default: 127.0.0.1)
    OPEND_PORT: OpenD port (default: 11111)
    FLUSH_INTERVAL_SEC: seconds between flushes (default: 300)
    BUFFER_MAX: max records before flush (default: 50)
    HEARTBEAT_INTERVAL_SEC: seconds between heartbeat logs (default: 1800)
"""

import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime
import yaml
from pathlib import Path

from futu import (
    RET_OK,
    CurKlineHandlerBase,
    OpenQuoteContext,
    SubType,
)

# Add parent to path so we can import storage
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Add project root to Python path for live.calendar import
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from live.calendar import MarketCalendar

from storage import write_bars_to_gcs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ws_collector: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ws_collector")

# ── JSON file logging → /var/log/quant/prod/collector/ ──
try:
    from common.logging_util import QuantJsonFormatter, _ContextFilter

    _json_log = "/var/log/quant/prod/collector/ws_collector.log"
    _fh = logging.FileHandler(_json_log)
    _fh.setFormatter(QuantJsonFormatter())
    _fh.addFilter(_ContextFilter(env="prod", module="collector"))
    # Add to root so all child loggers (live.calendar, etc.) write JSON
    logging.getLogger().addHandler(_fh)
except Exception:
    logger.warning("Failed to set up JSON file logging (non-fatal)", exc_info=True)

# ── Config from env ──
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
OPEND_HOST = os.environ.get("OPEND_HOST", "127.0.0.1")
OPEND_PORT = int(os.environ.get("OPEND_PORT", "11111"))
FLUSH_INTERVAL_SEC = int(os.environ.get("FLUSH_INTERVAL_SEC", "300"))
BUFFER_MAX: int = int(os.environ.get("BUFFER_MAX", "500"))
HEARTBEAT_INTERVAL_SEC = int(os.environ.get("HEARTBEAT_INTERVAL_SEC", "1800"))

# ── Symbol config (SSOT) ──

_SYMBOL_CONFIG: dict[str, list[str]] = {}
_CALENDARS: dict[str, MarketCalendar] = {}
PREHEAT_MINUTES = 5


def _load_symbols_config():
    """Load symbol lists from config/symbols.yaml (SSOT)."""
    global _SYMBOL_CONFIG, _CALENDARS

    config_path = _PROJECT_ROOT / "config" / "symbols.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    for market in ("us", "hk"):
        _SYMBOL_CONFIG[market] = cfg["markets"][market]["symbols"]
        _CALENDARS[market] = MarketCalendar(market)

    logger.info(
        "Symbol config loaded: US=%d HK=%d (preheat=%dmin)",
        len(_SYMBOL_CONFIG.get("us", [])),
        len(_SYMBOL_CONFIG.get("hk", [])),
        PREHEAT_MINUTES,
    )


def _desired_symbols() -> set[str]:
    """Return symbols for currently-open markets using calendar + preheat."""
    desired: set[str] = set()
    for market in ("hk", "us"):
        cal = _CALENDARS.get(market)
        syms = _SYMBOL_CONFIG.get(market, [])
        if cal is not None and cal.is_open_now(preheat_minutes=PREHEAT_MINUTES):
            desired.update(syms)
    return desired


# ── K-line handler ──

class BarHandler(CurKlineHandlerBase):
    """Receives K-line bar updates; keeps latest OHLCV per (symbol, timestamp).

    CurKlineHandlerBase pushes the current incomplete bar multiple times as
    OHLCV expands. We keep the LATEST update (most complete OHLCV) per key.
    """

    def __init__(self, buffer: list, label: str = ""):
        super().__init__()
        self.buffer = buffer
        self.label = label
        self.bar_count = 0
        self._latest: dict[tuple[str, str], dict] = {}  # (symbol, timestamp) → bar
        self._already_flushed: set[tuple[str, str]] = set()  # keys already written to BQ

    def on_recv_rsp(self, rsp_pb):
        ret_code, data = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            logger.warning("[%s] Handler error: %s", self.label, data)
            return ret_code, data

        for _, row in data.iterrows():
            key = (row.get("code", ""), str(row["time_key"]))
            self._latest[key] = {
                "symbol": row.get("code", ""),
                "timestamp": row["time_key"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(float(row["volume"])),
            }
        self.bar_count += len(data)
        return ret_code, data

    def drain_to_buffer(self) -> int:
        """Drain completed bars (not the current per-symbol bar) to the buffer.

        Only drains bars whose timestamp is NOT the latest per symbol.
        The latest bar per symbol is kept in _latest until a newer timestamp
        arrives, ensuring we capture the most complete OHLCV.

        Thread-safe: dict operations are atomic under the GIL.

        Returns number of bars moved.
        """
        if not self._latest:
            return 0

        # Find latest timestamp per symbol
        latest_ts: dict[str, str] = {}
        for (sym, ts) in self._latest:
            if sym not in latest_ts or ts > latest_ts[sym]:
                latest_ts[sym] = ts

        # Drain completed bars (not latest) that haven't been flushed yet
        new_bars: list[dict] = []
        for (sym, ts), bar in list(self._latest.items()):
            if ts != latest_ts.get(sym):
                if (sym, ts) not in self._already_flushed:
                    new_bars.append(bar)
                    self._already_flushed.add((sym, ts))

        if new_bars:
            self.buffer.extend(new_bars)
        return len(new_bars)


# ── Main loop ──

_shutdown = False


def _on_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %d, shutting down...", signum)
    _shutdown = True


def main():
    global _shutdown

    if not GCS_BUCKET:
        logger.critical("GCS_BUCKET env var not set — cannot write data")
        sys.exit(1)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # Load symbol config from SSOT yaml
    _load_symbols_config()
    buffer: list[dict] = []
    handler = BarHandler(buffer, label="5m")

    current_subscriptions: set[str] = set()
    last_heartbeat = 0.0
    last_flush = 0.0
    last_market_check = 0.0
    reconnect_backoff = 1

    ctx: OpenQuoteContext | None = None

    logger.info("ws_collector starting: bucket=%s opend=%s:%d flush=%ds buffer_max=%d",
                GCS_BUCKET, OPEND_HOST, OPEND_PORT, FLUSH_INTERVAL_SEC, BUFFER_MAX)

    while not _shutdown:
        now_ts = time.time()

        # ── Reconnect / maintain OpenD connection ──
        if ctx is None:
            try:
                ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
                ctx.set_handler(handler)
                logger.info("Connected to OpenD %s:%d", OPEND_HOST, OPEND_PORT)
                reconnect_backoff = 1
                current_subscriptions.clear()
            except Exception as e:
                logger.error("OpenD connection failed: %s (retry in %ds)", e, reconnect_backoff)
                time.sleep(reconnect_backoff)
                reconnect_backoff = min(reconnect_backoff * 2, 60)
                continue

        # ── Market-hour subscription management (every 60s) ──
        if now_ts - last_market_check > 60:
            last_market_check = now_ts
            desired = _desired_symbols()

            to_sub = desired - current_subscriptions
            to_unsub = current_subscriptions - desired

            if to_sub:
                try:
                    ret, msg = ctx.subscribe(
                        list(to_sub), [SubType.K_5M],
                        subscribe_push=True,
                    )
                    if ret == RET_OK:
                        logger.info("Subscribed %d symbols (+)", len(to_sub))
                        current_subscriptions.update(to_sub)
                    else:
                        logger.warning("Subscribe failed: %s", msg)
                except Exception as e:
                    logger.error("Subscribe error: %s — reconnecting", e)
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    ctx = None
                    current_subscriptions.clear()
                    continue

            if to_unsub:
                try:
                    ctx.unsubscribe(list(to_unsub), [SubType.K_5M])
                    logger.info("Unsubscribed %d symbols (-)", len(to_unsub))
                    current_subscriptions.difference_update(to_unsub)
                except Exception:
                    pass

        # ── Flush buffer ──
        handler.drain_to_buffer()
        if buffer and (now_ts - last_flush > FLUSH_INTERVAL_SEC or len(buffer) >= BUFFER_MAX):
            _flush_buffer(buffer, handler.label)
            last_flush = now_ts

        # ── Heartbeat ──
        if now_ts - last_heartbeat > HEARTBEAT_INTERVAL_SEC:
            last_heartbeat = now_ts
            logger.info(
                "[HEARTBEAT] subscriptions=%d buffer=%d bars_received=%d",
                len(current_subscriptions), len(buffer), handler.bar_count,
            )

        time.sleep(5)

    # ── Shutdown ──
    logger.info("Shutting down — final flush...")
    handler.drain_to_buffer()
    _flush_buffer(buffer, handler.label)
    if ctx is not None:
        try:
            ctx.close()
        except Exception:
            pass
    logger.info("ws_collector stopped (bars_total=%d)", handler.bar_count)


def _flush_buffer(buffer: list, label: str):
    """Write buffered bars to GCS via storage.write_bars_to_gcs()."""
    if not buffer:
        return

    import pandas as pd

    # Snapshot and clear buffer immediately to avoid data loss on error
    snapshot = list(buffer)
    buffer.clear()

    if not snapshot:
        return

    # Build DataFrame with column alignment (OpenD bars may have inconsistent fields)
    try:
        df = pd.DataFrame(snapshot)
    except ValueError:
        all_keys = set()
        for item in snapshot:
            all_keys.update(item.keys())
        aligned = [{k: item.get(k) for k in all_keys} for item in snapshot]
        df = pd.DataFrame(aligned)

    df["market"] = df["symbol"].apply(
        lambda s: "HK" if s.startswith("HK.") else ("US" if s.startswith("US.") else "CRYPTO")
    )
    df["frequency"] = "5m"

    for market in ("HK", "US"):
        mkt_df = df[df["market"] == market]
        if mkt_df.empty:
            continue
        try:
            from common.bq_writer import write_bars_to_bq
            n = write_bars_to_bq(mkt_df, table_id=f"{market.lower()}_bars_5m")
            logger.info("Flushed %d bars (market=%s) → BQ", n, market)
        except Exception as e:
            logger.error("GCS write failed for %s: %s", market, e)
            return


if __name__ == "__main__":
    main()
