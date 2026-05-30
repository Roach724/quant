"""Data quality checker — queries BigQuery for completeness, freshness, sanity.

Env vars:
    GCP_PROJECT: GCP project ID (default: deductive-notch-495015-c2)
    MARKET: market to check, e.g. "us" or "hk" (default: us)
    FREQUENCY: bar frequency, e.g. "1d" or "5m" (default: 1d)
    MAX_AGE_HOURS: max bar age before freshness alert (default: 24)
    LOOKBACK_DAYS: days of data to scan (default: 7)
"""

import logging
import os
from datetime import datetime, timezone, timedelta

import pandas as pd
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EXPECTED_DAILY_BARS = 390


def check_completeness(df: pd.DataFrame, expected_bars: int = EXPECTED_DAILY_BARS) -> list[str]:
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
    for _, row in df.iterrows():
        if row["high"] < row["low"]:
            issues.append(
                f"Sanity: {row['symbol']} at {row['timestamp']} high < low: "
                f"{row['high']} < {row['low']}"
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


def query_bars(market: str, frequency: str, lookback_days: int) -> pd.DataFrame:
    project = os.environ.get("GCP_PROJECT", "deductive-notch-495015-c2")
    table = f"{project}.quant.{market}_bars_{frequency}"

    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    query = f"""
        SELECT symbol, timestamp, open, high, low, close, volume
        FROM `{table}`
        WHERE DATE(timestamp) BETWEEN @start AND @end
        ORDER BY symbol, timestamp
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("start", "STRING", start),
        bigquery.ScalarQueryParameter("end", "STRING", end),
    ])

    client = bigquery.Client(project=project)
    logger.info("Querying %s (market=%s freq=%s range=%s..%s)", table, market, frequency, start, end)
    df = client.query(query, job_config=job_config).to_dataframe()

    if df.empty:
        logger.warning("No data returned from %s", table)
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def main(event=None, context=None):
    market = os.environ.get("MARKET", "us")
    frequency = os.environ.get("FREQUENCY", "1d")
    max_age_hours = int(os.environ.get("MAX_AGE_HOURS", "24"))
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "7"))

    df = query_bars(market, frequency, lookback_days)
    if df.empty:
        logger.warning("No data to check")
        return {"issues": 0, "status": "no_data"}

    logger.info("Checking %d rows across %d symbols", len(df), df["symbol"].nunique())

    all_issues = []
    all_issues.extend(check_sanity(df))
    all_issues.extend(check_freshness(df, max_age_hours))
    all_issues.extend(check_completeness(df))

    if all_issues:
        logger.warning("Quality issues found: %d", len(all_issues))
        for issue in all_issues:
            logger.warning(issue)
    else:
        logger.info("All quality checks passed")

    return {"issues": len(all_issues)}


if __name__ == "__main__":
    main()
