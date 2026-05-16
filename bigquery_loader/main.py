"""BigQuery data loader Cloud Run Job.

Runs daily via Cloud Scheduler. Loads Parquet files from GCS into
native BigQuery tables using single-level globs (avoiding BigQuery's
"multiple asterisks not supported" limitation).

Env vars:
    GCS_BUCKET: GCS bucket name (required)
    GCP_PROJECT: GCP project ID (required)
    MARKET: market to load, e.g. "us" or "crypto" (default: us)
    TABLE: BigQuery table name (default: us_bars)
    LOAD_DAYS: days of historical data to load (default: 7)
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCHEMA = [
    bigquery.SchemaField("symbol", "STRING"),
    bigquery.SchemaField("timestamp", "TIMESTAMP"),
    bigquery.SchemaField("open", "FLOAT64"),
    bigquery.SchemaField("high", "FLOAT64"),
    bigquery.SchemaField("low", "FLOAT64"),
    bigquery.SchemaField("close", "FLOAT64"),
    bigquery.SchemaField("volume", "INT64"),
    bigquery.SchemaField("market", "STRING"),
    bigquery.SchemaField("frequency", "STRING"),
]


def ensure_dataset(client, project, dataset_id="quant", location="asia-east2"):
    ds_ref = bigquery.Dataset(f"{project}.{dataset_id}")
    ds_ref.location = location
    try:
        client.create_dataset(ds_ref, exists_ok=True)
        logger.info("Dataset %s.%s ready", project, dataset_id)
    except Exception as e:
        logger.warning("Dataset ensure: %s", e)


def ensure_table(client, project, dataset_id="quant", table_id="us_bars"):
    table_ref = bigquery.Table(f"{project}.{dataset_id}.{table_id}", schema=SCHEMA)
    table_ref.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="timestamp",
    )
    table_ref.clustering_fields = ["symbol"]
    try:
        client.create_table(table_ref, exists_ok=True)
        logger.info("Table %s.%s.%s ready", project, dataset_id, table_id)
    except Exception as e:
        logger.warning("Table ensure: %s", e)


def load_market(client, bucket, market, start_date, end_date, project,
                dataset="quant", table="us_bars"):
    """Load a market's Parquet data over a date range."""
    for i in range((end_date - start_date).days + 1):
        d = end_date - timedelta(days=i)
        date_str = d.isoformat()
        load_day(client, bucket, market, date_str, project, dataset, table)


def load_day(client, bucket, market, date_str, project, dataset="quant", table="us_bars"):
    """Load one day of Parquet files using single-level glob."""
    pattern = (
        f"raw/{market}/bars/year={date_str[:4]}/"
        f"month={date_str[5:7]}/day={date_str[8:10]}/*.parquet"
    )
    uri = f"gs://{bucket}/{pattern}"

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        time_partitioning=bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="timestamp",
        ),
        clustering_fields=["symbol"],
    )

    table_ref = f"{project}.{dataset}.{table}${date_str.replace('-', '')}"
    logger.info("Loading %s -> %s", uri, table_ref)

    try:
        load_job = client.load_table_from_uri(
            uri, table_ref, job_config=job_config
        )
        load_job.result()
        logger.info("Loaded %s: %d rows", date_str, load_job.output_rows)
    except Exception as e:
        logger.warning("Load failed for %s: %s", date_str, e)


def main():
    bucket = os.environ["GCS_BUCKET"]
    project = os.environ["GCP_PROJECT"]
    market = os.environ.get("MARKET", "us")
    table = os.environ.get("TABLE", "us_bars")
    load_days = int(os.environ.get("LOAD_DAYS", "7"))

    client = bigquery.Client(project=project)
    ensure_dataset(client, project)
    ensure_table(client, project, table_id=table)

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=load_days - 1)
    load_market(client, bucket, market, start, today, project, table=table)

    logger.info("BigQuery load complete: market=%s table=%s %d days",
                market, table, load_days)


if __name__ == "__main__":
    main()
