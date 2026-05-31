# Project Cleanup — Cloud Run & GCS Read Removal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all Cloud Run / GCS-read dependencies: delete query-api (Go), SDK, deprecated Docker/Terraform files; rewrite quality to BQ; remove JSON companion writes; update docs and CI.

**Architecture:** Deletion-heavy cleanup with targeted rewrites. The project moves to a simpler data flow: Collectors → GCS → BigQuery → direct BQ queries from all consumers (PaperRunner, quality, etc.). No intermediate API or SDK layer.

**Tech Stack:** Python 3.12, BigQuery, Terraform, shell scripts.

---

### Task 1: Delete `query-api/` directory

**Files:**
- Delete: `query-api/` (entire tree)

- [ ] **Step 1: Delete the directory**

```bash
git rm -rf query-api/
```

- [ ] **Step 2: Verify no broken references**

```bash
grep -r "query-api\|query_api" --include="*.py" --include="*.go" --include="*.sh" --include="*.tf" --include="*.yml" --include="*.yaml" --include="*.md" . | grep -v ".git/" | grep -v "docs/superpowers"
```

Expected: hits in `CLAUDE.md`, `MEMO.md`, `scripts/status.sh`, `.github/workflows/`, `terraform/` — all will be cleaned in later tasks. No hits in Python source (`oms/`, `engine/`, `collectors/`, `execution/`, `paper/`, `factors/`, `ml/`, `experiment/`).

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove query-api Go service — replaced by direct BQ queries"
```

---

### Task 2: Delete `sdk/` directory

**Files:**
- Delete: `sdk/` (entire tree)

- [ ] **Step 1: Verify no external consumers before deletion**

```bash
grep -rn "from quant\|import quant\|quant\.data\|quant\.client\|quant\.direct\|bars_direct\|QuantClient" --include="*.py" . | grep -v "sdk/" | grep -v ".git/" | grep -v "__pycache__" | grep -v "egg-info"
```

Expected: only `run_paper.py:153` (docstring, not actual import). If any other file imports `quant`, abort and investigate.

- [ ] **Step 2: Delete the directory**

```bash
git rm -rf sdk/
```

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove SDK — GCS direct reads and API client no longer needed"
```

---

### Task 3: Delete Cloud Run Dockerfiles

**Files:**
- Delete: `collectors/Dockerfile`
- Delete: `docker/Dockerfile.collector`
- Delete: `bigquery_loader/Dockerfile`

- [ ] **Step 1: Delete the files**

```bash
git rm collectors/Dockerfile docker/Dockerfile.collector bigquery_loader/Dockerfile
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove Cloud Run Dockerfiles — no longer deployed as containers"
```

---

### Task 4: Delete deprecated Terraform file

**Files:**
- Delete: `terraform/cloud_run_jobs.tf`

- [ ] **Step 1: Delete the file**

```bash
git rm terraform/cloud_run_jobs.tf
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove deprecated Cloud Run jobs terraform"
```

---

### Task 5: Remove JSON companion writes from `collectors/storage.py`

**Files:**
- Modify: `collectors/storage.py`

- [ ] **Step 1: Edit `write_bars_to_gcs` — remove JSON companion block + unused import**

In `collectors/storage.py`, remove the JSON companion write block (lines 69-75) AND the now-unused `import json` (line 2):

```python
# Remove these lines:
        # Write companion JSON for Go API consumption
        json_path = path.replace(".parquet", ".json")
        json_blob = bucket.blob(json_path)
        json_blob.upload_from_string(
            group.to_json(orient="records", date_format="iso"),
            content_type="application/json",
        )
        paths.append(f"gs://{bucket_name}/{json_path}")
```

The function should end with just the parquet write and the paths list containing only parquet paths:

```python
    paths = []

    groups = df.groupby(["symbol", df["timestamp"].dt.date])
    for (symbol, _date), group in groups:
        ts = group["timestamp"].iloc[0]
        path = build_gcs_path(market, "bars", frequency, symbol, ts)
        blob = bucket.blob(path)
        blob.upload_from_string(
            dataframe_to_parquet_bytes(group),
            content_type="application/octet-stream",
        )
        paths.append(f"gs://{bucket_name}/{path}")

    return paths
```

- [ ] **Step 2: Remove `import json` (now unused)**

```bash
sed -i '/^import json$/d' collectors/storage.py
```

- [ ] **Step 3: Verify the edit**

```bash
grep -n "json" collectors/storage.py
```

Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add collectors/storage.py
git commit -m "chore: remove JSON companion writes from storage — Go API is deprecated"
```

---

### Task 6: Rewrite `quality/main.py` to use BigQuery

**Files:**
- Modify: `quality/main.py`

- [ ] **Step 1: Write the new BQ-based quality checker**

Replace the entire content of `quality/main.py`:

```python
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
```

- [ ] **Step 2: Verify the code is syntactically valid**

```bash
conda activate quant && python -c "import ast; ast.parse(open('quality/main.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Update quality-check.service — remove GCS_BUCKET, add BQ env vars**

Edit `/etc/systemd/system/quality-check.service`:

```ini
# /etc/systemd/system/quality-check.service
[Unit]
Description=Quant Data Quality Check

[Service]
Type=oneshot
User=quant
WorkingDirectory=/opt/quant/quality
Environment=GCP_PROJECT=deductive-notch-495015-c2
Environment=MARKET=us
Environment=FREQUENCY=1d
ExecStart=/usr/bin/python3.12 /opt/quant/quality/main.py
StandardOutput=append:/home/quant/logs/quality.log
StandardError=append:/home/quant/logs/quality.log
```

Key changes: `GCS_BUCKET` → `GCP_PROJECT` + `MARKET` + `FREQUENCY`.
(Add a second service `quality-check-hk.service` with `MARKET=hk` later if needed.)

```bash
sudo systemctl daemon-reload
```

- [ ] **Step 4: Commit**

```bash
git add quality/main.py
git commit -m "refactor(quality): rewrite checker to query BigQuery instead of GCS"
```

---

### Task 7: Deprecate `terraform/cloud_run_api.tf`

**Files:**
- Modify: `terraform/cloud_run_api.tf`
- Modify: `terraform/service_accounts.tf`
- Modify: `terraform/outputs.tf`

- [ ] **Step 1: Comment out `cloud_run_api.tf` with DEPRECATED header**

Replace `terraform/cloud_run_api.tf` content:

```hcl
# =============================================================================
# ⛔ DEPRECATED as of 2026-05-31
# The Go query-api service has been retired.
# All data queries now go directly to BigQuery.
# These resources are kept as reference only — do NOT terraform apply.
# =============================================================================

# BEGIN DEPRECATED
# resource "google_cloud_run_v2_service" "query_api" {
#   name                = "quant-query-api"
#   location            = var.region
#   ingress             = "INGRESS_TRAFFIC_ALL"
#   deletion_protection = false
# 
#   template {
#     service_account = google_service_account.query_api.email
#     containers {
#       image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/query-api:latest"
#       env {
#         name  = "GCS_BUCKET"
#         value = google_storage_bucket.quant_data.name
#       }
#       resources {
#         limits = {
#           memory = "512Mi"
#           cpu    = "1"
#         }
#       }
#     }
#     scaling {
#       min_instance_count = 0
#       max_instance_count = 5
#     }
#   }
# }
# 
# # Public access blocked by org policy. Use authenticated access via gcloud auth.
# # resource "google_cloud_run_v2_service_iam_member" "query_api_public" {
# #   name     = google_cloud_run_v2_service.query_api.name
# #   location = var.region
# #   role     = "roles/run.invoker"
# #   member   = "allUsers"
# # }
# END DEPRECATED
```

- [ ] **Step 2: Comment out `query_api` service account and IAM binding in `service_accounts.tf`**

In `terraform/service_accounts.tf`, comment out the query_api SA and its IAM binding (lines 12-21):

```hcl
# BEGIN DEPRECATED — query-api service is retired
# resource "google_service_account" "query_api" {
#   account_id   = "quant-query-api"
#   display_name = "Quant Query API"
# }
# 
# resource "google_storage_bucket_iam_member" "query_api_read" {
#   bucket = google_storage_bucket.quant_data.name
#   role   = "roles/storage.objectViewer"
#   member = "serviceAccount:${google_service_account.query_api.email}"
# }
# END DEPRECATED
```

- [ ] **Step 3: Comment out `query_api_service_account_email` in `terraform/outputs.tf`**

In `terraform/outputs.tf`, comment out lines 9-11:

```hcl
# BEGIN DEPRECATED — query-api service is retired
# output "query_api_service_account_email" {
#   value = google_service_account.query_api.email
# }
# END DEPRECATED
```

- [ ] **Step 4: Verify Terraform is still valid**

```bash
cd terraform && terraform fmt -check -recursive && terraform validate
```

Expected: both pass (terraform validate may warn about the deprecated resources but should not error).

- [ ] **Step 5: Commit**

```bash
git add terraform/cloud_run_api.tf terraform/service_accounts.tf terraform/outputs.tf
git commit -m "chore: deprecate query-api Cloud Run service and service account"
```

---

### Task 8: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite CLAUDE.md**

Replace the entire file:

```markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quantitative trading system spanning data pipeline → backtesting → execution → monitoring.
Python (research, OMS, execution, data collection) + Terraform (GCP infra).

**Active branch:** `feature/quant-next-phase`. **Main branch:** `main`.

## Commands

### Test

```bash
# Full Python suite (skip VCR tests that need network)
python -m pytest oms/tests/ engine/tests/ collectors/tests/ quality/tests/ -v -k "not vcr"

# Single test file
python -m pytest engine/tests/test_engine.py -v

# Single test
python -m pytest engine/tests/test_engine.py::test_engine_run_buy_hold -v

# Terraform validation
cd terraform && terraform fmt -check -recursive && terraform validate
```

### Lint & Typecheck

```bash
ruff check collectors/ quality/ engine/ oms/ execution/ dashboard/
ruff format --check collectors/ quality/ engine/ oms/ execution/ dashboard/
pip install pandas-stubs && mypy collectors/ --ignore-missing-imports
```

### Dashboard

```bash
pip install fastapi uvicorn && uvicorn dashboard.api:app --port 8090
# Open dashboard/index.html in browser
```

## Architecture

### Data Flow

```
Collectors (VM cron + ws_collector) → GCS (Parquet) → BigQuery (daily LOAD jobs)
                                                          ↑
                                              All queries go through BigQuery
```

Data writes: collectors to GCS (archive), then BQ loader loads into BigQuery.
Data reads: everything queries BigQuery directly. GCS is write-only.

### Signal-to-Execution Flow

```
Strategy (engine/strategy.py) → Signal
  ↓
Execution algo (execution/twap.py, vwap.py) — optional TWAP/VWAP
  ↓
OMS Bridge (oms/bridge.py) — convert_signal() + forward_signal()
  ↓
RiskGateway (oms/risk_gateway.py) — pre-trade checks
  ↓
OrderManager (oms/manager.py) → Broker (oms/broker/) → Fill
  ↓
PositionTracker + RiskMonitor (oms/position.py, risk_monitor.py)
```

### Key Interfaces

- **Broker** (`oms/broker/__init__.py`): Protocol with async `submit_order()`, `cancel_order()`, `get_positions()`, `get_account()`. Implementations: `PaperBroker` (simulated), `FutuStockBroker`, `FutuCryptoBroker`, `AlpacaBroker`.
- **ExecutionAlgo** (`execution/protocol.py`): `async def run(signal, broker, market_data) -> list`
- **Strategy** (`engine/strategy.py`): User subclasses, implements `on_init(ctx)` and `on_bar(ctx, bar) -> list[Signal]`. Parameters are auto-discovered from class annotations.
- **DataSource** (`engine/data.py`): Protocol. `DataFrameSource` wraps a pre-loaded `pd.DataFrame`.
- **RiskRule** (`engine/risk/protocol.py`): `def apply(orders, portfolio, bar_data) -> list`. Composed in `RiskEngine`.

### Broker Protocol (critical for understanding OMS)

The OMS is async throughout (`asyncio`). The bridge (`oms/bridge.py`) wraps async calls in `asyncio.run()` to connect the synchronous engine to the async OMS. `PaperBroker` is deterministic — market orders fill immediately at synthetic prices, limit orders check against a price set via `update_price()`.

### GCP Infrastructure

- **Project:** `deductive-notch-495015-c2`, **Region:** `asia-east2`
- **Terraform state:** `gs://deductive-notch-495015-c2-quant-terraform-state`
- **Key services:** GCE VM (collectors, BQ loader), GCS (data lake, write-only), BigQuery (analytics, query)
- **Auth:** `gcloud auth login` + `gcloud auth application-default login` for Terraform/SDKs

### Test Structure

- `engine/tests/` — backtesting engine (36 tests)
- `oms/tests/` — OMS, broker, bridge, risk (32 tests)
- `collectors/tests/` — data collection (8 tests)
- `quality/tests/` — data quality (4 tests)
- Total: ~80 tests (2 skipped VCR tests)

## What to Do
- **Conda Environment** ALWAYS use the `quant` conda environment for this project, i.e. `conda activate quant`.
- **New features follow the established layer pattern:** define a Protocol/interface first, implement the concrete class, write tests against a mock/fake, then wire into the bridge/API layer.
- **Add new risk rules** in `engine/risk/` implementing the `RiskRule` protocol, then register in `oms/risk_gateway.py` for live use.
- **Add new broker adapters** by implementing the `Broker` protocol (see `oms/broker/futu_stock_broker.py` as template).
- **New strategies** subclass `engine.strategy.Strategy` — parameters are auto-discovered from class annotations for optimization.
- **Python tests use pytest.** Tests that need network (Alpaca API) use `@pytest.mark.vcr` and are excluded from CI with `-k "not vcr"`.
- **Terraform changes** go through `terraform plan` first. State is remote (GCS backend). `terraform.tfvars` is committed (dev project only).
- **Work on feature branches** branched from `main`. Phase branches (`phaseN-*`) represent major milestones.
- **Commit notebooks** with `git add -f` — `*.ipynb` is in `.gitignore` by default.
- **Run `asyncio.run()`** to bridge sync engine code to async OMS/broker calls. Never call async methods directly from sync context.

## What Not to Do

- **Don't use third-party backtesting frameworks** (backtrader, vectorbt, zipline). The engine is custom from scratch by design.
- **Don't commit `.terraform/`, `*.tfstate`, or credentials.** `.tfvars` for dev is OK; for prod, use a separate secure store.
- **Don't skip `terraform plan` review** before apply. State corruption in GCS backend is hard to undo.
- **Don't mix async/sync carelessly.** The engine is synchronous. The OMS is async. Use `oms/bridge.py` functions which handle the boundary.
- **Don't call broker API directly** from strategy code. Always go through `Broker` protocol → `OrderManager`.
- **Don't hardcode paths or credentials** in Python code. Use env vars (`os.environ.get()`) or config dataclasses.
- **Don't read data directly from GCS.** All data queries go through BigQuery. GCS is write-only for archive.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md — remove SDK, query-api, Cloud Run references"
```

---

### Task 9: Update `MEMO.md`

**Files:**
- Modify: `MEMO.md`

- [ ] **Step 1: Update the VM services table (Section 二)**

Remove the `query-api` row and update the table:

```markdown
### VM 部署 (GCE asia-east2-a)

| 组件 | 方式 | 状态 | 详情 |
|------|------|------|------|
| OpenD | 手动 | ✅ 运行 | `127.0.0.1:11111`，行情+交易双登录 |
| ws_collector (5m) | systemd | ✅ 运行 | WebSocket 推送，US 234 + HK 15 + Crypto 10 |
| US 1d 采集 | cron (21:30 UTC) | ✅ | Futu API，Mon-Fri，收盘后 |
| HK 1d 采集 | cron (08:30 UTC) | ✅ | Futu API，Mon-Fri，收盘后 |
| BQ Loader ×4 | cron | ✅ | US/HK × 1d+5m，Mon-Fri |
| BQ Loader ×2 | cron | ✅ | Crypto × 1d+5m，daily |
| logrotate | cron | ✅ | 30 天轮转 |
```

- [ ] **Step 2: Update GCP cloud services (Section 二)**

```markdown
### GCP 云服务

| 组件 | 状态 |
|------|------|
| GCS 数据桶 (`deductive-notch-495015-c2-quant-data`) | ✅ 写入归档 |
| BigQuery `quant` dataset (6 张分区分聚簇表) | ✅ |
| Cloud Run | ❌ 已弃用，迁移到 VM |
```

- [ ] **Step 3: Update data architecture diagram (Section 三)**

```markdown
## 三、数据采集架构

```
实时层:   ws_collector (systemd, WebSocket 5m) ──→ GCS ──→ BQ
定时层:   main.py cron ×2 (收盘 1d)            ──→ GCS ──→ BQ
历史层:   backfill.py (回填)                    ──→ GCS ──→ BQ
查询层:   所有模块直接查询 BigQuery（GCS 仅写入归档）

标的:     三者统一通过 Futu API fetch_supported_symbols() 获取
          US=234 / HK=15 / Crypto=10
GCS 路径: raw/{market}/bars/freq={freq}/year={YYYY}/month={MM}/day={DD}/symbol={SYMBOL}.parquet
BQ 表:    quant.{market}_bars_{freq} — PARTITION BY DATE(timestamp), CLUSTER BY symbol
```
```

- [ ] **Step 4: Update project structure table (Section 五)**

Remove `query-api/` and `sdk/` rows:

```markdown
## 五、项目结构

| 目录 | 内容 | 状态 |
|------|------|------|
| `engine/` | 回测引擎、策略接口、风控、walk-forward、ML pred | ✅ |
| `oms/` | OrderManager、Broker (Paper/Alpaca/Futu Stock/Crypto)、Router、风控 | ✅ |
| `execution/` | TWAP/VWAP 算法执行 | ✅ |
| `collectors/` | backfill.py / main.py / ws_collector.py + adapters (Futu/YFinance/Binance) | ✅ |
| `bigquery_loader/` | GCS Parquet → BigQuery 批量加载 + 去重 | ✅ |
| `factors/` | FactorBuilder — 43+ 因子 (Alpha158 + HK 特色) | ✅ |
| `ml/` | ModelTrainer — OLS/Ridge/LightGBM + IC 评估 | ✅ |
| `experiment/` | ExperimentTracker + InvestmentRecord | ✅ |
| `paper/` | Paper Runner — 多市场历史回放模拟 | ✅ |
| `quality/` | 数据质量监控（BQ 直查） | ✅ |
| `scripts/` | cron_wrapper.sh / backfill_chain.sh | ✅ |
| `docker/` | Dockerfile.collector + OpenD 启动脚本 | ❌ 已弃用 |
| `terraform/` | GCP 基础设施 IaC | ✅ |
| `docs/` | 设计文档、回填追踪 | ✅ |
```

- [ ] **Step 5: Update code scale metrics (Section 十)**

```markdown
- **代码规模**: Python 13k+ 行 / Terraform ~500 行
- **测试覆盖**: 80+ Python 测试
```

- [ ] **Step 6: Commit**

```bash
git add MEMO.md
git commit -m "docs: update MEMO.md — remove query-api, SDK, Cloud Run references"
```

---

### Task 10: Update `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Remove sdk references**

In `pyproject.toml`, update the `include` line and testpaths:

```toml
[tool.setuptools.packages.find]
include = ["collectors*", "quality*"]

[tool.pytest.ini_options]
testpaths = ["collectors/tests", "quality/tests"]
pythonpath = [".", "collectors"]
```

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "chore: remove sdk from pyproject.toml package and test paths"
```

---

### Task 11: Update `scripts/status.sh`

**Files:**
- Modify: `scripts/status.sh`

- [ ] **Step 1: Remove query-api status check**

Remove lines 32-33 (the "Query API" section):

```bash
# Remove:
echo ""
echo "--- Query API ---"
systemctl is-active query-api 2>/dev/null || echo "  NOT RUNNING"
```

- [ ] **Step 2: Commit**

```bash
git add scripts/status.sh
git commit -m "chore: remove query-api from status.sh"
```

---

### Task 12: Update `.github/workflows/ci.yml`

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Remove sdk/ from lint and test, remove go-test job**

Replace the entire file:

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
      - run: ruff check collectors/ quality/
      - run: ruff format --check collectors/ quality/

  python-typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install mypy pandas-stubs
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
      - run: pip install -r quality/requirements.txt
      - run: python -m pytest collectors/tests/ quality/tests/ -v --cov

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
git commit -m "ci: remove sdk, query-api, and go-test from CI pipeline"
```

---

### Task 13: Update `.github/workflows/deploy.yml`

**Files:**
- Modify: `.github/workflows/deploy.yml`

- [ ] **Step 1: Remove collector, query-api, and Cloud Run deployment steps**

Replace the entire file:

```yaml
name: Deploy

on:
  push:
    branches: [main]

concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: true

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write

    steps:
      - uses: actions/checkout@v4

      - id: auth
        name: Authenticate to GCP
        uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}

      - name: Set up gcloud CLI
        uses: google-github-actions/setup-gcloud@v2

      - name: Trigger VM deploy via gcloud
        run: |
          gcloud compute ssh quant-vm \
            --zone=${{ vars.GCP_REGION || 'asia-east2' }}-a \
            --command="cd /opt/quant && git fetch origin main && git reset --hard origin/main && sudo systemctl restart ws-collector" || \
            echo "::error::VM deploy failed — check VM connectivity and git state"
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: simplify deploy — remove Cloud Run, just pull on VM"
```

---

### Task 14: Update `terraform/startup.sh`

**Files:**
- Modify: `terraform/startup.sh`

- [ ] **Step 1: Remove Go installation from apt packages (Section 1)**

On line 15, remove `golang-go`:

```bash
apt-get install -y --no-install-recommends \
    ubuntu-mate-desktop xrdp \
    python3 python3-pip python3-venv \
    git curl unzip wget \
    chromium-browser nginx \
    cron
```

- [ ] **Step 2: Remove Go Query API build and systemd sections (Sections 6-7)**

Remove lines 62-90 (the "Build Go Query API" and "systemd: Query API" sections).

- [ ] **Step 3: Commit**

```bash
git add terraform/startup.sh
git commit -m "chore: remove Go query-api build and systemd from startup script"
```

---

### Task 15: Fix BQ loader cron log paths

**Problem:** All 6 BQ loader cron jobs redirect output to `/var/log/bq_loader.log` but the `quant` user has no write permission to `/var/log/`. Output is silently discarded, making it impossible to confirm success or diagnose failures.

**Fix:** Rewire all 6 BQ loader cron jobs to use `cron_wrapper.sh` (same as collectors), which logs to `/home/quant/logs/` with START/OK/FAILED markers and automatic alert on failure.

**Files:**
- Modify: quant user's crontab (`sudo crontab -u quant -e`)

- [ ] **Step 1: Replace 6 BQ loader cron entries**

Replace the first 6 entries in quant's crontab with cron_wrapper.sh equivalents:

```cron
# BigQuery Data Loaders
# US 5m bars (Mon-Fri, 6am UTC)
0 6 * * 1-5 /opt/quant/scripts/cron_wrapper.sh bq_loader_us_5m env GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=us FREQUENCY=5m TABLE=us_bars_5m python3.12 -m bigquery_loader.main
# US 1d bars
0 6 * * 1-5 /opt/quant/scripts/cron_wrapper.sh bq_loader_us_1d env GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=us FREQUENCY=1d TABLE=us_bars_1d python3.12 -m bigquery_loader.main
# HK 5m bars (Mon-Fri, 9:30am UTC)
30 9 * * 1-5 /opt/quant/scripts/cron_wrapper.sh bq_loader_hk_5m env GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=hk FREQUENCY=5m TABLE=hk_bars_5m python3.12 -m bigquery_loader.main
# HK 1d bars
30 9 * * 1-5 /opt/quant/scripts/cron_wrapper.sh bq_loader_hk_1d env GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=hk FREQUENCY=1d TABLE=hk_bars_1d python3.12 -m bigquery_loader.main
# Crypto 5m bars (daily, 6am UTC)
0 6 * * * /opt/quant/scripts/cron_wrapper.sh bq_loader_crypto_5m env GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=crypto FREQUENCY=5m TABLE=crypto_bars_5m python3.12 -m bigquery_loader.main
# Crypto 1d bars (daily, 1am UTC)
0 1 * * * /opt/quant/scripts/cron_wrapper.sh bq_loader_crypto_1d env GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=crypto FREQUENCY=1d TABLE=crypto_bars_1d python3.12 -m bigquery_loader.main
```

- [ ] **Step 2: Clean up stale empty log files**

```bash
sudo rm -f /var/log/bq_loader.log /var/log/bq_loader_*.log
```

- [ ] **Step 3: Verify crontab is valid**

```bash
sudo crontab -u quant -l | grep bq_loader
```

Expected: 6 lines, all using `cron_wrapper.sh`, no `/var/log/` references.

- [ ] **Step 4: Document in MEMO.md**

Crontab changes are not tracked in git. The MEMO.md update in Task 9 will reflect the new log paths.

---

### Task 16: Final verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run all Python tests**

```bash
conda activate quant && python -m pytest collectors/tests/ quality/tests/ engine/tests/ oms/tests/ -v -k "not vcr"
```

Expected: all tests pass (tests that passed before should still pass).

- [ ] **Step 2: Verify Terraform is valid**

```bash
cd terraform && terraform fmt -check -recursive && terraform validate
```

Expected: both pass.

- [ ] **Step 3: Run ruff lint**

```bash
ruff check collectors/ quality/ engine/ oms/ execution/ dashboard/
```

Expected: no new errors.

- [ ] **Step 4: Check for any remaining broken references**

```bash
grep -rn "query-api\|query_api\|sdk/quant\|bars_direct\|QuantClient\|GCSBarReader\|source.*direct\|source.*api" --include="*.py" --include="*.go" --include="*.sh" --include="*.tf" --include="*.yml" --include="*.yaml" --include="*.toml" . | grep -v ".git/" | grep -v "__pycache__" | grep -v "docs/" | grep -v "egg-info"
```

Expected: no output (or only hits inside `docs/` directory which are historical design docs).

- [ ] **Step 5: Final commit if any straggler fixes were needed**

```bash
git status
```

If clean, done. If not, review stragglers and commit.
```

---

### Dependency Order

Tasks 1-4 are independent deletions (can run in parallel).
Tasks 5-6 are independent modifications.
Task 15 is independent (system config, no code dependency).
Tasks 7-14 depend on Tasks 1-4 being done (for clean grep/verify steps).
Task 16 runs last, after all changes.
