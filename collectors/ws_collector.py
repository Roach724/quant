#!/usr/bin/env python3
"""WebSocket 5m K-line + Orderbook collector — systemd daemon.

Subscribes to OpenD SubType.K_5M + SubType.ORDER_BOOK for HK/US across
market hours, buffers completed bars + orderbook snapshots, and flushes
to GCS via existing storage.py.

Orderbook is polled every 60s during US market hours. Factors computed:
spread, depth_imbalance, weighted_bid/ask. Stored to us/orderbook/.

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

from futu import (
    RET_OK,
    CurKlineHandlerBase,
    OpenQuoteContext,
    SubType,
)

# Add parent to path so we can import storage
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from storage import write_bars_to_gcs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ws_collector: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ws_collector")

# ── Config from env ──
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
OPEND_HOST = os.environ.get("OPEND_HOST", "127.0.0.1")
OPEND_PORT = int(os.environ.get("OPEND_PORT", "11111"))
FLUSH_INTERVAL_SEC = int(os.environ.get("FLUSH_INTERVAL_SEC", "300"))
BUFFER_MAX: int = int(os.environ.get("BUFFER_MAX", "500"))
HEARTBEAT_INTERVAL_SEC = int(os.environ.get("HEARTBEAT_INTERVAL_SEC", "1800"))
OB_POLL_INTERVAL_SEC = int(os.environ.get("OB_POLL_INTERVAL_SEC", "60"))

# ── Symbol pools ──
# Dynamically fetched from Futu API at startup (HK + US).
# Crypto symbols are hardcoded (CCXT-based, not Futu).

CRYPTO_SYMBOLS = [
    "CC.BTC",
    "CC.ETH",
    "CC.SOL",
    "CC.LTC",
    "CC.XRP",
    "CC.DOT",
    "CC.ADA",
    "CC.AVAX",
    "CC.LINK",
    "CC.UNI",
]

HK_SYMBOLS = []  # populated by _load_futu_symbols()
US_SYMBOLS = []  # populated by _load_futu_symbols()


def _load_futu_symbols():
    """Fetch supported HK/US symbols from Futu OpenD and populate lists."""
    global HK_SYMBOLS, US_SYMBOLS
    try:
        from adapters.futu_stock_adapter import FutuStockAdapter

        futu = FutuStockAdapter()
        try:
            all_syms = futu.fetch_supported_symbols()
            HK_SYMBOLS = sorted(s for s in all_syms if s.startswith("HK."))
            US_SYMBOLS = sorted(s for s in all_syms if s.startswith("US."))
            logger.info(
                "Symbol pools loaded: US=%d HK=%d (total=%d)",
                len(US_SYMBOLS),
                len(HK_SYMBOLS),
                len(all_syms),
            )
        finally:
            futu.close()
    except Exception as e:
        logger.error("Failed to load Futu symbols: %s — using empty lists", e)


# ── Market hour helpers (simplified, UTC-based) ──# ── Market hour helpers (simplified, UTC-based) ──  # noqa: E501


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _hk_is_open() -> bool:
    """HK market: Mon-Fri, UTC 01:30-04:00 and 05:00-08:00 (lunch break 04:00-05:00)."""
    dt = _now_utc()
    if dt.weekday() >= 5:
        return False
    t = dt.hour * 60 + dt.minute
    return (90 <= t < 240) or (300 <= t < 480)


def _us_is_open() -> bool:
    """US market: Mon-Fri, UTC 13:30-20:00 (ET 09:30-16:00)."""
    dt = _now_utc()
    if dt.weekday() >= 5:
        return False
    t = dt.hour * 60 + dt.minute
    return 810 <= t <= 1200


def _crypto_is_open() -> bool:
    """Crypto: always open."""
    return True


# ── Orderbook polling ──

_ob_buffer: list[dict] = []
_last_ob_flush = 0.0
_last_ob_poll = 0.0


def _poll_orderbook(ctx: "OpenQuoteContext") -> None:
    """Poll orderbook snapshots using batched subscribe/unsubscribe.

    Uses ~50 free subscription slots (out of 300, after K_5M uses ~234).
    Each batch: subscribe 50 → poll → unsubscribe → next batch.
    """
    global _last_ob_poll, _ob_buffer, _last_ob_flush

    if not _us_is_open():
        return

    now_ts = time.time()
    if now_ts - _last_ob_poll < OB_POLL_INTERVAL_SEC:
        return
    _last_ob_poll = now_ts

    us_syms = [s for s in US_SYMBOLS if s.startswith("US.")]
    now = datetime.now(UTC)
    snapshot_count = 0
    batch_size = 50  # stays under (300 - 234 K_5M) = 66 free slots

    for i in range(0, len(us_syms), batch_size):
        batch = us_syms[i : i + batch_size]
        ret, _ = ctx.subscribe(batch, [SubType.ORDER_BOOK])
        if ret != RET_OK:
            continue

        for sym in batch:
            r, data = ctx.get_order_book(sym, num=10)
            if r != RET_OK:
                continue
            bid_list = []
            ask_list = []
            if isinstance(data, dict):
                bid_list = data.get("Bid", [])
                ask_list = data.get("Ask", [])
            if not bid_list or not ask_list:
                continue

            best_bid = bid_list[0][0]
            best_ask = ask_list[0][0]
            spread = best_ask - best_bid
            midpoint = (best_bid + best_ask) / 2
            spread_pct = (spread / midpoint * 100) if midpoint > 0 else 0

            total_bv = sum(b[1] for b in bid_list)
            total_av = sum(a[1] for a in ask_list)
            total_vol = total_bv + total_av
            depth_imbalance = ((total_bv - total_av) / total_vol) if total_vol > 0 else 0
            w_bid = sum(b[0] * b[1] for b in bid_list) / total_bv if total_bv > 0 else 0
            w_ask = sum(a[0] * a[1] for a in ask_list) / total_av if total_av > 0 else 0

            row = {
                "symbol": sym,
                "snapshot_time": now,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "spread_pct": spread_pct,
                "total_bid_volume": total_bv,
                "total_ask_volume": total_av,
                "depth_imbalance": depth_imbalance,
                "weighted_bid": w_bid,
                "weighted_ask": w_ask,
                "bid_ask_volume_ratio": total_bv / total_av if total_av > 0 else 0,
            }
            for j in range(min(3, len(bid_list))):
                row[f"bid_price_{j + 1}"] = bid_list[j][0]
                row[f"bid_volume_{j + 1}"] = bid_list[j][1]
            for j in range(min(3, len(ask_list))):
                row[f"ask_price_{j + 1}"] = ask_list[j][0]
                row[f"ask_volume_{j + 1}"] = ask_list[j][1]
            _ob_buffer.append(row)
            snapshot_count += 1

        # Unsubscribe batch to free slots
        ctx.unsubscribe(batch, [SubType.ORDER_BOOK])

    logger.info(
        "[OB] Polled %d orderbooks (batch=%d), buffer=%d",
        snapshot_count,
        batch_size,
        len(_ob_buffer),
    )

    # Flush orderbook buffer independently (every FLUSH_INTERVAL_SEC)
    if _ob_buffer and now_ts - _last_ob_flush > FLUSH_INTERVAL_SEC:
        _flush_orderbook()
        _last_ob_flush = now_ts


def _flush_orderbook():
    """Write buffered orderbook snapshots to GCS."""
    global _ob_buffer
    if not _ob_buffer:
        return

    import pandas as pd

    snapshot = list(_ob_buffer)
    _ob_buffer.clear()
    df = pd.DataFrame(snapshot)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    gcs_path = f"gs://{GCS_BUCKET}/us/orderbook/{date_str}/data.parquet"
    try:
        df.to_parquet(gcs_path, index=False)
        logger.info(
            "[OB] Flushed %d rows -> %s",
            len(df),
            gcs_path,
        )
    except Exception as e:
        logger.error("[OB] GCS write failed: %s", e)


# ── K-line handler ──


class BarHandler(CurKlineHandlerBase):
    """Receives completed 5m K-line bars and appends to a shared buffer."""

    def __init__(self, buffer: list, label: str = ""):
        super().__init__()
        self.buffer = buffer
        self.label = label
        self.bar_count = 0

    def on_recv_rsp(self, rsp_pb):
        ret_code, data = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            logger.warning("[%s] Handler error: %s", self.label, data)
            return ret_code, data

        for _, row in data.iterrows():
            self.buffer.append(
                {
                    "symbol": row.get("code", ""),
                    "timestamp": row["time_key"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(float(row["volume"])),
                }
            )
        self.bar_count += len(data)
        return ret_code, data


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

    # Populate symbol pools from Futu API
    _load_futu_symbols()
    buffer: list[dict] = []
    handler = BarHandler(buffer, label="5m")

    current_subscriptions: set[str] = set()
    last_heartbeat = 0.0
    last_market_check = 0.0
    reconnect_backoff = 1

    ctx: OpenQuoteContext | None = None

    logger.info(
        "ws_collector starting: bucket=%s opend=%s:%d flush=%ds buffer_max=%d",
        GCS_BUCKET,
        OPEND_HOST,
        OPEND_PORT,
        FLUSH_INTERVAL_SEC,
        BUFFER_MAX,
    )

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
            desired: set[str] = set()

            if _hk_is_open():
                desired.update(HK_SYMBOLS)
            if _us_is_open():
                desired.update(US_SYMBOLS)
            # CRYPTO disabled
            # CRYPTO disabled

            to_sub = desired - current_subscriptions
            to_unsub = current_subscriptions - desired

            if to_sub:
                try:
                    ret, msg = ctx.subscribe(
                        list(to_sub),
                        [SubType.K_5M, SubType.ORDER_BOOK],
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

        # ── Orderbook poll (no extra subscription cost) ──
        if ctx is not None:
            try:
                _poll_orderbook(ctx)
            except Exception as e:
                logger.warning("Orderbook poll error: %s", e)

        # ── Flush buffer ──
        if buffer and now_ts - last_heartbeat > FLUSH_INTERVAL_SEC:
            _flush_buffer(buffer, handler.label)

        # ── Heartbeat ──
        if now_ts - last_heartbeat > HEARTBEAT_INTERVAL_SEC:
            last_heartbeat = now_ts
            logger.info(
                "[HEARTBEAT] subscriptions=%d buffer=%d bars_received=%d",
                len(current_subscriptions),
                len(buffer),
                handler.bar_count,
            )

        time.sleep(5)

    # ── Shutdown ──
    logger.info("Shutting down — final flush...")
    _flush_buffer(buffer, handler.label)
    _flush_orderbook()
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
            paths = write_bars_to_gcs(
                mkt_df,
                GCS_BUCKET,
                market=market.lower(),
                frequency="5m",
            )
            logger.info(
                "Flushed %d bars (market=%s) → %d GCS paths", len(mkt_df), market, len(paths)
            )
        except Exception as e:
            logger.error("GCS write failed for %s: %s", market, e)
            return


if __name__ == "__main__":
    main()
