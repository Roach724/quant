#!/usr/bin/env python3
"""Weekly dedup safety net: remove duplicate (symbol, timestamp) rows from bars tables.

Only scans recent data (30 days) to keep cost low. Old data is immutable.
Runs as cron: 0 3 * * 0 (Sunday 03:00 UTC)
"""

from google.cloud import bigquery
import logging, sys, time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dedup_bars")

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"

BARS_TABLES = [
    ("us_bars_5m", "timestamp"),
    ("us_bars_1d", "timestamp"),
    ("hk_bars_5m", "timestamp"),
    ("hk_bars_1d", "timestamp"),
    ("us_bars_index_5m", "timestamp"),
    ("us_bars_index_1d", "date"),
    ("hk_bars_index_5m", "timestamp"),
    ("hk_bars_index_1d", "date"),
]

LOOKBACK_DAYS = 30


def dedup_table(client, table_name, time_col):
    table_ref = f"{PROJECT}.{DATASET}.{table_name}"
    logger.info("Deduping %s...", table_ref)

    # Count before
    before = client.get_table(table_ref).num_rows

    # Delete duplicates: keep one row per (symbol, time_col)
    query = f"""
    DELETE FROM `{table_ref}`
    WHERE CONCAT(CAST(symbol AS STRING), '|', CAST({time_col} AS STRING)) IN (
      SELECT key FROM (
        SELECT 
          CONCAT(CAST(symbol AS STRING), '|', CAST({time_col} AS STRING)) AS key,
          ROW_NUMBER() OVER (PARTITION BY symbol, {time_col} ORDER BY _ingest_time DESC) AS rn
        FROM `{table_ref}`
        WHERE {time_col} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {LOOKBACK_DAYS} DAY)
      ) WHERE rn > 1
    )
    """
    try:
        job = client.query(query)
        job.result()
        removed = job.num_dml_affected_rows
        after = client.get_table(table_ref).num_rows
        if removed > 0:
            logger.info("  %s: removed %d dupes (%d → %d)", table_name, removed, before, after)
        else:
            logger.info("  %s: clean (no dupes)", table_name)
    except Exception as e:
        logger.warning("  %s: dedup failed — %s", table_name, e)


def main():
    client = bigquery.Client(project=PROJECT)
    logger.info("Weekly bars dedup starting (lookback=%d days)", LOOKBACK_DAYS)

    for table_name, time_col in BARS_TABLES:
        dedup_table(client, table_name, time_col)

    logger.info("Weekly bars dedup complete.")


if __name__ == "__main__":
    main()
