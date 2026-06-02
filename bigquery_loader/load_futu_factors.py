"""Load new Futu API data from GCS Parquet to BigQuery.

Usage:
    python -m bigquery_loader.load_futu_factors --table us_capital_distribution
    python -m bigquery_loader.load_futu_factors --table us_owner_plate --mode overwrite
"""

import argparse
import logging
from datetime import UTC, datetime

from google.cloud import bigquery

logger = logging.getLogger(__name__)
PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"
GCS_BUCKET = f"{PROJECT}-quant-data"

TABLE_CONFIG = {
    # market → source mapping for GCS path
    "us_capital_distribution": {"market": "us", "source": "capital_distribution"},
    "hk_capital_distribution": {"market": "hk", "source": "capital_distribution"},
    "us_insider_trade": {"market": "us", "source": "insider_trade"},
    "us_insider_holder": {"market": "us", "source": "insider_holder"},
    "us_daily_short_volume": {"market": "us", "source": "daily_short_volume"},
    "hk_daily_short_volume": {"market": "hk", "source": "daily_short_volume"},
    "us_earnings_price_move": {"market": "us", "source": "earnings_price_move"},
    "hk_earnings_price_move": {"market": "hk", "source": "earnings_price_move"},
    "us_earnings_price_history": {"market": "us", "source": "earnings_price_history"},
    "hk_earnings_price_history": {"market": "hk", "source": "earnings_price_history"},
    "us_owner_plate": {"market": "us", "source": "owner_plate"},
    "hk_owner_plate": {"market": "hk", "source": "owner_plate"},
    "us_rehab": {"market": "us", "source": "rehab"},
    "hk_rehab": {"market": "hk", "source": "rehab"},
    "hk_top_ten_brokers": {"market": "hk", "source": "top_ten_brokers"},
    # Phase 2
    "us_morningstar": {"market": "us", "source": "morningstar"},
    "hk_morningstar": {"market": "hk", "source": "morningstar"},
    "us_rating_summary": {"market": "us", "source": "rating_summary"},
    "us_stock_screen": {"market": "us", "source": "stock_screen"},
    "hk_stock_screen": {"market": "hk", "source": "stock_screen"},
}


def load_table(table_name: str, date_str: str = None, mode: str = "append"):
    """Load a single table from GCS to BQ."""
    if table_name not in TABLE_CONFIG:
        raise ValueError(f"Unknown table: {table_name}. Valid: {list(TABLE_CONFIG.keys())}")

    config = TABLE_CONFIG[table_name]
    market = config["market"]
    source = config["source"]

    if date_str is None:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    gcs_uri = f"gs://{GCS_BUCKET}/{market}/{source}/{date_str}/data.parquet"

    client = bigquery.Client(project=PROJECT)
    table_ref = f"{PROJECT}.{DATASET}.{table_name}"

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=(
            bigquery.WriteDisposition.WRITE_TRUNCATE
            if mode == "overwrite"
            else bigquery.WriteDisposition.WRITE_APPEND
        ),
        autodetect=True,
    )

    logger.info(f"Loading {gcs_uri} → {table_ref} (mode={mode})")
    load_job = client.load_table_from_uri(gcs_uri, table_ref, job_config=job_config)
    load_job.result()

    table = client.get_table(table_ref)
    logger.info(f"Loaded {table_name}: {table.num_rows} total rows")
    return table.num_rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Load Futu factor data from GCS to BigQuery")
    parser.add_argument(
        "--table",
        required=True,
        choices=list(TABLE_CONFIG.keys()),
        help="Target BigQuery table name",
    )
    parser.add_argument("--date", default=None, help="Date subfolder in GCS (default: today UTC)")
    parser.add_argument(
        "--mode",
        default="append",
        choices=["append", "overwrite"],
        help="Write mode: append (default) or overwrite (truncates table)",
    )
    args = parser.parse_args()

    try:
        rows = load_table(args.table, args.date, args.mode)
        print(f"SUCCESS: {rows} rows in {args.table}")
    except Exception as e:
        logger.error(f"Failed to load {args.table}: {e}")
        raise
