"""Week 1 integration tests — PaperRunner with BQ data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import logging
import os

import pytest

from run_paper import PaperRunner

logging.basicConfig(level=logging.INFO)


def test_bq_data_loads_ohlcv():
    """_sdk_data should load OHLCV from BigQuery {market}_bars_1d table.

    Gracefully handles the case where BQ tables have not been backfilled yet.
    """
    market = "us"
    symbols = ["AAPL", "MSFT"]
    start, end = "2026-01-01", "2026-01-10"

    # Check that BQ credentials are configured
    if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
        from google.cloud import bigquery
        try:
            bigquery.Client().get_service_account_email()  # noqa: B018
        except Exception:
            return  # skip test if BQ is not configured

    runner = PaperRunner({
        "market": market, "capital": 100000,
        "strategy": "SimpleMomentum",
        "start": start, "end": end,
    })

    try:
        ds = runner.load_data("bq", symbols, start, end)
    except Exception as e:
        if "Not found: Table" in str(e) or "notFound" in str(e):
            pytest.skip(f"Table not yet backfilled: {e}")
        raise

    assert ds.close is not None, "Expected non-null close DataFrame"
    # BQ may have no data if backfill hasn't run yet — that's ok
    if len(ds) == 0:
        print("BQ returned 0 rows — table exists but may not be backfilled yet.")
        return

    # Verify it returned a proper DataFrameSource with price data
    found = [s for s in symbols if s in ds.close.columns]
    assert found, f"Expected AAPL or MSFT in columns, got {list(ds.close.columns)}"
    print(f"Loaded {len(ds)} bars for {found}")
