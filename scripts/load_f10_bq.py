#!/usr/bin/env python3
"""Load F10 Parquet data from GCS into BigQuery.

Usage:
    python scripts/load_f10_bq.py --source valuation --market us
    python scripts/load_f10_bq.py --source valuation --market hk
    python scripts/load_f10_bq.py --source all
"""
import argparse
import io
import logging
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery, storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("load_f10_bq")

F10_CONFIG = {
    "valuation":      [("us", "us_valuation"),     ("hk", "hk_valuation")],
    "short_interest": [("us", "us_short_interest"), ("hk", "hk_short_interest")],
    "capital_flow":   [("us", "us_capital_flow"),   ("hk", "hk_capital_flow")],
    "analyst":        [("us", "us_analyst"),         ("hk", "hk_analyst")],
    "shareholder":    [("us", "us_shareholder"),    ("hk", "hk_shareholder")],
    "financials":     [("us", "us_financials"),     ("hk", "hk_financials")],
}


def json_table_schema():
    return [
        bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("data", "JSON"),
        bigquery.SchemaField("fetched_at", "TIMESTAMP"),
        bigquery.SchemaField("ingest_time", "TIMESTAMP"),
    ]


def valuation_table_schema():
    return [
        bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("valuation_type", "STRING"),
        bigquery.SchemaField("interval", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("value", "FLOAT64"),
        bigquery.SchemaField("plate_value", "FLOAT64"),
        bigquery.SchemaField("ingest_time", "TIMESTAMP"),
    ]


def ensure_table(client, project, dataset, table, schema):
    table_ref = bigquery.Table(f"{project}.{dataset}.{table}", schema=schema)
    table_ref.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY, field="ingest_time")
    table_ref.clustering_fields = ["symbol"]
    try:
        client.create_table(table_ref, exists_ok=True)
        log.info("Table %s.%s ready", dataset, table)
    except Exception as e:
        log.warning("Table ensure: %s", e)


def load_valuation(client, bucket_name, market, table, project, dataset="quant"):
    prefix = f"raw/{market}/f10/valuation/"
    gcs = storage.Client()
    blobs = list(gcs.bucket(bucket_name).list_blobs(prefix=prefix))
    parquet_blobs = [b for b in blobs if b.name.endswith(".parquet")]
    if not parquet_blobs:
        log.warning("No parquet files at %s", prefix)
        return 0

    table_ref = f"{project}.{dataset}.{table}"
    now = datetime.now(timezone.utc)
    total = 0
    for blob in parquet_blobs:
        try:
            buf = blob.download_as_bytes()
            df = pd.read_parquet(io.BytesIO(buf))
        except Exception as e:
            log.warning("Read failed: %s: %s", blob.name, e)
            continue
        if df.empty:
            continue
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        df["ingest_time"] = now
        keep = ["symbol", "valuation_type", "interval", "date", "value", "plate_value", "ingest_time"]
        df = df[[c for c in keep if c in df.columns]]
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
        try:
            job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
            job.result()
            total += len(df)
        except Exception as e:
            log.warning("BQ load failed for %s: %s", blob.name, e)
    log.info("Loaded %d rows → %s.%s", total, dataset, table)
    return total


def load_json_source(client, bucket_name, market, table, source, project, dataset="quant"):
    prefix = f"raw/{market}/f10/{source}/"
    gcs = storage.Client()
    blobs = list(gcs.bucket(bucket_name).list_blobs(prefix=prefix))
    parquet_blobs = [b for b in blobs if b.name.endswith(".parquet")]
    if not parquet_blobs:
        log.warning("No parquet files at %s", prefix)
        return 0

    table_ref = f"{project}.{dataset}.{table}"
    now = datetime.now(timezone.utc)
    total = 0
    for blob in parquet_blobs:
        try:
            buf = blob.download_as_bytes()
            df = pd.read_parquet(io.BytesIO(buf))
        except Exception as e:
            log.warning("Read failed: %s: %s", blob.name, e)
            continue
        if df.empty:
            continue
        meta_cols = {"symbol", "data_type", "fetched_at"}
        val_cols = [c for c in df.columns if c not in meta_cols and c != "ingest_time"]
        if val_cols and "data" not in df.columns:
            import json
            df["data"] = df.apply(
                lambda row: json.dumps({c: row[c] for c in val_cols}, default=str), axis=1)
        keep = ["symbol"]
        if "data_type" in df.columns:
            keep.append("data_type")
        if "data" in df.columns:
            keep.append("data")
        if "fetched_at" in df.columns:
            keep.append("fetched_at")
        df = df[[c for c in keep if c in df.columns]]
        df["ingest_time"] = now
        if "fetched_at" in df.columns:
            df["fetched_at"] = pd.to_datetime(df["fetched_at"], errors="coerce")
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
        try:
            job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
            job.result()
            total += len(df)
        except Exception as e:
            log.warning("BQ load failed for %s: %s", blob.name, e)
    log.info("Loaded %d rows → %s.%s", total, dataset, table)
    return total


def main():
    parser = argparse.ArgumentParser(description="Load F10 Parquet from GCS → BQ")
    parser.add_argument("--source", required=True,
                        choices=list(F10_CONFIG.keys()) + ["all"])
    parser.add_argument("--market", default="us", choices=["us", "hk", "all"])
    parser.add_argument("--bucket", default=os.environ.get("GCS_BUCKET", "deductive-notch-495015-c2-quant-data"))
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT", "deductive-notch-495015-c2"))
    parser.add_argument("--dataset", default="quant")
    args = parser.parse_args()

    client = bigquery.Client(project=args.project)
    sources = list(F10_CONFIG.keys()) if args.source == "all" else [args.source]
    markets = ["us", "hk"] if args.market == "all" else [args.market]

    for src in sources:
        for mkt, table in F10_CONFIG[src]:
            if mkt not in markets:
                continue
            if src == "valuation":
                ensure_table(client, args.project, args.dataset, table, valuation_table_schema())
                load_valuation(client, args.bucket, mkt, table, args.project, args.dataset)
            else:
                ensure_table(client, args.project, args.dataset, table, json_table_schema())
                load_json_source(client, args.bucket, mkt, table, src, args.project, args.dataset)

    log.info("F10 BQ load complete for sources: %s", sources)


if __name__ == "__main__":
    main()
