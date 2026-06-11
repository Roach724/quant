"""BigQuery direct writer — replaces GCS → BQ Loader pipeline.

Uses client.insert_rows_json() with timeout to prevent hanging.
At our data volume (~1 row/second), this is more than sufficient.

Usage:
    from common.bq_writer import write_bars_to_bq, write_rows_to_bq

    n = write_bars_to_bq(df, table_id="hk_bars_5m")
    n = write_rows_to_bq(df, table_name="us_capital_distribution")
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
from google.api_core import exceptions as gapi_exceptions
from google.cloud import bigquery

logger = logging.getLogger(__name__)

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"
MAX_RETRIES = 3
RETRY_BASE_S = 1.0


def _df_to_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert DataFrame to list of dict rows, handling Timestamps and NaN."""
    rows = []
    for _, row in df.iterrows():
        d = {}
        for k, v in row.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.isoformat()
            elif isinstance(v, float) and pd.isna(v):
                d[k] = None
            elif isinstance(v, pd.Period):
                d[k] = str(v)
            else:
                d[k] = v
        rows.append(d)
    return rows


def _bq_table_ref(table_name: str, dataset: str = DATASET, project: str = PROJECT) -> str:
    return f"{project}.{dataset}.{table_name}"


def write_rows_to_bq(
    df: pd.DataFrame,
    table_name: str,
    dataset: str = DATASET,
    project: str = PROJECT,
) -> int:
    """Write DataFrame directly to BigQuery.

    Args:
        df: DataFrame. Column names must match BQ table schema.
        table_name: BQ table name.
        dataset: BQ dataset.
        project: GCP project.

    Returns:
        Number of rows written.
    """
    if df.empty:
        logger.info("Empty DataFrame, skipping BQ write for %s.%s", dataset, table_name)
        return 0

    # Strip columns not in BQ schema to avoid insert errors
    client = bigquery.Client(project=project)
    table_ref = _bq_table_ref(table_name, dataset, project)
    try:
        table = client.get_table(table_ref)
        valid_cols = {s.name for s in table.schema}
        extra = set(df.columns) - valid_cols
        if extra:
            df = df.drop(columns=list(extra))
            logger.warning("Dropped columns not in BQ schema: %s", sorted(extra))
    except Exception:
        pass  # Schema lookup failed, try anyway

    rows = _df_to_rows(df)
    total = len(rows)
    logger.info("Writing %d rows to %s", total, table_ref)

    # Chunk rows to avoid BQ 413 (payload too large)
    CHUNK_SIZE = 10000
    written = 0
    for chunk_start in range(0, total, CHUNK_SIZE):
        chunk = rows[chunk_start:chunk_start + CHUNK_SIZE]
        chunk_end = min(chunk_start + CHUNK_SIZE, total)

        for attempt in range(MAX_RETRIES):
            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(client.insert_rows_json, table_ref, chunk)
                    errors = future.result(timeout=30)
                if errors:
                    error_msgs = [e.get("errors", e) for e in errors]
                    raise RuntimeError(f"Insert errors: {error_msgs[:3]}")
                written += len(chunk)
                break
            except concurrent.futures.TimeoutError:
                logger.error("BQ write timed out after 30s (attempt %d/%d, chunk %d-%d/%d)",
                             attempt + 1, MAX_RETRIES, chunk_start, chunk_end, total)
                client = bigquery.Client(project=project)
            except (gapi_exceptions.ServiceUnavailable, gapi_exceptions.ResourceExhausted, RuntimeError) as e:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BASE_S * (2 ** attempt)
                    logger.warning(
                        "BQ write chunk %d-%d/%d attempt %d/%d failed (%s), retrying in %.0fs",
                        chunk_start, chunk_end, total, attempt + 1, MAX_RETRIES, e, wait,
                    )
                    time.sleep(wait)
                    client = bigquery.Client(project=project)
                else:
                    logger.error("BQ write chunk %d-%d/%d failed after %d attempts: %s",
                                 chunk_start, chunk_end, total, MAX_RETRIES, e)
                    raise

    logger.info("BQ write complete: %d rows → %s", written, table_ref)

    # Streaming buffer visibility: rows appear within seconds
    return written


def _filter_existing_rows(
    df: pd.DataFrame,
    table_id: str,
    dataset: str,
    project: str,
) -> pd.DataFrame:
    """Remove rows from df that already exist in BQ (by symbol, timestamp)."""
    try:
        table_ref = _bq_table_ref(table_id, dataset, project)
        min_ts = df["timestamp"].min()
        max_ts = df["timestamp"].max()
        symbols = df["symbol"].unique().tolist()

        # Chunk symbols query to avoid huge IN clauses
        client = bigquery.Client(project=project)
        existing_keys: set[tuple[str, str]] = set()
        chunk_sz = 200

        for i in range(0, len(symbols), chunk_sz):
            chunk = symbols[i : i + chunk_sz]
            q = f"""
            SELECT DISTINCT symbol, FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', timestamp) AS ts
            FROM `{table_ref}`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp BETWEEN @min_ts AND @max_ts
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("symbols", "STRING", chunk),
                    bigquery.ScalarQueryParameter("min_ts", "TIMESTAMP", min_ts),
                    bigquery.ScalarQueryParameter("max_ts", "TIMESTAMP", max_ts),
                ]
            )
            for row in client.query(q, job_config=job_config).result():
                existing_keys.add((row.symbol, row.ts))

        if not existing_keys:
            return df

        # Build lookup key and filter
        df["_bq_key"] = df["symbol"].astype(str) + "|" + df["timestamp"].apply(
            lambda t: t.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(t, "strftime") else str(t)[:19]
        )
        existing_set = {f"{s}|{t}" for s, t in existing_keys}
        before = len(df)
        df = df[~df["_bq_key"].isin(existing_set)].drop(columns=["_bq_key"])
        skipped = before - len(df)
        if skipped > 0:
            logger.info("Skipped %d existing rows (of %d) in %s", skipped, before, table_ref)
    except Exception as e:
        logger.warning("Skip-existing check failed, writing all rows: %s", e)
    return df


def write_bars_to_bq(
    df: pd.DataFrame,
    table_id: str,
    dataset: str = DATASET,
    project: str = PROJECT,
    skip_existing: bool = True,
) -> int:
    """Write OHLCV bars DataFrame to BigQuery.

    When skip_existing=True (default), queries BQ for existing (symbol, timestamp)
    pairs in the DataFrame's time range and inserts only truly new rows.
    This prevents duplicates without risking data loss.

    Normalizes symbols to canonical BQ format (HK.XXXXX / US.XXX) before writing.
    """
    # Defensive: normalize symbols before writing to prevent format contamination
    market = "hk" if table_id.startswith("hk") else "us"
    from common.normalize import queryize_symbol_series
    df = df.copy()
    df["symbol"] = queryize_symbol_series(df["symbol"], market)

    if skip_existing and not df.empty and "symbol" in df.columns and "timestamp" in df.columns:
        df = _filter_existing_rows(df, table_id, dataset, project)

    return write_rows_to_bq(df, table_name=table_id, dataset=dataset, project=project)
