#!/usr/bin/env python3
"""Backfill index K-line data from Futu (HK) and yfinance (US).

Usage:
    python scripts/backfill_index.py --market hk --freq 5m --start 2024-01-01
    python scripts/backfill_index.py --market us --freq 1d --start 2020-01-01
"""

import argparse, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from google.cloud import bigquery
from common.logging_util import get_logger

logger = get_logger("backfill.index", env="prod", module="backfill",
                    log_file="/var/log/quant/prod/backfill/index.log")

BQ_PROJECT = "deductive-notch-495015-c2"
BQ_DATASET = "quant"

def backfill_hk(symbol: str, freq: str, start: str, end: str, client) -> int:
    """Backfill HK index via Futu API with pagination."""
    from futu import OpenQuoteContext, KLType, RET_OK

    ktype_map = {"1m": KLType.K_1M, "5m": KLType.K_5M, "1d": KLType.K_DAY}
    ktype = ktype_map.get(freq)
    table = f"{BQ_PROJECT}.{BQ_DATASET}.hk_bars_index_{freq}"
    total = 0
    batch_start = start

    for page in range(20):
        q = OpenQuoteContext(host="127.0.0.1", port=11111)
        try:
            ret, data, _ = q.request_history_kline(symbol, ktype=ktype,
                                                    start=batch_start, end=end,
                                                    max_count=1000)
            if ret != 0:  # RET_OK is 0
                logger.error("Futu K-line failed %s p%d: %s", symbol, page, data)
                break
            if data is None or len(data) == 0:
                break

            rows = []
            for _, r in data.iterrows():
                rows.append({
                    "symbol": symbol,
                    "timestamp": str(r["time_key"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r.get("volume", 0) or 0),
                })

            errors = client.insert_rows_json(table, rows)
            if errors:
                logger.error("BQ insert err p%d: %s", page, errors[:3])
                break
            total += len(rows)
            last_ts = str(data.iloc[-1]["time_key"])
            print(f"  {symbol} page {page}: {len(rows)} rows, last={last_ts}")
            logger.info("%s p%d: %d rows last=%s", symbol, page, len(rows), last_ts)

            if len(data) < 1000:
                break
            batch_start = last_ts
        finally:
            q.close()

    return total

def backfill_us(symbol: str, freq: str, start: str, client) -> int:
    """Backfill US index via yfinance."""
    import yfinance as yf

    interval_map = {"5m": "5m", "1d": "1d"}
    interval = interval_map.get(freq, "1d")
    table = f"{BQ_PROJECT}.{BQ_DATASET}.us_bars_index_{freq}"

    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, interval=interval)

    if df.empty:
        logger.warning("%s: no data from %s", symbol, start)
        return 0

    rows = []
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime().replace(tzinfo=timezone.utc) if hasattr(idx, "to_pydatetime") else idx
        rows.append({
            "symbol": symbol,
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row.get("Volume", 0) or 0),
        })

    errors = client.insert_rows_json(table, rows)
    if errors:
        logger.error("BQ insert errors: %s", errors[:3])
        return 0

    logger.info("Backfilled %s %s from %s: %d bars", symbol, freq, start, len(rows))
    return len(rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["hk", "us"], required=True)
    parser.add_argument("--freq", choices=["1m", "5m", "1d"], default="1d")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", default="")
    args = parser.parse_args()

    cfg = yaml.safe_load(open("config/symbols.yaml"))
    symbols = cfg.get("indices", {}).get(args.market, {}).get("symbols", [])
    client = bigquery.Client(project=BQ_PROJECT)

    total = 0
    for sym in symbols:
        if args.market == "hk":
            n = backfill_hk(sym, args.freq, args.start,
                           args.end or datetime.now().strftime("%Y-%m-%d"), client)
        else:
            n = backfill_us(sym, args.freq, args.start, client)
        total += n
        print(f"  {sym}: {n} bars total")

    logger.info("Backfill done: %d bars (%s %s)", total, args.market, args.freq)
    print(f"Complete: {total} bars ({args.market} {args.freq})")

if __name__ == "__main__":
    main()
