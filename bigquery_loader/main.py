"""BigQuery data loader Cloud Run Job.

Runs daily via Cloud Scheduler. Loads Parquet files from GCS into
native BigQuery tables using single-level globs. Uses WRITE_APPEND
with dedup on _ingest_time to safely handle overlapping collector runs.

Env vars:
    GCS_BUCKET: GCS bucket name (required)
    GCP_PROJECT: GCP project ID (required)
    MARKET: market to load, e.g. "us" or "crypto" (default: us)
    TABLE: BigQuery table name (default: us_bars)
    FREQUENCY: bar frequency for path glob (default: 5m)
    LOAD_DAYS: days of historical data to load (default: 7)
"""

import io
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
    bigquery.SchemaField("_ingest_time", "TIMESTAMP"),
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


def load_market(client, bucket, market, frequency, start_date, end_date, project,
                dataset="quant", table="us_bars"):
    """Load a market's Parquet data over a date range."""
    for i in range((end_date - start_date).days + 1):
        d = end_date - timedelta(days=i)
        date_str = d.isoformat()
        load_day(client, bucket, market, frequency, date_str, project, dataset, table)


def load_day(client, bucket, market, frequency, date_str, project, dataset="quant", table="us_bars"):
    """Load one day of Parquet files using single-level glob.

    Falls back to pandas-based loading if BigQuery rejects nanosecond timestamps.
    """
    pattern = (
        f"raw/{market}/bars/freq={frequency}/"
        f"year={date_str[:4]}/month={date_str[5:7]}/"
        f"day={date_str[8:10]}/*.parquet"
    )
    uri = f"gs://{bucket}/{pattern}"

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
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
        err_msg = str(e)
        if "nanoseconds" in err_msg or "NANOS" in err_msg:
            _load_day_via_dataframe(client, uri, project, dataset, table,
                                    date_str, err_msg)
        else:
            logger.warning("Load failed for %s: %s", date_str, e)


def _load_day_via_dataframe(client, uri, project, dataset, table,
                            date_str, original_error):
    """Fallback: read Parquet into pandas, cast timestamp to microseconds, load."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from google.cloud import storage

    logger.info("Falling back to pandas load for %s (ns timestamp)", date_str)

    # List blobs matching the glob
    bucket_name = uri.split("/")[2]
    prefix = "/".join(uri.replace(f"gs://{bucket_name}/", "").replace("*.parquet", "").split("/"))
    gcs = storage.Client()
    blobs = list(gcs.bucket(bucket_name).list_blobs(prefix=prefix))
    parquet_blobs = [b for b in blobs if b.name.endswith(".parquet")]

    if not parquet_blobs:
        logger.warning("No parquet files found at %s", prefix)
        return

    rows_loaded = 0
    for blob in parquet_blobs:
        try:
            buf = blob.download_as_bytes()
            table_arrow = pq.read_table(io.BytesIO(buf))
            # Cast timestamp columns to microseconds
            for i, field in enumerate(table_arrow.schema):
                if pa.types.is_timestamp(field.type):
                    col = table_arrow.column(i).cast(
                        pa.timestamp("us", tz=field.type.tz)
                    )
                    table_arrow = table_arrow.set_column(
                        i, field.with_type(pa.timestamp("us", tz=field.type.tz)), col
                    )
            df = table_arrow.to_pandas()
        except Exception as read_err:
            logger.warning("Failed to read %s: %s", blob.name, read_err)
            continue

        if df.empty:
            continue

        table_ref = f"{project}.{dataset}.{table}"
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            time_partitioning=bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="timestamp",
            ),
            clustering_fields=["symbol"],
        )
        try:
            load_job = client.load_table_from_dataframe(
                df, table_ref, job_config=job_config
            )
            load_job.result()
            rows_loaded += len(df)
        except Exception as df_err:
            logger.warning("DataFrame load failed for %s: %s", blob.name, df_err)

    logger.info("Pandas fallback loaded %s: %d rows", date_str, rows_loaded)


def dedup_table(client, project, dataset="quant", table="us_bars"):
    """Remove duplicate (symbol, timestamp) rows, keeping the latest _ingest_time."""
    query = f"""
        CREATE OR REPLACE TABLE `{project}.{dataset}.{table}`
        PARTITION BY DATE(timestamp)
        CLUSTER BY symbol
        AS
        SELECT * EXCEPT(rn) FROM (
          SELECT *, ROW_NUMBER() OVER (
            PARTITION BY symbol, timestamp ORDER BY _ingest_time DESC
          ) AS rn
          FROM `{project}.{dataset}.{table}`
        ) WHERE rn = 1
    """
    logger.info("Deduping %s.%s.%s ...", project, dataset, table)
    try:
        job = client.query(query)
        job.result()
        logger.info("Dedup complete: %s.%s.%s", project, dataset, table)
    except Exception as e:
        logger.warning("Dedup failed for %s.%s.%s: %s", project, dataset, table, e)


def main():
    bucket = os.environ["GCS_BUCKET"]
    project = os.environ["GCP_PROJECT"]
    market = os.environ.get("MARKET", "us")
    table = os.environ.get("TABLE", "us_bars")
    frequency = os.environ.get("FREQUENCY", "5m")
    load_days = int(os.environ.get("LOAD_DAYS", "7"))
    start_date_str = os.environ.get("START_DATE", "")

    client = bigquery.Client(project=project)
    ensure_dataset(client, project)
    ensure_table(client, project, table_id=table)

    today = datetime.now(timezone.utc).date()
    if start_date_str:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end = min(start + timedelta(days=load_days - 1), today)
    else:
        start = today - timedelta(days=load_days - 1)
        end = today
    load_market(client, bucket, market, frequency, start, end, project, table=table)
    dedup_table(client, project, table=table)

    logger.info("BigQuery load complete: market=%s freq=%s table=%s %d days (%s → %s)",
                market, frequency, table, (end - start).days + 1, start.isoformat(), end.isoformat())


if __name__ == "__main__":
    main()
