# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quantitative trading system spanning data pipeline → backtesting → execution → monitoring. Python-based: collectors, engine, OMS, execution, dashboard.

**Active branch:** `phase3-execution-oms` (Phase 3 + Phase 4 complete). **Main branch:** `main` (Phase 1 production).

## Commands

### Test

```bash
# Full Python suite (skip VCR tests that need network)
python -m pytest oms/tests/ engine/tests/ collectors/tests/ quality/tests/ -v -k "not vcr"

# Single test file
python -m pytest engine/tests/test_engine.py -v

# Single test
python -m pytest engine/tests/test_engine.py::test_engine_run_buy_hold -v
```

### Lint & Typecheck

```bash
ruff check collectors/ quality/ engine/ oms/ execution/ dashboard/
ruff format --check collectors/ quality/ engine/ oms/ execution/ dashboard/
pip install pandas-stubs && mypy collectors/ --ignore-missing-imports
```

### Build & Deploy

```bash
# Docker images
docker build -t asia-east2-docker.pkg.dev/deductive-notch-495015-c2/quant/collector:latest -f collectors/Dockerfile collectors/
docker build -t asia-east2-docker.pkg.dev/deductive-notch-495015-c2/quant/bq-loader:latest -f bigquery_loader/Dockerfile bigquery_loader/

# Push + deploy
docker push <image> && gcloud run jobs update <name> --region=asia-east2 --image=<image>
```

### Dashboard

```bash
pip install fastapi uvicorn && uvicorn dashboard.api:app --port 8090
# Open dashboard/index.html in browser
```

## Architecture

### Data Flow

```
Collectors (Python, Cloud Run Jobs) → GCS (Parquet write-only archive)
                                   ↘ BigQuery (daily LOAD jobs via bq-loader)
```

GCS is a write-only archive for raw data. All data access goes through BigQuery — no direct GCS reads, no intermediate API or SDK layer.

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

- **Broker** (`oms/broker/__init__.py`): Protocol with async `submit_order()`, `cancel_order()`, `get_positions()`, `get_account()`. Two implementations: `PaperBroker` (simulated, deterministic) and `AlpacaBroker` (real API, paper or live).
- **ExecutionAlgo** (`execution/protocol.py`): `async def run(signal, broker, market_data) -> list`
- **Strategy** (`engine/strategy.py`): User subclasses, implements `on_init(ctx)` and `on_bar(ctx, bar) -> list[Signal]`. Parameters are auto-discovered from class annotations.
- **DataSource** (`engine/data.py`): Protocol. `DataFrameSource` wraps a pre-loaded `pd.DataFrame`.
- **RiskRule** (`engine/risk/protocol.py`): `def apply(orders, portfolio, bar_data) -> list`. Composed in `RiskEngine`.

### Broker Protocol (critical for understanding OMS)

The OMS is async throughout (`asyncio`). The bridge (`oms/bridge.py`) wraps async calls in `asyncio.run()` to connect the synchronous engine to the async OMS. `PaperBroker` is deterministic — market orders fill immediately at synthetic prices, limit orders check against a price set via `update_price()`. `AlpacaBroker` wraps `alpaca-py`'s `TradingClient`.

### GCP Infrastructure

- **Project:** `deductive-notch-495015-c2`, **Region:** `asia-east2`
- **Terraform state:** `gs://deductive-notch-495015-c2-quant-terraform-state`
- **Key services:** Cloud Run Jobs (collector, BQ loader), Cloud Scheduler (cron triggers), GCS (write-only data archive), BigQuery (analytics)
- **Auth:** `gcloud auth login` + `gcloud auth application-default login` for Terraform/SDKs

### Test Structure

- `engine/tests/` — backtesting engine (36 tests)
- `oms/tests/` — OMS, broker, bridge, risk (32 tests)
- `collectors/tests/` — data collection (8 tests)
- `quality/tests/` — data quality (4 tests)
- Total: **80 tests** (2 skipped VCR tests)

## What to Do
- **Conda Environment** ALWAYS use the `quant` conda environment for this project, i.e. `conda activate quant`.
- **New features follow the established layer pattern:** define a Protocol/interface first, implement the concrete class, write tests against a mock/fake, then wire into the bridge/API layer.
- **Add new risk rules** in `engine/risk/` implementing the `RiskRule` protocol, then register in `oms/risk_gateway.py` for live use.
- **Add new broker adapters** by implementing the `Broker` protocol (see `oms/broker/alpaca_broker.py` as template).
- **New strategies** subclass `engine.strategy.Strategy` — parameters are auto-discovered from class annotations for optimization.
- **Python tests use pytest.** Tests that need network (Alpaca API) use `@pytest.mark.vcr` and are excluded from CI with `-k "not vcr"`.
- **Terraform changes** go through `terraform plan` first. State is remote (GCS backend). `terraform.tfvars` is committed (dev project only).
- **Work on feature branches** branched from `main`. Phase branches (`phaseN-*`) represent major milestones.
- **Docker images must be built and pushed** after code changes to collectors or bigquery_loader. Use the `gcloud auth configure-docker` command first.
- **Commit notebooks** with `git add -f` — `*.ipynb` is in `.gitignore` by default.
- **Run `asyncio.run()`** to bridge sync engine code to async OMS/broker calls. Never call async methods directly from sync context.
- **Data access is BigQuery-only.** No direct GCS reads in application code. GCS is a write-only archive for collectors.

## What Not to Do

- **Don't use third-party backtesting frameworks** (backtrader, vectorbt, zipline). The engine is custom from scratch by design.
- **Don't commit `.terraform/`, `*.tfstate`, or credentials.** `.tfvars` for dev is OK; for prod, use a separate secure store.
- **Don't skip `terraform plan` review** before apply. State corruption in GCS backend is hard to undo.
- **Don't mix async/sync carelessly.** The engine is synchronous. The OMS is async. Use `oms/bridge.py` functions which handle the boundary.
- **Don't call Alpaca API directly** from strategy code. Always go through `Broker` protocol → `OrderManager`.
- **Don't hardcode paths or credentials** in Python code. Use env vars (`os.environ.get()`) or config dataclasses.
- **Don't read from GCS directly.** GCS is write-only for archival. All data reads must go through BigQuery.
- **Don't push Docker images without testing locally first.** Cloud Run cold-start failures take minutes to surface.
- **Don't introduce intermediate API/SDK layers between GCS and BigQuery.** The pipeline is: Collectors → GCS → BigQuery. Data is queried directly from BigQuery.
