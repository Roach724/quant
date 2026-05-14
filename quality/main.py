"""Data quality Cloud Function entrypoint."""

import logging
import os
from datetime import datetime, timezone

import pandas as pd
from google.cloud import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def check_completeness(df: pd.DataFrame, expected_bars: int = 390) -> list[str]:
    issues = []
    for symbol, group in df.groupby("symbol"):
        actual = len(group)
        if actual < expected_bars * 0.95:
            issues.append(
                f"Completeness: {symbol} has {actual} bars, expected {expected_bars} "
                f"(coverage: {actual / expected_bars:.1%})"
            )
    return issues


def check_freshness(df: pd.DataFrame, max_age_hours: int = 24) -> list[str]:
    issues = []
    now = datetime.now(timezone.utc)
    for symbol, group in df.groupby("symbol"):
        latest = group["timestamp"].max()
        age = (now - latest).total_seconds() / 3600
        if age > max_age_hours:
            issues.append(
                f"Freshness: {symbol} latest bar is {latest.isoformat()} ({age:.1f}h ago)"
            )
    return issues


def check_sanity(df: pd.DataFrame) -> list[str]:
    issues = []
    for idx, row in df.iterrows():
        if row["high"] < row["low"]:
            issues.append(
                f"Sanity: {row['symbol']} at {row['timestamp']} high < low: {row['high']} < {row['low']}"
            )
        for col in ["open", "high", "low", "close"]:
            if row[col] <= 0:
                issues.append(
                    f"Sanity: {row['symbol']} at {row['timestamp']} has {col} = {row[col]}"
                )
    if len(df) > 30:
        vol_std = df["volume"].std()
        vol_mean = df["volume"].mean()
        spikes = df[df["volume"] > vol_mean + 10 * vol_std]
        for _, row in spikes.iterrows():
            issues.append(
                f"Sanity: {row['symbol']} at {row['timestamp']} volume spike: {row['volume']:,}"
            )
    return issues


def main(event=None, context=None):
    bucket_name = os.environ["GCS_BUCKET"]
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    prefix = f"raw/us/bars/{today}/"
    blobs = list(bucket.list_blobs(prefix=prefix, max_results=500))
    logger.info("Checking %d blobs under %s", len(blobs), prefix)

    all_issues = []

    for blob in blobs:
        df = pd.read_parquet(f"gs://{bucket_name}/{blob.name}")
        all_issues.extend(check_sanity(df))
        all_issues.extend(check_freshness(df))

        symbol = blob.name.split("/")[-1].replace(".parquet", "")
        symbol_df = df[df["symbol"] == symbol]
        all_issues.extend(check_completeness(symbol_df))

    if all_issues:
        logger.warning("Quality issues found: %d", len(all_issues))
        for issue in all_issues:
            logger.warning(issue)
    else:
        logger.info("All quality checks passed for %d blobs", len(blobs))

    return {"issues": len(all_issues)}
