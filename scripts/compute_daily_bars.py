"""Compute daily bars from 5m bars in BigQuery.

Replaces: collectors/main.py FREQUENCY=1d (Futu fetch)
Aggregates OHLCV from 5m bars → writes to {market}_bars_1d.
Also supports index aggregation: {market}_bars_index_5m → {market}_bars_index_1d.

Usage:
    python scripts/compute_daily_bars.py --market hk [--date 2026-06-03]
    python scripts/compute_daily_bars.py --market hk --index [--date 2026-06-03]
"""
import argparse
import logging
import sys
from datetime import datetime, timezone

from google.cloud import bigquery

sys.path.insert(0, "/opt/quant-dev")

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("compute_daily_bars")


def compute_1d(market: str, date_str: str) -> int:
    table_5m = f"{PROJECT}.{DATASET}.{market}_bars_5m"
    table_1d = f"{PROJECT}.{DATASET}.{market}_bars_1d"

    client = bigquery.Client(project=PROJECT)

    prefix = market.upper()
    # Normalize symbol: strip market prefix + (HK only) zero-pad to 5 digits
    if market == "hk":
        norm_expr = (
            f"CONCAT('{prefix}.', "
            f"LPAD(REGEXP_REPLACE(REGEXP_REPLACE(symbol, r'^{prefix}\\.', ''), r'^0+', ''), 5, '0'))"
        )
    else:
        norm_expr = f"REGEXP_REPLACE(symbol, r'^{prefix}\\.', '')"
        norm_expr = f"CONCAT('{prefix}.', {norm_expr})"

    # Aggregate 5m → 1d
    query = f"""
        SELECT
          {norm_expr} as symbol,
          PARSE_TIMESTAMP('%Y-%m-%d', '{date_str}') as timestamp,
          ARRAY_AGG(open ORDER BY bar_ts LIMIT 1)[OFFSET(0)] as open,
          MAX(high) as high,
          MIN(low) as low,
          ARRAY_AGG(close ORDER BY bar_ts DESC LIMIT 1)[OFFSET(0)] as close,
          SUM(volume) as volume,
          '{market}' as market,
          '1d' as frequency,
        FROM (
          SELECT
            symbol, timestamp as bar_ts, open, high, low, close, volume
          FROM `{table_5m}`
          WHERE timestamp >= TIMESTAMP('{date_str}')
            AND timestamp < TIMESTAMP(DATE_ADD('{date_str}', INTERVAL 1 DAY))
        )
        GROUP BY symbol
        HAVING COUNT(*) >= 10  -- minimum bars for a valid daily bar
    """
    df = client.query(query).to_dataframe()

    if df.empty:
        logger.warning("No bars aggregated for %s (%s)", market, date_str)
        return 0

    logger.info(
        "Aggregated %d daily bars for %s (%s): %d symbols, %.0f bars/symbol avg",
        len(df), market, date_str,
        df["symbol"].nunique(),
        len(df) / max(df["symbol"].nunique(), 1),
    )

    # Skip symbols that already have a bar for this date
    existing = client.query(
        f"SELECT DISTINCT symbol FROM `{table_1d}` WHERE timestamp = TIMESTAMP('{date_str}')"
    ).to_dataframe()
    existing_set = set(existing["symbol"].tolist()) if not existing.empty else set()
    df_new = df[~df["symbol"].isin(existing_set)]

    if df_new.empty:
        logger.info("All %d bars already exist in %s, skipping", len(df), table_1d)
        return 0

    logger.info("Writing %d new bars (skipped %d existing)", len(df_new), len(df) - len(df_new))

    from common.bq_writer import write_rows_to_bq

    n = write_rows_to_bq(df_new, table_name=f"{market}_bars_1d")
    logger.info("Wrote %d daily bars → %s", n, table_1d)
    return n


def compute_index_1d(market: str, date_str: str) -> int:
    """Aggregate index 5m bars → 1d bars for hk/us index tables."""
    table_5m = f"{PROJECT}.{DATASET}.{market}_bars_index_5m"
    table_1d = f"{PROJECT}.{DATASET}.{market}_bars_index_1d"

    client = bigquery.Client(project=PROJECT)

    # Index symbols keep their original format (HK.800000, ^IXIC, etc.)
    norm_expr = "symbol"

    # Aggregate 5m → 1d
    query = f"""
        SELECT
          {norm_expr} as symbol,
          '{date_str}' as date,
          ARRAY_AGG(open ORDER BY bar_ts LIMIT 1)[OFFSET(0)] as open,
          MAX(high) as high,
          MIN(low) as low,
          ARRAY_AGG(close ORDER BY bar_ts DESC LIMIT 1)[OFFSET(0)] as close,
          SUM(volume) as volume,
          '{market}' as market,
          '1d' as frequency,
        FROM (
          SELECT
            symbol, timestamp as bar_ts, open, high, low, close, volume
          FROM `{table_5m}`
          WHERE timestamp >= TIMESTAMP('{date_str}')
            AND timestamp < TIMESTAMP(DATE_ADD('{date_str}', INTERVAL 1 DAY))
        )
        GROUP BY symbol
        HAVING COUNT(*) >= 5  -- minimum bars for a valid daily index bar
    """
    df = client.query(query).to_dataframe()

    if df.empty:
        logger.warning("No index bars aggregated for %s (%s)", market, date_str)
        return 0

    logger.info(
        "Aggregated %d daily index bars for %s (%s): %d symbols, %.0f bars/symbol avg",
        len(df), market, date_str,
        df["symbol"].nunique(),
        len(df) / max(df["symbol"].nunique(), 1),
    )

    # Skip symbols that already have a bar for this date
    existing = client.query(
        f"SELECT DISTINCT symbol FROM `{table_1d}` WHERE date = '{date_str}'"
    ).to_dataframe()
    existing_set = set(existing["symbol"].tolist()) if not existing.empty else set()
    df_new = df[~df["symbol"].isin(existing_set)]

    if df_new.empty:
        logger.info("All %d index bars already exist in %s, skipping", len(df), table_1d)
        return 0

    logger.info("Writing %d new index bars (skipped %d existing)", len(df_new), len(df) - len(df_new))

    from common.bq_writer import write_rows_to_bq

    n = write_rows_to_bq(df_new, table_name=f"{market}_bars_index_1d")
    logger.info("Wrote %d daily index bars → %s", n, table_1d)
    return n


def main():
    parser = argparse.ArgumentParser(description="Compute daily bars from 5m")
    parser.add_argument("--market", required=True, choices=["hk", "us"])
    parser.add_argument(
        "--date",
        help="Target date (default: today UTC)",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Aggregate index bars (hk_bars_index_5m/us_bars_index_5m → _1d)",
    )
    args = parser.parse_args()

    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        if args.index:
            n = compute_index_1d(args.market, date_str)
        else:
            n = compute_1d(args.market, date_str)
        print(f"SUCCESS: {n} daily bars written for {args.market} ({date_str})")
        return 0
    except Exception as e:
        logger.error("Failed: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
