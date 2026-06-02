"""Orderbook collector daemon — polls Futu ORDER_BOOK snapshots, computes
microstructure factors, and flushes to GCS periodically.

Uses batched subscriptions (50 symbols at a time) to stay under the
300-subscription limit while ws_collector runs.

Usage: python -m collectors.ob_collector

Environment:
    GCS_BUCKET: GCS bucket name (required)
    POLL_INTERVAL_SEC: seconds between snapshots (default: 60)
    FLUSH_INTERVAL_SEC: seconds between GCS flushes (default: 300)
"""

import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from futu import RET_OK, OpenQuoteContext, SubType

logger = logging.getLogger(__name__)

OPEND_HOST = os.environ.get("OPEND_HOST", "127.0.0.1")
OPEND_PORT = int(os.environ.get("OPEND_PORT", "11111"))
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
POLL_INTERVAL_SEC = int(os.environ.get("POLL_INTERVAL_SEC", "60"))
FLUSH_INTERVAL_SEC = int(os.environ.get("FLUSH_INTERVAL_SEC", "300"))
BATCH_SIZE = 50  # symbols per subscribe batch (respects 300 limit)


def get_us_symbols() -> list[str]:
    """Get US symbols from BigQuery bars_5m table."""
    from google.cloud import bigquery

    client = bigquery.Client()
    rows = client.query(
        "SELECT DISTINCT symbol FROM "
        "`deductive-notch-495015-c2.quant.us_bars_5m` "
        "WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 DAY)"
    ).result()
    return sorted(r.symbol for r in rows)


def compute_factors(bid_list: list, ask_list: list, symbol: str, ts: datetime) -> dict[str, Any]:
    """Compute microstructure factors from orderbook snapshot."""
    if not bid_list or not ask_list:
        return {}

    best_bid = bid_list[0][0]
    best_ask = ask_list[0][0]
    spread = best_ask - best_bid
    midpoint = (best_bid + best_ask) / 2
    spread_pct = (spread / midpoint * 100) if midpoint > 0 else 0

    total_bid_vol = sum(b[1] for b in bid_list)
    total_ask_vol = sum(a[1] for a in ask_list)
    total_vol = total_bid_vol + total_ask_vol
    depth_imbalance = ((total_bid_vol - total_ask_vol) / total_vol) if total_vol > 0 else 0

    weighted_bid = sum(b[0] * b[1] for b in bid_list) / total_bid_vol if total_bid_vol > 0 else 0
    weighted_ask = sum(a[0] * a[1] for a in ask_list) / total_ask_vol if total_ask_vol > 0 else 0

    row: dict[str, Any] = {
        "symbol": symbol,
        "snapshot_time": ts,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_pct": spread_pct,
        "total_bid_volume": total_bid_vol,
        "total_ask_volume": total_ask_vol,
        "depth_imbalance": depth_imbalance,
        "weighted_bid": weighted_bid,
        "weighted_ask": weighted_ask,
        "bid_ask_volume_ratio": (total_bid_vol / total_ask_vol if total_ask_vol > 0 else 0),
    }
    for i in range(min(3, len(bid_list))):
        row[f"bid_price_{i + 1}"] = bid_list[i][0]
        row[f"bid_volume_{i + 1}"] = bid_list[i][1]
    for i in range(min(3, len(ask_list))):
        row[f"ask_price_{i + 1}"] = ask_list[i][0]
        row[f"ask_volume_{i + 1}"] = ask_list[i][1]
    return row


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if not GCS_BUCKET:
        logger.critical("GCS_BUCKET env var not set")
        sys.exit(1)

    symbols = get_us_symbols()
    logger.info("ob_collector: %d US symbols loaded", len(symbols))

    ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
    buffer: list[dict[str, Any]] = []
    last_flush = time.time()
    running = True

    def shutdown(sig: int, frame: object) -> None:
        nonlocal running
        logger.info("Received signal %d, shutting down...", sig)
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    poll_count = 0
    try:
        while running:
            now = datetime.now(UTC)
            snapshot_count = 0

            # Batch subscribe/poll/unsubscribe to stay under 300 limit
            for i in range(0, len(symbols), BATCH_SIZE):
                if not running:
                    break
                batch = symbols[i : i + BATCH_SIZE]
                ret, _ = ctx.subscribe(batch, [SubType.ORDER_BOOK])
                if ret != RET_OK:
                    logger.warning("Subscribe batch %d failed, skipping", i // BATCH_SIZE)
                    continue

                for sym in batch:
                    r, data = ctx.get_order_book(sym, num=10)
                    if r != RET_OK:
                        continue
                    bid_list: list = []
                    ask_list: list = []
                    if isinstance(data, dict):
                        bid_list = data.get("Bid", [])
                        ask_list = data.get("Ask", [])
                    row = compute_factors(bid_list, ask_list, sym, now)
                    if row:
                        buffer.append(row)
                        snapshot_count += 1

                # Unsubscribe batch to free slots
                ctx.unsubscribe(batch, [SubType.ORDER_BOOK])

            poll_count += 1
            if poll_count % 5 == 0:
                logger.info(
                    "Poll #%d: %d snapshots, buffer=%d",
                    poll_count,
                    snapshot_count,
                    len(buffer),
                )

            # Flush
            if time.time() - last_flush > FLUSH_INTERVAL_SEC and buffer:
                df = pd.DataFrame(buffer)
                date_str = now.strftime("%Y-%m-%d")
                gcs_path = f"gs://{GCS_BUCKET}/us/orderbook/{date_str}/data.parquet"
                df.to_parquet(gcs_path, index=False)
                logger.info("Flushed %d rows -> %s", len(df), gcs_path)
                buffer.clear()
                last_flush = time.time()

            time.sleep(POLL_INTERVAL_SEC)
    finally:
        if buffer:
            df = pd.DataFrame(buffer)
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            gcs_path = f"gs://{GCS_BUCKET}/us/orderbook/{date_str}/data.parquet"
            df.to_parquet(gcs_path, index=False)
            logger.info("Final flush: %d rows", len(df))
        ctx.close()
        logger.info("ob_collector stopped")


if __name__ == "__main__":
    main()
