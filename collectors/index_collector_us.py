#!/usr/bin/env python3
"""US Index 5m Collector — fetch & deduplicate via yfinance → BigQuery.

Usage:
    python collectors/index_collector_us.py --freq 5m
    python collectors/index_collector_us.py --freq 1d

Scheduling:
    */1 * * * * /opt/quant-dev/scripts/cron/us_index_5m.sh  (5m)
    30 22 * * 1-5 /opt/quant-dev/scripts/cron/us_index_1d.sh (1d, after close)
"""

from __future__ import annotations
import logging, sys, time, json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import yfinance as yf
from common.logging_util import get_logger
from google.cloud import bigquery

BQ_PROJECT = "deductive-notch-495015-c2"
BQ_DATASET = "quant"

logger = get_logger("index_collector.us", env="dev", module="collector",
                    log_file="/var/log/quant/dev/collector/index_us.log")

def load_index_symbols() -> list[str]:
    cfg = yaml.safe_load(open("config/symbols.yaml"))
    return cfg.get("indices", {}).get("us", {}).get("symbols", [])

def is_market_open() -> bool:
    """US market: Mon-Fri 09:30-16:00 ET = 13:30-20:00 UTC."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False  # weekend
    market_open = now.replace(hour=13, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=20, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close

def fetch_and_write(symbol: str, freq: str, client) -> int:
    """Fetch intraday bars for one index, deduplicate, write to BQ.
    Returns number of rows written."""
    ticker = yf.Ticker(symbol)
    interval = "5m" if freq == "5m" else "1d"
    df = ticker.history(period="1d" if freq == "5m" else "max", interval=interval)

    if df.empty:
        logger.debug("%s: no data for %s", symbol, freq)
        return 0

    table = f"{BQ_PROJECT}.{BQ_DATASET}.us_bars_index_{freq}"

    # Dedup: check latest timestamp in BQ
    try:
        q = f"SELECT MAX(timestamp) FROM `{table}` WHERE symbol='{symbol}'"
        latest = list(client.query(q).result())[0][0]
    except Exception:
        latest = None

    rows_written = 0
    batch = []
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime().replace(tzinfo=timezone.utc) if hasattr(idx, 'to_pydatetime') else idx
        if latest and ts <= latest.replace(tzinfo=timezone.utc):
            continue
        batch.append({
            "symbol": symbol,
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row.get("Volume", 0) or 0),
        })

    if batch:
        errors = client.insert_rows_json(table, batch)
        if errors:
            logger.error("BQ insert errors: %s", errors[:3])
        else:
            rows_written = len(batch)
            logger.info("Wrote %d bars for %s (%s)", rows_written, symbol, freq)

    return rows_written

def main():
    if not is_market_open():
        logger.info("Market closed — skipping")
        return

    symbols = load_index_symbols()
    client = bigquery.Client(project=BQ_PROJECT)
    freq = "5m"
    total = 0
    for sym in symbols:
        try:
            n = fetch_and_write(sym, freq, client)
            total += n
        except Exception:
            logger.exception("Failed to fetch %s", sym)
    logger.info("Collection complete: %d bars written", total)

if __name__ == "__main__":
    main()
