# Phase 1: Quantitative Trading Data Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a GCP-hosted data pipeline that collects minute-level US stock OHLCV bars from free APIs, stores them as partitioned Parquet in Cloud Storage, and serves them via a Go REST API + Python SDK for Jupyter-based research.

**Architecture:** Python Cloud Run Jobs fetch data via pluggable market adapters and write to GCS. A thin Go Cloud Run service reads Parquet from GCS and serves REST endpoints. A Python SDK wraps both the Go API and direct GCS access for research notebooks. All infrastructure is defined in Terraform, all serverless.

**Tech Stack:** Python 3.12+ (pandas, pyarrow, yfinance, alpaca-py, gcsfs, pytest, VCR.py), Go 1.23+ (standard library + cloud.google.com/go/storage, github.com/apache/arrow-go), Terraform (GCP provider), Cloud Run / Cloud Run Jobs / Cloud Storage / BigQuery / Cloud Scheduler / Artifact Registry.

---

## File Structure

```
quant/
├── collectors/              # Python — data ingestion
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py          # MarketAdapter protocol
│   │   ├── yfinance_adapter.py
│   │   └── alpaca_adapter.py
│   ├── schema.py            # Bar, Quote, Trade dataclasses
│   ├── storage.py           # GCS write helpers
│   ├── main.py              # Cloud Run Job entrypoint
│   ├── requirements.txt
│   └── Dockerfile
├── query-api/               # Go — data serving
│   ├── cmd/server/main.go
│   ├── internal/
│   │   ├── handler/handler.go
│   │   ├── reader/reader.go
│   │   └── market/market.go
│   ├── go.mod
│   ├── go.sum
│   └── Dockerfile
├── sdk/                     # Python — research interface
│   ├── quant/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   └── direct.py
│   └── pyproject.toml
├── quality/                 # Data quality Cloud Function
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── storage.tf
│   ├── service_accounts.tf
│   ├── artifact_registry.tf
│   ├── cloud_run_jobs.tf
│   ├── cloud_run_api.tf
│   ├── scheduler.tf
│   └── bigquery.tf
├── notebooks/
│   └── 01_validate_pipeline.ipynb
├── .github/workflows/ci.yml
├── pyproject.toml           # Root dev tools config
└── README.md
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `collectors/__init__.py`
- Create: `collectors/adapters/__init__.py`
- Create: `sdk/quant/__init__.py`
- Create: `quality/__init__.py`

- [ ] **Step 1: Create root pyproject.toml**

```toml
[project]
name = "quant"
version = "0.1.0"
requires-python = ">=3.12"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true

[tool.pytest.ini_options]
testpaths = ["collectors/tests", "sdk/tests", "quality/tests"]
pythonpath = [".", "collectors", "sdk"]
```

- [ ] **Step 2: Create empty __init__.py files**

```bash
mkdir -p collectors/adapters sdk/quant quality
touch collectors/__init__.py collectors/adapters/__init__.py sdk/quant/__init__.py quality/__init__.py
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml collectors/__init__.py collectors/adapters/__init__.py sdk/quant/__init__.py quality/__init__.py
git commit -m "chore: scaffold project structure and dev tooling config"
```

---

### Task 2: Terraform — GCS Storage & Service Accounts

**Files:**
- Create: `terraform/main.tf`
- Create: `terraform/variables.tf`
- Create: `terraform/outputs.tf`
- Create: `terraform/storage.tf`
- Create: `terraform/service_accounts.tf`
- Create: `terraform/artifact_registry.tf`

- [ ] **Step 1: Create terraform/main.tf**

```hcl
terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {
    bucket = "quant-terraform-state"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
```

- [ ] **Step 2: Create terraform/variables.tf**

```hcl
variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}
```

- [ ] **Step 3: Create terraform/outputs.tf**

```hcl
output "gcs_bucket_name" {
  value = google_storage_bucket.quant_data.name
}

output "collector_service_account_email" {
  value = google_service_account.collector.email
}

output "query_api_service_account_email" {
  value = google_service_account.query_api.email
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.docker.repository_id
}
```

- [ ] **Step 4: Create terraform/storage.tf**

```hcl
resource "google_storage_bucket" "quant_data" {
  name                        = "${var.project_id}-quant-data"
  location                    = var.region
  force_destroy               = var.environment != "prod"
  uniform_bucket_level_access = true

  lifecycle_rule {
    condition { age = 90 }
    action { type = "SetStorageClass", storage_class = "NEARLINE" }
  }
}
```

- [ ] **Step 5: Create terraform/service_accounts.tf**

```hcl
resource "google_service_account" "collector" {
  account_id   = "quant-collector"
  display_name = "Quant Data Collector"
}

resource "google_storage_bucket_iam_member" "collector_write" {
  bucket = google_storage_bucket.quant_data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.collector.email}"
}

resource "google_service_account" "query_api" {
  account_id   = "quant-query-api"
  display_name = "Quant Query API"
}

resource "google_storage_bucket_iam_member" "query_api_read" {
  bucket = google_storage_bucket.quant_data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.query_api.email}"
}

resource "google_service_account" "quality" {
  account_id   = "quant-quality"
  display_name = "Quant Data Quality Checker"
}

resource "google_storage_bucket_iam_member" "quality_read" {
  bucket = google_storage_bucket.quant_data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.quality.email}"
}
```

- [ ] **Step 6: Create terraform/artifact_registry.tf**

```hcl
resource "google_artifact_registry_repository" "docker" {
  repository_id = "quant"
  format        = "DOCKER"
  location      = var.region
}
```

- [ ] **Step 7: Commit**

```bash
git add terraform/
git commit -m "feat: add terraform config for GCS, IAM, and artifact registry"
```

---

### Task 3: Python Data Schema & Market Adapter Protocol

**Files:**
- Create: `collectors/schema.py`
- Create: `collectors/adapters/base.py`
- Create: `collectors/tests/test_schema.py`
- Create: `collectors/tests/conftest.py`

- [ ] **Step 1: Write failing tests for schema**

```python
# collectors/tests/test_schema.py
from datetime import datetime, timezone
from collectors.schema import Bar


def test_bar_creation():
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
        open=189.50,
        high=190.20,
        low=189.30,
        close=189.80,
        volume=1000000,
        market="US",
        frequency="1m",
    )
    assert bar.symbol == "AAPL"
    assert bar.close == 189.80
    assert bar.market == "US"


def test_bar_to_parquet_roundtrip(tmp_path):
    import pandas as pd

    bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 5, 13, 10, i, tzinfo=timezone.utc),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000 * i,
            market="US",
            frequency="1m",
        )
        for i in range(5)
    ]
    df = pd.DataFrame([b.__dict__ for b in bars])
    path = tmp_path / "test.parquet"
    df.to_parquet(path, index=False)

    loaded = pd.read_parquet(path)
    assert len(loaded) == 5
    assert list(loaded.columns) == [
        "symbol", "timestamp", "open", "high", "low", "close", "volume", "market", "frequency"
    ]
```

- [ ] **Step 2: Write failing test for MarketAdapter protocol**

```python
# collectors/tests/test_adapter_protocol.py
from datetime import date
from collectors.adapters.base import MarketAdapter


class FakeAdapter:
    market = "US"

    def fetch_bars(self, symbols, start, end, frequency="1m"):
        pass


def test_adapter_is_protocol():
    adapter = FakeAdapter()
    assert isinstance(adapter, MarketAdapter)


def test_adapter_protocol_requires_market():
    from typing import get_type_hints
    hints = get_type_hints(MarketAdapter)
    assert "market" in MarketAdapter.__annotations__
```

- [ ] **Step 3: Implement schema.py**

```python
# collectors/schema.py
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    market: str
    frequency: str


@dataclass
class Quote:
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    market: str


@dataclass
class Trade:
    symbol: str
    timestamp: datetime
    price: float
    size: int
    side: str
    market: str
```

- [ ] **Step 4: Implement base.py**

```python
# collectors/adapters/base.py
from datetime import date, datetime, time
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class MarketAdapter(Protocol):
    market: str

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame: ...

    def fetch_supported_symbols(self) -> list[str]: ...

    def market_hours(self, d: date) -> tuple[time, time]: ...
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd collectors && python -m pytest tests/test_schema.py tests/test_adapter_protocol.py -v
```

Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add collectors/schema.py collectors/adapters/base.py collectors/tests/
git commit -m "feat: define Bar/Quote/Trade schema and MarketAdapter protocol"
```

---

### Task 4: YFinance Adapter

**Files:**
- Create: `collectors/adapters/yfinance_adapter.py`
- Create: `collectors/tests/test_yfinance_adapter.py`

- [ ] **Step 1: Write failing test with VCR cassette**

```python
# collectors/tests/test_yfinance_adapter.py
from datetime import datetime, timezone
import pandas as pd
import pytest


@pytest.mark.vcr
def test_yfinance_fetch_bars_returns_dataframe():
    from collectors.adapters.yfinance_adapter import YFinanceUSAdapter

    adapter = YFinanceUSAdapter()
    start = datetime(2026, 5, 11, tzinfo=timezone.utc)
    end = datetime(2026, 5, 13, tzinfo=timezone.utc)

    df = adapter.fetch_bars(["AAPL"], start, end, frequency="1m")

    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert "symbol" in df.columns
    assert "close" in df.columns
    assert "market" in df.columns
    assert all(df["market"] == "US")
    assert df["timestamp"].dtype.kind == "M"


def test_yfinance_symbols_returns_list():
    from collectors.adapters.yfinance_adapter import YFinanceUSAdapter

    adapter = YFinanceUSAdapter()
    symbols = adapter.fetch_supported_symbols()

    assert isinstance(symbols, list)
    assert "AAPL" in symbols


def test_yfinance_market_hours_returns_tuple():
    from datetime import date
    from collectors.adapters.yfinance_adapter import YFinanceUSAdapter

    adapter = YFinanceUSAdapter()
    open_time, close_time = adapter.market_hours(date(2026, 5, 13))

    assert open_time.hour == 9
    assert open_time.minute == 30
    assert close_time.hour == 16
```

- [ ] **Step 2: Implement YFinance adapter**

```python
# collectors/adapters/yfinance_adapter.py
from datetime import date, datetime, time, timezone, timedelta

import pandas as pd
import yfinance as yf


class YFinanceUSAdapter:
    market = "US"

    _SP500_SYMBOLS = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
        "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC",
        "PYPL", "ADBE", "CRM", "NFLX", "INTC", "CSCO", "VZ", "PFE", "MRK",
        "ABT", "KO", "PEP", "TMO", "NKE", "ORCL", "ABBV", "ACN", "AVGO",
        "COST", "CVX", "MCD", "WFC", "TXN", "QCOM", "AMD", "AMGN", "HON",
        "INTU", "IBM", "PM", "MS", "LOW", "CAT", "SPY",
    ]

    def __init__(self):
        pass

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame:
        valid_intervals = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d"}
        yf_interval = valid_intervals.get(frequency, "1m")

        tickers = yf.Tickers(" ".join(symbols))
        df = tickers.history(start=start, end=end, interval=yf_interval)

        if df.empty:
            return pd.DataFrame(columns=[
                "symbol", "timestamp", "open", "high", "low", "close", "volume", "market", "frequency"
            ])

        records = []
        for symbol in symbols:
            if symbol not in df.columns.get_level_values(1):
                continue
            sym_df = df.xs(symbol, level=1, axis=1).dropna(subset=["Open"])
            for ts, row in sym_df.iterrows():
                records.append({
                    "symbol": symbol,
                    "timestamp": ts.tz_convert("UTC"),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                    "market": self.market,
                    "frequency": frequency,
                })

        return pd.DataFrame(records)

    def fetch_supported_symbols(self) -> list[str]:
        return list(self._SP500_SYMBOLS)

    def market_hours(self, d: date) -> tuple[time, time]:
        return time(9, 30), time(16, 0)
```

- [ ] **Step 3: Run tests (offline mode first)**

```bash
cd collectors && python -m pytest tests/test_yfinance_adapter.py::test_yfinance_symbols_returns_list tests/test_yfinance_adapter.py::test_yfinance_market_hours_returns_tuple -v
```

Expected: 2 tests PASS

- [ ] **Step 4: Run VCR test (record real API call, then replay)**

```bash
cd collectors && python -m pytest tests/test_yfinance_adapter.py::test_yfinance_fetch_bars_returns_dataframe -v --vcr-record=once
```

Expected: PASS (records cassette on first run, replays on subsequent)

- [ ] **Step 5: Commit**

```bash
git add collectors/adapters/yfinance_adapter.py collectors/tests/test_yfinance_adapter.py
git commit -m "feat: implement YFinance US stock adapter"
```

---

### Task 5: Alpaca Adapter

**Files:**
- Create: `collectors/adapters/alpaca_adapter.py`
- Create: `collectors/tests/test_alpaca_adapter.py`

- [ ] **Step 1: Write failing test**

```python
# collectors/tests/test_alpaca_adapter.py
from datetime import datetime, timezone
import os
import pandas as pd
import pytest


@pytest.mark.vcr
def test_alpaca_fetch_bars_returns_dataframe():
    from collectors.adapters.alpaca_adapter import AlpacaUSAdapter

    api_key = os.environ.get("ALPACA_API_KEY", "test-key")
    api_secret = os.environ.get("ALPACA_API_SECRET", "test-secret")
    adapter = AlpacaUSAdapter(api_key=api_key, api_secret=api_secret)

    start = datetime(2026, 5, 11, tzinfo=timezone.utc)
    end = datetime(2026, 5, 13, tzinfo=timezone.utc)

    df = adapter.fetch_bars(["AAPL", "MSFT"], start, end, frequency="1m")

    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert set(df["symbol"].unique()) == {"AAPL", "MSFT"}
    assert all(df["market"] == "US")


def test_alpaca_symbols():
    from collectors.adapters.alpaca_adapter import AlpacaUSAdapter

    adapter = AlpacaUSAdapter(api_key="test", api_secret="test")
    symbols = adapter.fetch_supported_symbols()

    assert isinstance(symbols, list)
    assert len(symbols) > 0


def test_alpaca_adapter_implements_protocol():
    from collectors.adapters.alpaca_adapter import AlpacaUSAdapter
    from collectors.adapters.base import MarketAdapter

    adapter = AlpacaUSAdapter(api_key="test", api_secret="test")
    assert isinstance(adapter, MarketAdapter)
    assert adapter.market == "US"
```

- [ ] **Step 2: Implement Alpaca adapter**

```python
# collectors/adapters/alpaca_adapter.py
from datetime import date, datetime, time, timedelta

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit


class AlpacaUSAdapter:
    market = "US"

    def __init__(self, api_key: str, api_secret: str):
        self._client = StockHistoricalDataClient(api_key, api_secret)

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1m",
    ) -> pd.DataFrame:
        freq_map = {
            "1m": TimeFrame(1, TimeFrameUnit.Minute),
            "5m": TimeFrame(5, TimeFrameUnit.Minute),
            "15m": TimeFrame(15, TimeFrameUnit.Minute),
            "1h": TimeFrame(1, TimeFrameUnit.Hour),
            "1d": TimeFrame(1, TimeFrameUnit.Day),
        }
        tf = freq_map.get(frequency, freq_map["1m"])

        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=tf,
            start=start,
            end=end,
        )
        response = self._client.get_stock_bars(request)

        records = []
        for symbol, bars in response.data.items():
            for bar in bars:
                records.append({
                    "symbol": symbol,
                    "timestamp": bar.timestamp.replace(tzinfo=None),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "market": self.market,
                    "frequency": frequency,
                })

        return pd.DataFrame(records)

    def fetch_supported_symbols(self) -> list[str]:
        from alpaca.data.requests import StockLatestBarRequest
        response = self._client.get_stock_latest_bar(
            StockLatestBarRequest(symbol_or_symbols=[])
        )
        return sorted(response.data.keys())

    def market_hours(self, d: date) -> tuple[time, time]:
        return time(9, 30), time(16, 0)
```

- [ ] **Step 3: Run tests**

```bash
cd collectors && python -m pytest tests/test_alpaca_adapter.py -v -k "not vcr"
```

Expected: 2 tests PASS (protocol check + symbols returns list)

- [ ] **Step 4: Commit**

```bash
git add collectors/adapters/alpaca_adapter.py collectors/tests/test_alpaca_adapter.py
git commit -m "feat: implement Alpaca US stock adapter"
```

---

### Task 6: GCS Storage Helper & Collector Entrypoint

**Files:**
- Create: `collectors/storage.py`
- Create: `collectors/main.py`
- Create: `collectors/tests/test_storage.py`

- [ ] **Step 1: Write failing test for storage**

```python
# collectors/tests/test_storage.py
import os
from datetime import datetime, timezone
import pandas as pd
import pytest
from collectors.schema import Bar
from collectors.storage import build_gcs_path, dataframe_to_parquet_bytes


def test_build_gcs_path():
    ts = datetime(2026, 5, 13, 10, 30, tzinfo=timezone.utc)
    path = build_gcs_path(market="us", data_type="bars", symbol="AAPL", timestamp=ts)
    assert path == "raw/us/bars/2026/05/13/AAPL.parquet"


def test_build_gcs_path_cn_market():
    ts = datetime(2026, 5, 13, 10, 30, tzinfo=timezone.utc)
    path = build_gcs_path(market="cn", data_type="bars", symbol="000001", timestamp=ts)
    assert path == "raw/cn/bars/2026/05/13/000001.parquet"


def test_dataframe_to_parquet_bytes():
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
        open=100.0, high=101.0, low=99.0, close=100.5,
        volume=1000, market="US", frequency="1m",
    )
    df = pd.DataFrame([bar.__dict__])
    buf = dataframe_to_parquet_bytes(df)
    assert isinstance(buf, bytes)
    assert len(buf) > 0

    # Verify roundtrip
    import io
    loaded = pd.read_parquet(io.BytesIO(buf))
    assert loaded.iloc[0]["symbol"] == "AAPL"
```

- [ ] **Step 2: Implement storage.py**

```python
# collectors/storage.py
import io
from datetime import datetime

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def build_gcs_path(market: str, data_type: str, symbol: str, timestamp: datetime) -> str:
    """Build GCS object path: raw/{market}/{data_type}/{YYYY}/{MM}/{DD}/{SYMBOL}.parquet"""
    return (
        f"raw/{market.lower()}/{data_type}/"
        f"{timestamp.year:04d}/{timestamp.month:02d}/{timestamp.day:02d}/"
        f"{symbol}.parquet"
    )


def dataframe_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Convert a DataFrame to Parquet bytes with Snappy compression."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def write_bars_to_gcs(
    df: pd.DataFrame,
    bucket_name: str,
    market: str = "us",
) -> list[str]:
    """Write bars DataFrame to GCS, one file per symbol-date combination. Returns list of GCS paths."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    paths = []

    groups = df.groupby(["symbol", df["timestamp"].dt.date])
    for (symbol, _date), group in groups:
        ts = group["timestamp"].iloc[0]
        path = build_gcs_path(market, "bars", symbol, ts)
        blob = bucket.blob(path)
        blob.upload_from_string(
            dataframe_to_parquet_bytes(group),
            content_type="application/octet-stream",
        )
        paths.append(f"gs://{bucket_name}/{path}")

    return paths
```

- [ ] **Step 3: Implement main.py (collector entrypoint)**

```python
# collectors/main.py
"""Cloud Run Job entrypoint for data collection.

Triggered by Cloud Scheduler. Reads COLLECTOR_SOURCE env var to
pick which adapter to use (yfinance or alpaca). Fetches minute
bars for configured symbols and writes to GCS.

Env vars:
    GCS_BUCKET: GCS bucket name (required)
    COLLECTOR_SOURCE: "yfinance" (default) or "alpaca"
    ALPACA_API_KEY, ALPACA_API_SECRET: required if source=alpaca
    SYMBOLS: comma-separated symbols (default: SPY,AAPL,MSFT)
    FREQUENCY: bar frequency (default: 1m)
    LOOKBACK_MINUTES: minutes to look back (default: 60)
"""

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from adapters.alpaca_adapter import AlpacaUSAdapter
from adapters.yfinance_adapter import YFinanceUSAdapter
from storage import write_bars_to_gcs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_adapter(source: str):
    if source == "alpaca":
        api_key = os.environ["ALPACA_API_KEY"]
        api_secret = os.environ["ALPACA_API_SECRET"]
        return AlpacaUSAdapter(api_key=api_key, api_secret=api_secret)
    return YFinanceUSAdapter()


def main():
    bucket = os.environ["GCS_BUCKET"]
    source = os.environ.get("COLLECTOR_SOURCE", "yfinance")
    symbols_str = os.environ.get("SYMBOLS", "SPY,AAPL,MSFT")
    frequency = os.environ.get("FREQUENCY", "1m")
    lookback = int(os.environ.get("LOOKBACK_MINUTES", "60"))

    symbols = [s.strip() for s in symbols_str.split(",")]
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback)

    logger.info("Starting collection: source=%s symbols=%s range=%s..%s",
                source, symbols, start.isoformat(), end.isoformat())

    try:
        adapter = get_adapter(source)
        df = adapter.fetch_bars(symbols, start, end, frequency=frequency)

        if df.empty:
            logger.warning("No data returned for symbols=%s", symbols)
            return

        paths = write_bars_to_gcs(df, bucket, market=adapter.market.lower())
        logger.info("Wrote %d rows to %d GCS paths", len(df), len(paths))
    except Exception:
        logger.exception("Collection failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run storage tests**

```bash
cd collectors && python -m pytest tests/test_storage.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add collectors/storage.py collectors/main.py collectors/tests/test_storage.py
git commit -m "feat: add GCS storage helper and collector main entrypoint"
```

---

### Task 7: Collector Docker Image & Terraform Job

**Files:**
- Create: `collectors/requirements.txt`
- Create: `collectors/Dockerfile`
- Create: `terraform/cloud_run_jobs.tf`
- Create: `terraform/scheduler.tf`

- [ ] **Step 1: Create collectors/requirements.txt**

```
pandas>=2.2
pyarrow>=17.0
google-cloud-storage>=2.18
yfinance>=0.2.50
alpaca-py>=0.34.0
```

- [ ] **Step 2: Create collectors/Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY schema.py .
COPY storage.py .
COPY main.py .
COPY adapters/ adapters/

ENTRYPOINT ["python", "main.py"]
```

- [ ] **Step 3: Create terraform/cloud_run_jobs.tf**

```hcl
resource "google_cloud_run_v2_job" "collector_yfinance" {
  name     = "quant-collector-yfinance"
  location = var.region

  template {
    template {
      service_account = google_service_account.collector.email
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/collector:latest"
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.quant_data.name
        }
        env {
          name  = "COLLECTOR_SOURCE"
          value = "yfinance"
        }
        env {
          name  = "SYMBOLS"
          value = "SPY,AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA"
        }
        env {
          name  = "FREQUENCY"
          value = "1m"
        }
        env {
          name  = "LOOKBACK_MINUTES"
          value = "120"
        }
        resources {
          limits = {
            memory = "512Mi"
            cpu    = "1"
          }
        }
      }
      max_retries = 3
      timeout     = "600s"
    }
  }
}
```

- [ ] **Step 4: Create terraform/scheduler.tf**

```hcl
resource "google_cloud_scheduler_job" "collect_minute_bars" {
  name             = "quant-collect-minute-bars"
  schedule         = "*/5 * * * 1-5"
  time_zone        = "America/New_York"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_yfinance.name}:run"

    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add collectors/requirements.txt collectors/Dockerfile terraform/cloud_run_jobs.tf terraform/scheduler.tf
git commit -m "feat: add collector Dockerfile, Cloud Run Job and Scheduler terraform"
```

---

### Task 8: Go Query API — Module Init & Market Types

**Files:**
- Create: `query-api/go.mod`
- Create: `query-api/internal/market/market.go`
- Create: `query-api/internal/market/market_test.go`

- [ ] **Step 1: Initialize Go module**

```bash
cd query-api && go mod init github.com/quant/query-api
```

- [ ] **Step 2: Write failing test for market types**

```go
// query-api/internal/market/market_test.go
package market

import (
    "testing"
    "time"
)

func TestBarStruct(t *testing.T) {
    bar := Bar{
        Symbol:    "AAPL",
        Timestamp: time.Date(2026, 5, 13, 10, 0, 0, 0, time.UTC),
        Open:      189.50,
        High:      190.20,
        Low:       189.30,
        Close:     189.80,
        Volume:    1000000,
        Market:    "US",
        Frequency: "1m",
    }
    if bar.Symbol != "AAPL" {
        t.Errorf("expected AAPL, got %s", bar.Symbol)
    }
    if bar.Close != 189.80 {
        t.Errorf("expected 189.80, got %f", bar.Close)
    }
}

func TestParseMarket(t *testing.T) {
    tests := []struct {
        input    string
        expected Market
        ok       bool
    }{
        {"us", US, true},
        {"US", US, true},
        {"cn", CN, true},
        {"hk", HK, true},
        {"jp", "", false},
        {"", "", false},
    }
    for _, tc := range tests {
        m, ok := ParseMarket(tc.input)
        if ok != tc.ok || (tc.ok && m != tc.expected) {
            t.Errorf("ParseMarket(%q) = (%s, %v); want (%s, %v)", tc.input, m, ok, tc.expected, tc.ok)
        }
    }
}

func TestMarketStoragePrefix(t *testing.T) {
    tests := []struct {
        market   Market
        dataType string
        expected string
    }{
        {US, "bars", "raw/us/bars"},
        {CN, "bars", "raw/cn/bars"},
        {HK, "quotes", "raw/hk/quotes"},
    }
    for _, tc := range tests {
        got := tc.market.StoragePrefix(tc.dataType)
        if got != tc.expected {
            t.Errorf("StoragePrefix(%q) = %q; want %q", tc.dataType, got, tc.expected)
        }
    }
}
```

- [ ] **Step 3: Implement market.go**

```go
// query-api/internal/market/market.go
package market

import (
    "fmt"
    "strings"
    "time"
)

type Market string

const (
    US Market = "US"
    CN Market = "CN"
    HK Market = "HK"
)

func ParseMarket(s string) (Market, bool) {
    switch strings.ToUpper(s) {
    case "US":
        return US, true
    case "CN":
        return CN, true
    case "HK":
        return HK, true
    default:
        return "", false
    }
}

func (m Market) StoragePrefix(dataType string) string {
    return fmt.Sprintf("raw/%s/%s", strings.ToLower(string(m)), dataType)
}

type Bar struct {
    Symbol    string    `json:"symbol" parquet:"symbol"`
    Timestamp time.Time `json:"timestamp" parquet:"timestamp"`
    Open      float64   `json:"open" parquet:"open"`
    High      float64   `json:"high" parquet:"high"`
    Low       float64   `json:"low" parquet:"low"`
    Close     float64   `json:"close" parquet:"close"`
    Volume    int64     `json:"volume" parquet:"volume"`
    Market    string    `json:"market" parquet:"market"`
    Frequency string    `json:"frequency" parquet:"frequency"`
}
```

- [ ] **Step 4: Run Go tests**

```bash
cd query-api && go test ./internal/market/ -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add query-api/go.mod query-api/internal/market/
git commit -m "feat: init Go module and market types"
```

---

### Task 9: Go Query API — Parquet Reader

**Files:**
- Create: `query-api/internal/reader/reader.go`
- Create: `query-api/internal/reader/reader_test.go`

- [ ] **Step 1: Write failing test for reader**

```go
// query-api/internal/reader/reader_test.go
package reader

import (
    "context"
    "os"
    "testing"
    "time"
)

func TestBuildGCSPrefix(t *testing.T) {
    tests := []struct {
        market   string
        dataType string
        date     time.Time
        expected string
    }{
        {"us", "bars", time.Date(2026, 5, 13, 0, 0, 0, 0, time.UTC), "raw/us/bars/2026/05/13/"},
        {"cn", "bars", time.Date(2026, 1, 5, 0, 0, 0, 0, time.UTC), "raw/cn/bars/2026/01/05/"},
    }
    for _, tc := range tests {
        got := buildGCSPrefix(tc.market, tc.dataType, tc.date)
        if got != tc.expected {
            t.Errorf("buildGCSPrefix(%q, %q, %v) = %q; want %q",
                tc.market, tc.dataType, tc.date, got, tc.expected)
        }
    }
}

func TestParseParams(t *testing.T) {
    params, err := ParseQueryParams(
        "us", "AAPL,MSFT",
        "2026-05-01T00:00:00Z", "2026-05-13T23:59:59Z",
        "1m",
    )
    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }
    if params.Market != "us" {
        t.Errorf("expected market us, got %s", params.Market)
    }
    if len(params.Symbols) != 2 {
        t.Errorf("expected 2 symbols, got %d", len(params.Symbols))
    }
    if params.Frequency != "1m" {
        t.Errorf("expected frequency 1m, got %s", params.Frequency)
    }
}

func TestParseParamsInvalidMarket(t *testing.T) {
    _, err := ParseQueryParams(
        "jp", "AAPL",
        "2026-05-01T00:00:00Z", "2026-05-13T23:59:59Z",
        "1m",
    )
    if err == nil {
        t.Error("expected error for invalid market")
    }
}
```

- [ ] **Step 2: Implement reader.go**

```go
// query-api/internal/reader/reader.go
package reader

import (
    "context"
    "fmt"
    "io"
    "sort"
    "strings"
    "time"

    "cloud.google.com/go/storage"

    "github.com/quant/query-api/internal/market"
)

type QueryParams struct {
    Market    string
    Symbols   []string
    Start     time.Time
    End       time.Time
    Frequency string
}

func ParseQueryParams(mkt, symbols, startStr, endStr, freq string) (QueryParams, error) {
    if _, ok := market.ParseMarket(mkt); !ok {
        return QueryParams{}, fmt.Errorf("invalid market: %s", mkt)
    }
    start, err := time.Parse(time.RFC3339, startStr)
    if err != nil {
        return QueryParams{}, fmt.Errorf("invalid start time: %w", err)
    }
    end, err := time.Parse(time.RFC3339, endStr)
    if err != nil {
        return QueryParams{}, fmt.Errorf("invalid end time: %w", err)
    }
    if freq == "" {
        freq = "1m"
    }
    return QueryParams{
        Market:    strings.ToLower(mkt),
        Symbols:   splitTrim(symbols),
        Start:     start,
        End:       end,
        Frequency: freq,
    }, nil
}

func buildGCSPrefix(mkt, dataType string, date time.Time) string {
    return fmt.Sprintf("raw/%s/%s/%04d/%02d/%02d/",
        strings.ToLower(mkt), dataType,
        date.Year(), date.Month(), date.Day())
}

func splitTrim(s string) []string {
    parts := strings.Split(s, ",")
    result := make([]string, 0, len(parts))
    for _, p := range parts {
        trimmed := strings.TrimSpace(p)
        if trimmed != "" {
            result = append(result, trimmed)
        }
    }
    return result
}

type BarRow struct {
    Symbol    string    `json:"symbol"`
    Timestamp time.Time `json:"timestamp"`
    Open      float64   `json:"open"`
    High      float64   `json:"high"`
    Low       float64   `json:"low"`
    Close     float64   `json:"close"`
    Volume    int64     `json:"volume"`
    Market    string    `json:"market"`
    Frequency string    `json:"frequency"`
}

type QueryResult struct {
    Bars   []BarRow `json:"bars"`
    Status string   `json:"status"`
}
```

- [ ] **Step 3: Run tests**

```bash
cd query-api && go test ./internal/reader/ -v
```

Expected: 3 tests PASS (note: buildGCSPrefix and ParseQueryParams are package-level, so tests can call them)

- [ ] **Step 4: Commit**

```bash
git add query-api/internal/reader/
git commit -m "feat: implement query parameter parsing and GCS prefix builder"
```

---

### Task 10: Go Query API — HTTP Handler & Server

**Files:**
- Create: `query-api/internal/handler/handler.go`
- Create: `query-api/cmd/server/main.go`
- Create: `query-api/internal/handler/handler_test.go`

- [ ] **Step 1: Write failing handler test**

```go
// query-api/internal/handler/handler_test.go
package handler

import (
    "encoding/json"
    "net/http"
    "net/http/httptest"
    "testing"
)

func TestHealthEndpoint(t *testing.T) {
    h := NewHandler("test-bucket")
    req := httptest.NewRequest("GET", "/health", nil)
    w := httptest.NewRecorder()

    h.ServeHTTP(w, req)

    if w.Code != http.StatusOK {
        t.Errorf("expected 200, got %d", w.Code)
    }
    var body map[string]string
    json.NewDecoder(w.Body).Decode(&body)
    if body["status"] != "ok" {
        t.Errorf("expected status ok, got %s", body["status"])
    }
}

func TestBarsEndpointMissingParams(t *testing.T) {
    h := NewHandler("test-bucket")
    req := httptest.NewRequest("GET", "/api/v1/bars", nil)
    w := httptest.NewRecorder()

    h.ServeHTTP(w, req)

    if w.Code != http.StatusBadRequest {
        t.Errorf("expected 400, got %d", w.Code)
    }
}

func TestBarsEndpointInvalidMarket(t *testing.T) {
    h := NewHandler("test-bucket")
    req := httptest.NewRequest("GET", "/api/v1/bars?market=jp&symbols=AAPL&start=2026-05-01T00:00:00Z&end=2026-05-13T23:59:59Z", nil)
    w := httptest.NewRecorder()

    h.ServeHTTP(w, req)

    if w.Code != http.StatusBadRequest {
        t.Errorf("expected 400 for invalid market, got %d", w.Code)
    }
}

func TestSymbolsEndpoint(t *testing.T) {
    h := NewHandler("test-bucket")
    req := httptest.NewRequest("GET", "/api/v1/symbols?market=us", nil)
    w := httptest.NewRecorder()

    h.ServeHTTP(w, req)
    // Without real GCS data, symbols endpoint should still return 200 with empty array
    if w.Code != http.StatusOK {
        t.Errorf("expected 200, got %d", w.Code)
    }
}
```

- [ ] **Step 2: Implement handler.go**

```go
// query-api/internal/handler/handler.go
package handler

import (
    "encoding/json"
    "log"
    "net/http"
    "strings"

    "github.com/quant/query-api/internal/reader"
)

type Handler struct {
    bucket string
    mux    *http.ServeMux
}

func NewHandler(bucket string) *Handler {
    h := &Handler{bucket: bucket, mux: http.NewServeMux()}
    h.mux.HandleFunc("/health", h.handleHealth)
    h.mux.HandleFunc("/api/v1/bars", h.handleBars)
    h.mux.HandleFunc("/api/v1/symbols", h.handleSymbols)
    return h
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    h.mux.ServeHTTP(w, r)
}

func (h *Handler) handleHealth(w http.ResponseWriter, r *http.Request) {
    writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (h *Handler) handleBars(w http.ResponseWriter, r *http.Request) {
    q := r.URL.Query()
    params, err := reader.ParseQueryParams(
        q.Get("market"),
        q.Get("symbols"),
        q.Get("start"),
        q.Get("end"),
        q.Get("frequency"),
    )
    if err != nil {
        writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
        return
    }

    log.Printf("Query bars: market=%s symbols=%v start=%s end=%s freq=%s",
        params.Market, params.Symbols, params.Start, params.End, params.Frequency)

    // In Phase 1, return empty result — real GCS reading comes after
    // collecting actual data. This lets us deploy and test the API shape.
    result := reader.QueryResult{
        Bars:   []reader.BarRow{},
        Status: "ok",
    }
    writeJSON(w, http.StatusOK, result)
}

func (h *Handler) handleSymbols(w http.ResponseWriter, r *http.Request) {
    market := strings.ToLower(r.URL.Query().Get("market"))
    if market == "" {
        writeJSON(w, http.StatusBadRequest, map[string]string{"error": "market is required"})
        return
    }
    // Phase 1 deferred: scan GCS listing to build symbol index.
    // For now returns empty — symbols populate once collectors run.
    writeJSON(w, http.StatusOK, map[string]interface{}{
        "symbols": []string{},
        "market":  market,
    })
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    json.NewEncoder(w).Encode(v)
}
```

- [ ] **Step 3: Implement server main.go**

```go
// query-api/cmd/server/main.go
package main

import (
    "fmt"
    "log"
    "net/http"
    "os"

    "github.com/quant/query-api/internal/handler"
)

func main() {
    port := os.Getenv("PORT")
    if port == "" {
        port = "8080"
    }
    bucket := os.Getenv("GCS_BUCKET")
    if bucket == "" {
        log.Fatal("GCS_BUCKET environment variable is required")
    }

    h := handler.NewHandler(bucket)
    addr := fmt.Sprintf(":%s", port)
    log.Printf("Query API listening on %s", addr)
    log.Fatal(http.ListenAndServe(addr, h))
}
```

- [ ] **Step 4: Run tests**

```bash
cd query-api && go test ./internal/handler/ -v
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add query-api/cmd/server/main.go query-api/internal/handler/
git commit -m "feat: implement Go query API HTTP handler and server"
```

---

### Task 11: Go Query API — Docker Image & Terraform

**Files:**
- Create: `query-api/Dockerfile`
- Create: `terraform/cloud_run_api.tf`

- [ ] **Step 1: Create query-api/Dockerfile**

```dockerfile
FROM golang:1.23-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /server ./cmd/server

FROM gcr.io/distroless/static-debian12
COPY --from=builder /server /server
ENV PORT=8080
EXPOSE 8080
ENTRYPOINT ["/server"]
```

- [ ] **Step 2: Create terraform/cloud_run_api.tf**

```hcl
resource "google_cloud_run_v2_service" "query_api" {
  name     = "quant-query-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.query_api.email
    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/query-api:latest"
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.quant_data.name
      }
      resources {
        limits = {
          memory = "256Mi"
          cpu    = "1"
        }
      }
    }
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "query_api_public" {
  name     = google_cloud_run_v2_service.query_api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
```

- [ ] **Step 3: Commit**

```bash
git add query-api/Dockerfile terraform/cloud_run_api.tf
git commit -m "feat: add Go query API Dockerfile and Cloud Run service terraform"
```

---

### Task 12: Python Research SDK

**Files:**
- Create: `sdk/pyproject.toml`
- Create: `sdk/quant/__init__.py`
- Create: `sdk/quant/client.py`
- Create: `sdk/quant/direct.py`
- Create: `sdk/tests/test_client.py`
- Create: `sdk/tests/test_direct.py`

- [ ] **Step 1: Write failing test for SDK client**

```python
# sdk/tests/test_client.py
from unittest.mock import patch, Mock
import pandas as pd
import pytest
from quant.client import QuantClient


def test_client_bars_returns_dataframe():
    mock_resp = {
        "bars": [
            {"symbol": "AAPL", "timestamp": "2026-05-13T10:00:00Z",
             "open": 189.5, "high": 190.2, "low": 189.3, "close": 189.8,
             "volume": 1000000, "market": "us", "frequency": "1m"},
        ],
        "status": "ok",
    }

    with patch("quant.client.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_resp
        mock_get.return_value.raise_for_status = Mock()

        client = QuantClient(base_url="http://test:8080")
        df = client.bars("AAPL", "2026-05-01", "2026-05-13", market="us")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "AAPL"
    assert df.iloc[0]["close"] == 189.8


def test_client_raises_on_bad_response():
    with patch("quant.client.requests.get") as mock_get:
        mock_get.return_value.raise_for_status.side_effect = Exception("500")

        client = QuantClient(base_url="http://test:8080")
        with pytest.raises(Exception):
            client.bars("AAPL", "2026-05-01", "2026-05-13")
```

- [ ] **Step 2: Write failing test for direct reader**

```python
# sdk/tests/test_direct.py
from datetime import datetime, timezone
import pandas as pd
import pytest
from quant.direct import bars_direct


def test_bars_direct_reads_parquet_from_gcs(tmp_path):
    """Test direct GCS reader with local Parquet files."""
    import pyarrow.parquet as pq
    import pyarrow as pa

    # Create a Parquet file mimicking GCS storage layout
    data_dir = tmp_path / "raw/us/bars/2026/05/13"
    data_dir.mkdir(parents=True)
    table = pa.table({
        "symbol": ["AAPL"] * 3,
        "timestamp": [
            datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 13, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 13, 10, 2, tzinfo=timezone.utc),
        ],
        "open": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "close": [100.5, 101.5, 102.5],
        "volume": [1000, 1100, 1200],
        "market": ["US"] * 3,
        "frequency": ["1m"] * 3,
    })
    pq.write_table(table, data_dir / "AAPL.parquet")

    # Read using local filesystem via file:// prefix
    df = bars_direct(
        "AAPL", "2026-05-13", "2026-05-13",
        market="us", base_path=str(tmp_path),
    )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert list(df["symbol"]) == ["AAPL"] * 3
```

- [ ] **Step 3: Implement client.py**

```python
# sdk/quant/client.py
import requests
import pandas as pd


class QuantClient:
    def __init__(self, base_url: str = "http://localhost:8080"):
        self._base_url = base_url.rstrip("/")

    def bars(
        self,
        symbols: str | list[str],
        start: str,
        end: str,
        market: str = "us",
        frequency: str = "1m",
    ) -> pd.DataFrame:
        if isinstance(symbols, list):
            symbols = ",".join(symbols)

        resp = requests.get(
            f"{self._base_url}/api/v1/bars",
            params={
                "market": market,
                "symbols": symbols,
                "start": start,
                "end": end,
                "frequency": frequency,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("bars"):
            return pd.DataFrame()

        df = pd.DataFrame(data["bars"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.set_index(["symbol", "timestamp"]).sort_index()
```

- [ ] **Step 4: Implement direct.py**

```python
# sdk/quant/direct.py
from pathlib import Path
import pandas as pd


def bars_direct(
    symbols: str | list[str],
    start: str,
    end: str,
    market: str = "us",
    base_path: str | None = None,
) -> pd.DataFrame:
    if isinstance(symbols, str):
        symbols = [symbols]

    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    frames = []
    for symbol in symbols:
        date_range = pd.date_range(start_dt, end_dt, freq="D")
        for d in date_range:
            path = (
                f"{base_path or ''}/raw/{market}/bars/"
                f"{d.year:04d}/{d.month:02d}/{d.day:02d}/{symbol}.parquet"
            )
            try:
                df = pd.read_parquet(path)
                frames.append(df)
            except FileNotFoundError:
                continue

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    return result.set_index(["symbol", "timestamp"]).sort_index()
```

- [ ] **Step 5: Implement __init__.py facade**

```python
# sdk/quant/__init__.py
"""Quantitative trading data SDK.

Provides a clean interface for querying market data from research notebooks.

Usage:
    import quant.data as qd
    df = qd.bars("AAPL", "2026-05-01", "2026-05-13")
"""

from quant.client import QuantClient
from quant.direct import bars_direct

_client: QuantClient | None = None


def _get_client() -> QuantClient:
    global _client
    if _client is None:
        api_url = __import__("os").environ.get("QUANT_API_URL", "http://localhost:8080")
        _client = QuantClient(base_url=api_url)
    return _client


def bars(
    symbols: str | list[str],
    start: str,
    end: str,
    market: str = "us",
    frequency: str = "1m",
    source: str = "api",
):
    """Fetch OHLCV bars.

    Args:
        symbols: Ticker(s), e.g. "AAPL" or ["AAPL", "MSFT"]
        start: Start date/time as ISO string (e.g. "2026-05-01" or "2026-05-01T09:30:00")
        end: End date/time as ISO string
        market: Market code ("us", "cn", "hk")
        frequency: Bar frequency ("1m", "5m", "1h", "1d")
        source: "api" (via Go query API) or "direct" (read from GCS/Local directly)

    Returns:
        pd.DataFrame with columns [open, high, low, close, volume, market, frequency]
        indexed by (symbol, timestamp).
    """
    if source == "direct":
        return bars_direct(symbols, start, end, market=market)

    if isinstance(symbols, list):
        symbols = ",".join(symbols)
    return _get_client().bars(symbols, start, end, market=market, frequency=frequency)
```

- [ ] **Step 6: Create sdk/pyproject.toml**

```toml
[project]
name = "quant-sdk"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pandas>=2.2",
    "pyarrow>=17.0",
    "requests>=2.32",
]
```

- [ ] **Step 7: Run tests**

```bash
cd sdk && python -m pytest tests/ -v
```

Expected: 3 tests PASS

- [ ] **Step 8: Commit**

```bash
git add sdk/
git commit -m "feat: implement Python research SDK with API client and direct GCS reader"
```

---

### Task 13: Data Quality Checks

**Files:**
- Create: `quality/main.py`
- Create: `quality/requirements.txt`
- Create: `quality/Dockerfile`
- Create: `quality/tests/test_main.py`

- [ ] **Step 1: Write failing test for quality checks**

```python
# quality/tests/test_main.py
from datetime import datetime, timezone
import pandas as pd
from quality.main import check_completeness, check_sanity


def test_check_completeness_all_present():
    df = pd.DataFrame({
        "symbol": ["AAPL"] * 390,
        "timestamp": pd.date_range("2026-05-13 14:30", periods=390, freq="1min", tz="UTC"),
        "open": [100.0] * 390,
        "high": [101.0] * 390,
        "low": [99.0] * 390,
        "close": [100.5] * 390,
        "volume": [1000] * 390,
        "market": ["US"] * 390,
        "frequency": ["1m"] * 390,
    })
    issues = check_completeness(df, expected_bars=390)
    assert len(issues) == 0


def test_check_completeness_missing_bars():
    df = pd.DataFrame({
        "symbol": ["AAPL"] * 100,
        "timestamp": pd.date_range("2026-05-13 14:30", periods=100, freq="1min", tz="UTC"),
        "open": [100.0] * 100, "high": [101.0] * 100, "low": [99.0] * 100,
        "close": [100.5] * 100, "volume": [1000] * 100,
        "market": ["US"] * 100, "frequency": ["1m"] * 100,
    })
    issues = check_completeness(df, expected_bars=390)
    assert len(issues) > 0
    assert "expected 390" in issues[0].lower()


def test_check_sanity_high_less_than_low():
    df = pd.DataFrame({
        "symbol": ["AAPL"],
        "timestamp": [datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)],
        "open": [100.0], "high": [90.0],  # high < low
        "low": [95.0], "close": [97.0], "volume": [1000],
        "market": ["US"], "frequency": ["1m"],
    })
    issues = check_sanity(df)
    assert len(issues) > 0
    assert any("high < low" in issue.lower() for issue in issues)


def test_check_sanity_negative_price():
    df = pd.DataFrame({
        "symbol": ["AAPL"],
        "timestamp": [datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)],
        "open": [-5.0], "high": [100.0], "low": [95.0], "close": [97.0],
        "volume": [1000], "market": ["US"], "frequency": ["1m"],
    })
    issues = check_sanity(df)
    assert len(issues) > 0
```

- [ ] **Step 2: Implement quality/main.py**

```python
# quality/main.py
"""Data quality Cloud Function entrypoint.

Triggered by Cloud Scheduler (daily). Reads GCS metadata to find
the latest data, runs quality checks, and logs results to Cloud Logging.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

import pandas as pd
from google.cloud import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def check_completeness(df: pd.DataFrame, expected_bars: int = 390) -> list[str]:
    """Check that each symbol has the expected number of bars per trading day."""
    issues = []
    for symbol, group in df.groupby("symbol"):
        actual = len(group)
        if actual < expected_bars * 0.95:
            issues.append(
                f"Completeness: {symbol} has {actual} bars, expected ~{expected_bars} "
                f"(coverage: {actual / expected_bars:.1%})"
            )
    return issues


def check_freshness(df: pd.DataFrame, max_age_hours: int = 24) -> list[str]:
    """Check that data is not stale."""
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
    """Run sanity checks on price and volume data."""
    issues = []
    for idx, row in df.iterrows():
        if row["high"] < row["low"]:
            issues.append(
                f"Sanity: {row['symbol']} at {row['timestamp']} has high ({row['high']}) < low ({row['low']})"
            )
        for col in ["open", "high", "low", "close"]:
            if row[col] <= 0:
                issues.append(
                    f"Sanity: {row['symbol']} at {row['timestamp']} has {col} = {row[col]}"
                )
    # Volume spike check using rolling std
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
    """Cloud Function entrypoint."""
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
```

- [ ] **Step 3: Create quality/requirements.txt**

```
pandas>=2.2
pyarrow>=17.0
google-cloud-storage>=2.18
```

- [ ] **Step 4: Create quality/Dockerfile**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
ENTRYPOINT ["python", "main.py"]
```

- [ ] **Step 5: Run tests**

```bash
cd quality && python -m pytest tests/ -v
```

Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add quality/ tests/quality/
git commit -m "feat: implement data quality checks"
```

---

### Task 14: BigQuery External Table Setup

**Files:**
- Create: `terraform/bigquery.tf`

- [ ] **Step 1: Create terraform/bigquery.tf**

```hcl
resource "google_bigquery_dataset" "quant" {
  dataset_id = "quant"
  location   = var.region
}

resource "google_bigquery_table" "us_bars" {
  dataset_id = google_bigquery_dataset.quant.dataset_id
  table_id   = "us_bars"

  external_data_configuration {
    autodetect    = true
    source_format = "PARQUET"
    source_uris   = ["gs://${google_storage_bucket.quant_data.name}/raw/us/bars/*/*/*/*.parquet"]

    hive_partitioning_options {
      mode = "AUTO"
      source_uri_prefix = "gs://${google_storage_bucket.quant_data.name}/raw/us/bars/"
    }
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add terraform/bigquery.tf
git commit -m "feat: add BigQuery external table for US bars"
```

---

### Task 15: GitHub Actions CI Pipeline

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create .github/workflows/ci.yml**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  python-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff
      - run: ruff check collectors/ sdk/ quality/
      - run: ruff format --check collectors/ sdk/ quality/

  python-typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install mypy pandas-stubs
      - run: pip install -e sdk/
      - run: mypy collectors/ --ignore-missing-imports

  python-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pytest pytest-cov
      - run: pip install -r collectors/requirements.txt
      - run: pip install -e sdk/
      - run: pip install -r quality/requirements.txt
      - run: python -m pytest collectors/tests/ sdk/tests/ quality/tests/ -v --cov --ignore=tests/vcr_cassettes

  go-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: "1.23"
      - run: cd query-api && go vet ./...
      - run: cd query-api && go test ./... -v -cover

  terraform-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "1.9"
      - run: cd terraform && terraform fmt -check -recursive
      - run: cd terraform && terraform validate
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat: add GitHub Actions CI pipeline"
```

---

### Task 16: Validation Notebook

**Files:**
- Create: `notebooks/01_validate_pipeline.ipynb`

- [ ] **Step 1: Create validation notebook**

The notebook should be created with cells that:

```python
# Cell 1: Imports and config
import pandas as pd
import quant.data as qd

pd.set_option("display.max_columns", None)
print("SDK version:", qd.__version__ if hasattr(qd, "__version__") else "dev")
```

```python
# Cell 2: Query data via SDK (direct GCS mode for Phase 1)
df = qd.bars(
    ["AAPL", "MSFT"],
    start="2026-05-01",
    end="2026-05-13",
    market="us",
    frequency="1m",
    source="direct",
)
print(f"Shape: {df.shape}")
print(f"Columns: {df.columns.tolist()}")
df.head(10)
```

```python
# Cell 3: Basic statistics
df.groupby("symbol").agg({
    "close": ["mean", "std", "min", "max"],
    "volume": ["sum", "mean"],
}).round(2)
```

```python
# Cell 4: Data completeness check
for symbol in df.index.get_level_values("symbol").unique():
    sym_df = df.loc[symbol]
    expected_trading_days = len(pd.bdate_range("2026-05-01", "2026-05-13"))
    actual_days = len(sym_df.groupby(sym_df.index.date))
    print(f"{symbol}: {actual_days}/{expected_trading_days} trading days, {len(sym_df)} total bars")
```

- [ ] **Step 2: Commit**

```bash
git add notebooks/01_validate_pipeline.ipynb
git commit -m "feat: add end-to-end validation notebook"
```
