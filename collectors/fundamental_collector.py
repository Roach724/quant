#!/usr/bin/env python3
"""F10 fundamental data collector — writes Parquet to GCS.

Market is auto-derived from symbol prefix: US.AAPL → us, HK.00700 → hk.

Usage:
    GCS_BUCKET=xxx python collectors/fundamental_collector.py --source valuation
    GCS_BUCKET=xxx python collectors/fundamental_collector.py --source all
"""
import argparse
import io
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage

from collectors.adapters.futu_financials_adapter import FutuFinancialsAdapter
from collectors.adapters.futu_valuation_adapter import FutuValuationAdapter
from collectors.adapters.futu_short_interest_adapter import FutuShortInterestAdapter
from collectors.adapters.futu_capital_flow_adapter import FutuCapitalFlowAdapter
from collectors.adapters.futu_analyst_adapter import FutuAnalystAdapter
from collectors.adapters.futu_shareholder_adapter import FutuShareholderAdapter

ADAPTERS = {
    "financials": FutuFinancialsAdapter,
    "valuation": FutuValuationAdapter,
    "short_interest": FutuShortInterestAdapter,
    "capital_flow": FutuCapitalFlowAdapter,
    "analyst": FutuAnalystAdapter,
    "shareholder": FutuShareholderAdapter,
}

log = logging.getLogger("fundamental_collector")


def _df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    table = pa.Table.from_pandas(df)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _build_f10_gcs_path(market: str, data_type: str, symbol: str, timestamp: datetime) -> str:
    safe_sym = symbol.replace(".", "_")
    return (
        f"raw/{market.lower()}/f10/{data_type}/"
        f"year={timestamp.year}/month={timestamp.month:02d}/day={timestamp.day:02d}/"
        f"symbol={safe_sym}.parquet"
    )


def main():
    parser = argparse.ArgumentParser(description="F10 fundamental data collector")
    parser.add_argument("--source", choices=list(ADAPTERS.keys()) + ["all"], required=True)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--gcs-bucket", default=os.environ.get("GCS_BUCKET", ""))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        from common.logging_util import setup_root_json
        setup_root_json(module="cron")
    except Exception:
        pass

    if not args.gcs_bucket:
        log.error("GCS_BUCKET env var or --gcs-bucket required")
        sys.exit(1)

    sources = list(ADAPTERS.keys()) if args.source == "all" else [args.source]

    for source in sources:
        log.info("Collecting %s (all markets)...", source)
        cls = ADAPTERS[source]
        adapter = cls(symbols=args.symbols if args.symbols else None)
        try:
            data = adapter.fetch_all()
            log.info("  %s: got data for %d symbols", source, len(data))
            if data:
                _write_to_gcs(data, source, args.gcs_bucket)
        finally:
            adapter.close()


def _write_to_gcs(data: dict, source: str, bucket_name: str):
    """Write F10 data to GCS Parquet. Market derived from symbol prefix."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    now = datetime.now(timezone.utc)
    paths = []
    us_count = hk_count = 0
    for sym, df in data.items():
        if df.empty:
            continue
        market = "hk" if sym.startswith("HK.") else "us"
        if market == "hk":
            hk_count += 1
        else:
            us_count += 1
        path = _build_f10_gcs_path(market, source, sym, now)
        blob = bucket.blob(path)
        blob.upload_from_string(
            _df_to_parquet_bytes(df),
            content_type="application/octet-stream",
        )
        paths.append(f"gs://{bucket_name}/{path}")
    log.info("  Wrote %d files (%d US + %d HK) to gs://%s/raw/{us,hk}/f10/%s/",
             len(paths), us_count, hk_count, bucket_name, source)


if __name__ == "__main__":
    main()
