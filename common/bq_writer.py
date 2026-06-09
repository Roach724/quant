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

    written = 0
    for attempt in range(MAX_RETRIES):
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(client.insert_rows_json, table_ref, rows)
                errors = future.result(timeout=30)
            if errors:
                error_msgs = [e.get("errors", e) for e in errors]
                raise RuntimeError(f"Insert errors: {error_msgs[:3]}")
            written = total
            break
        except concurrent.futures.TimeoutError:
            logger.error("BQ write timed out after 30s (attempt %d/%d)", attempt + 1, MAX_RETRIES)
            client = bigquery.Client(project=project)
        except (gapi_exceptions.ServiceUnavailable, gapi_exceptions.ResourceExhausted, RuntimeError) as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BASE_S * (2 ** attempt)
                logger.warning(
                    "BQ write attempt %d/%d failed (%s), retrying in %.0fs",
                    attempt + 1, MAX_RETRIES, e, wait,
                )
                time.sleep(wait)
                client = bigquery.Client(project=project)  # fresh client
            else:
                logger.error("BQ write failed after %d attempts: %s", MAX_RETRIES, e)
                raise

    logger.info("BQ write complete: %d rows → %s", written, table_ref)

    # Streaming buffer visibility: rows appear within seconds
    return written


def write_bars_to_bq(
    df: pd.DataFrame,
    table_id: str,
    dataset: str = DATASET,
    project: str = PROJECT,
) -> int:
    """Write OHLCV bars DataFrame directly to BigQuery.

    Normalizes symbols to canonical BQ format (HK.XXXXX / US.XXX) before writing.
    """
    # Defensive: normalize symbols before writing to prevent format contamination
    market = "hk" if table_id.startswith("hk") else "us"
    from common.normalize import queryize_symbol_series
    df = df.copy()
    df["symbol"] = queryize_symbol_series(df["symbol"], market)
    return write_rows_to_bq(df, table_name=table_id, dataset=dataset, project=project)
