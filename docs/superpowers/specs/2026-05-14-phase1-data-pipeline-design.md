# Phase 1: Quantitative Trading Data Pipeline — Design Spec

**Date:** 2026-05-14
**Status:** Approved
**Scope:** Phase 1 of multi-phase quant trading system

## Overview

Build the data pipeline foundation for a quantitative trading system. This phase delivers reliable, queryable market data (minute-level OHLCV bars) for US stocks, with architecture designed for multi-market expansion (A-shares, HK stocks) and incremental complexity (intraday → real-time → execution).

**Tech stack:** Python (data collection, research SDK) + Go (query API) on GCP serverless infrastructure.

## Architecture

```
Cloud Scheduler → Cloud Run Jobs (Python collectors) → Cloud Storage (Parquet)
                                                              ↓
                                              Go Query API (Cloud Run) → JSON/Parquet
                                                              ↓
                                              Python SDK → Jupyter notebooks
                                                              ↓
                                              BigQuery (external tables, ad-hoc SQL)
```

### Components

| Component | Language | GCP Service | Responsibility |
|-----------|----------|-------------|----------------|
| Data Collectors | Python | Cloud Run Jobs | Fetch market data via source adapters, write Parquet to GCS |
| Query API | Go | Cloud Run | REST endpoints to query bars by market/symbol/date |
| Research SDK | Python | Library | Clean Python API for notebooks. Reads from Go API or GCS directly. |
| Market Abstraction | Protocol | Shared | Define Bar/Quote/Trade types. Adapter interface. Market enum. |
| BigQuery Views | SQL | BigQuery | External tables over GCS Parquet for ad-hoc analytics |

### Project Structure (monorepo)

```
quant/
├── collectors/          # Python — data ingestion
│   ├── adapters/        # yfinance_adapter.py, alpaca_adapter.py
│   ├── schema.py        # Bar, Quote, Trade types
│   └── main.py          # Cloud Run Job entrypoint
├── query-api/           # Go — data serving
│   ├── cmd/server/      # Entrypoint
│   ├── internal/
│   │   ├── handler/     # HTTP handlers
│   │   ├── reader/      # Parquet reader (GCS)
│   │   └── market/      # Market enum, routing
│   └── go.mod
├── sdk/                 # Python — research interface
│   ├── client.py        # HTTP client to Go API
│   ├── direct.py        # Direct GCS reader
│   └── __init__.py      # quant.data.bars(...) facade
├── notebooks/           # Jupyter notebooks for research
├── terraform/           # GCP infra as code
└── README.md
```

## Data Model

### Bar (primary type for Phase 1)

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Ticker, normalized per market convention |
| timestamp | datetime64[ns, UTC] | Bar open time, always UTC |
| open, high, low, close | float64 | OHLC prices |
| volume | int64 | Shares traded |
| market | string | "US", "CN", "HK" |
| frequency | string | "1m", "5m", "15m", "1h", "1d" |

Future types (Phase 2+): Quote (bid/ask/size), Trade (price/size/side).

## Market Abstraction

```python
class MarketAdapter(Protocol):
    market: str  # "US", "CN", "HK"

    def fetch_bars(self, symbols: list[str], start: datetime,
                   end: datetime, frequency: str = "1m") -> pd.DataFrame: ...
    def fetch_supported_symbols(self) -> list[str]: ...
    def market_hours(self, date: date) -> tuple[time, time]: ...
```

Each market is a self-contained GCS partition. Adding a new market = new adapter + new GCS prefix. No cross-market joins. No global symbol namespace.

## Storage Layout

```
gs://quant-data/
├── raw/
│   ├── us/bars/{YYYY}/{MM}/{DD}/{SYMBOL}.parquet
│   ├── cn/bars/{YYYY}/{MM}/{DD}/{SYMBOL}.parquet   # Future
│   └── hk/bars/{YYYY}/{MM}/{DD}/{SYMBOL}.parquet   # Future
├── processed/                                        # Future
└── metadata/{market}/symbols.json
```

Format: Parquet with Snappy compression. Partitioned by market → data_type → date.

## Go Query API

### Endpoints

- **GET /api/v1/bars** — Query OHLCV bars. Params: `market`, `symbols`, `start`, `end`, `frequency` (default "1m"), `format` ("json" | "parquet"). Returns JSON array or Parquet bytes, sorted by timestamp.
- **GET /api/v1/symbols** — List available symbols and date ranges for a market. Params: `market`.
- **GET /health** — Liveness probe with GCS connectivity + latency stats.

### Internals

1. Parse request → build GCS prefix glob from market/date partitioning
2. List objects via GCS SDK, filter by symbol
3. Stream-read matching Parquet files with column pruning
4. Filter rows by time range in-memory
5. Marshal to JSON or return raw Parquet bytes

Service is stateless. Cloud Run scales to zero when idle.

## Python SDK

```python
import quant.data as qd

# Via Go API (convenient, <1M rows)
df = qd.bars("AAPL", "2026-05-01", "2026-05-13", market="us", frequency="1m")

# Direct GCS (bulk queries, >1M rows)
df = qd.bars_direct("AAPL", "2026-01-01", "2026-05-13", market="us")

# Returns pd.DataFrame with columns [symbol, timestamp, open, high, low, close, volume]
```

## Error Handling

### Collector (Python)
- API rate limit → retry with exponential backoff (1s, 2s, 4s, 8s), max 3 retries
- Partial data → flag in metadata, write available data. Alert if gap > 1 hour.
- GCS write failure → buffer to /tmp, retry. Cloud Run Jobs auto-retry max 3.
- Source unavailable → fallback adapter (yfinance if Alpaca down). Alert if all sources fail.

### Query API (Go)
- Bad params → 400 + JSON error body with field-level details
- Symbol not found → 404 + list of available symbols
- GCS read failure → 500 with Cloud Run retry
- Timeout → partial result with `X-Query-Status: partial` header

### Data Quality (daily Cloud Function)
- Completeness: expected vs actual bars per symbol per trading day
- Freshness: latest bar timestamp, alert if stale > 24h
- Sanity: high < low, negative prices, volume > 10 std dev
- Gap detection: missing bars within session → flag but don't reject

## Testing Strategy

### Python (pytest)
- Unit: adapter response parsing, schema validation, Parquet round-trip
- Integration: live API calls recorded via VCR.py (no network on CI)
- E2E: Cloud Run Job → GCS write → verify Parquet content
- Coverage: >80% collectors, >90% SDK

### Go (testing stdlib)
- Table-driven: handler parsing, GCS path building, market routing
- Integration: local Parquet files (no GCS dependency)
- Benchmark: Parquet read throughput, JSON marshal speed
- Coverage: >80% handlers and reader package

### CI (GitHub Actions)
- Lint (ruff, golangci-lint), type check (mypy, go vet)
- Unit + integration tests
- Terraform plan diff on PR
- GCP auth via Workload Identity Federation (no long-lived secrets)

## GCP Infrastructure (Terraform)

| Resource | Purpose | Est. Monthly Cost |
|----------|---------|-------------------|
| Cloud Storage (Standard) | Parquet data lake | ~$2 (100 GB) |
| Cloud Run (Go API) | Query API, scales to zero | ~$0 (free tier) |
| Cloud Run Jobs | Python collectors | ~$0-5 (pay per exec-sec) |
| Cloud Scheduler | Cron triggers | ~$0 (3 free jobs) |
| BigQuery | External tables, ad-hoc SQL | ~$0-3 (pay per query) |
| Artifact Registry | Docker images | ~$1 |
| Cloud Logging | Structured logs | ~$0 (free tier) |

**Total: ~$3-10/month at crawl stage.**

## Explicit Deferrals (out of scope for Phase 1)

- Real-time WebSocket streaming (Phase 2-3)
- Backtesting engine (Phase 2)
- Broker integration / execution (Phase 3)
- Redis caching layer (Phase 2)
- Grafana dashboards (Phase 4)
- CI/CD deployment automation (manual deploy acceptable)
- A-shares / HK stock collectors (architecture supports, implementation deferred)

## Development Sequence

1. Terraform GCP resources (GCS bucket, service accounts, Artifact Registry)
2. Python schema + market adapter protocol definition
3. Alpaca + yfinance adapters (with VCR tests)
4. Cloud Run Job: scheduled collection → GCS write
5. Go query API (handler → reader → GCS integration)
6. Python SDK (client + direct reader)
7. Jupyter notebook: end-to-end validation (collect → store → query → analyze)
8. BigQuery external table setup
9. Data quality Cloud Function
10. GitHub Actions CI pipeline
