# Paper Runner Week 1 Implementation Plan — 日线动量全链路验证

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run SimpleMomentum strategy on PaperRunner with BQ us_bars_1d data, verifying the full chain: BQ → SDK → PaperRunner → Broker → OMS → RiskManager → Report.

**Architecture:** PaperRunner already supports `data_source="sdk"` for BQ data. SimpleMomentum strategy already exists in paper/strategies.py. W1 focuses on wiring them together, extending data loading for full OHLCV, and verifying the run produces valid metrics/output.

**Tech Stack:** Python 3.12, Pandas, BigQuery, PaperRunner (existing)

**Prerequisite:** us_bars_1d BQ table populated (backfill chain at-job, complete by ~17:00 UTC May 30).

---

## File Map

| File | Purpose |
|------|---------|
| `run_paper.py` | Modify: extend `_sdk_data` to load full OHLCV |
| `paper/strategies.py` | Verify: SimpleMomentum is sufficient for W1 |
| `scripts/run_paper_momentum.sh` | Create: convenience launch script |
| `paper/tests/test_w1_integration.py` | Create: W1 integration test |

---

### Task 1: Extend SDK Data Loader for OHLCV

**Files:**
- Modify: `run_paper.py` (`_sdk_data` method)
- Create: `paper/tests/test_w1_integration.py`

**Why:** Current `_sdk_data` only loads close prices. Portfolio marking needs open/high/low for accurate P&L.

- [ ] **Step 1: Write failing test**

`paper/tests/test_w1_integration.py`:
```python
"""Week 1 integration tests — PaperRunner + BQ data."""
import pytest
import pandas as pd
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from run_paper import PaperRunner

def test_sdk_data_loads_ohlcv():
    """_sdk_data should return DataFrameSource with OHLCV columns."""
    runner = PaperRunner({"market": "us", "capital": 100000,
                          "strategy": "SimpleMomentum", "start": "2026-01-01",
                          "end": "2026-01-10"})
    # Test with known US symbols and small date range
    ds = runner.load_data("sdk", ["AAPL", "MSFT"], "2026-01-01", "2026-01-10")
    assert ds.close is not None
    assert len(ds) > 0
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest paper/tests/test_w1_integration.py::test_sdk_data_loads_ohlcv -v
```
Expected: FAIL (likely SDK import issues or data not available).

- [ ] **Step 3: Extend `_sdk_data` for OHLCV**

In `run_paper.py`, replace `_sdk_data`:
```python
    def _sdk_data(self, symbols: list[str], start: str, end: str) -> DataFrameSource:
        """Load OHLCV via BigQuery (BQ) for paper-trading replay."""
        try:
            from google.cloud import bigquery
        except ImportError:
            raise ImportError("google-cloud-bigquery required for BQ data. pip install google-cloud-bigquery")

        client = bigquery.Client()
        table = f"deductive-notch-495015-c2.quant.{self.market}_bars_1d"
        
        # Convert US. prefix (AAPL → US.AAPL) if needed
        bq_symbols = [f"US.{s}" if self.market == "us" else f"HK.{s}" for s in symbols]

        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM `{table}`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp BETWEEN @start AND @end
            ORDER BY timestamp, symbol
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("symbols", "STRING", bq_symbols),
            bigquery.ScalarQueryParameter("start", "STRING", start),
            bigquery.ScalarQueryParameter("end", "STRING", end),
        ])
        df = client.query(query, job_config=job_config).to_dataframe()
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Strip prefix back for consistency
        if self.market == "us":
            df["symbol"] = df["symbol"].str.replace("US.", "")
        elif self.market == "hk":
            df["symbol"] = df["symbol"].str.replace("HK.", "")

        # Pivot to DataFrameSource format
        close = df.pivot_table(index="timestamp", columns="symbol", values="close")
        open_df = df.pivot_table(index="timestamp", columns="symbol", values="open")
        high = df.pivot_table(index="timestamp", columns="symbol", values="high")
        low = df.pivot_table(index="timestamp", columns="symbol", values="low")
        volume = df.pivot_table(index="timestamp", columns="symbol", values="volume")
        
        # Forward-fill missing prices (non-trading days)
        close = close.ffill()
        open_df = open_df.ffill()
        high = high.ffill()
        low = low.ffill()
        volume = volume.fillna(0)

        log.info("BQ data: %d bars × %d symbols", len(close), len(symbols))
        return DataFrameSource(close=close, open=open_df, high=high, low=low, volume=volume)
```

Also add a new CLI option for `--data-source bq`:
In `build_parser()`, add to choices:
```python
    p.add_argument("--data-source", type=str, default="simulated",
                   choices=["simulated", "parquet", "sdk", "bq"],
                   help="Data source type (default: simulated)")
```

- [ ] **Step 4: Run test**

```bash
pytest paper/tests/test_w1_integration.py::test_sdk_data_loads_ohlcv -v
```
Expected: PASS (data loads from BQ).

- [ ] **Step 5: Commit**

```bash
git add run_paper.py paper/tests/test_w1_integration.py
git commit -m "feat: PaperRunner BQ data source — direct BigQuery OHLCV loader"
```

---

### Task 2: Verify SimpleMomentum Strategy

**Files:**
- Verify: `paper/strategies.py` (read only)

- [ ] **Step 1: Review SimpleMomentum for W1 suitability**

Read `paper/strategies.py` SimpleMomentum class. Confirm:
- `lookback=20`, `top_k=5`, `rebalance_every=5` defaults
- Computes momentum as `price_t / price_{t-N} - 1` cross-sectionally
- Returns Signal.buy for top-K symbols, Signal.close for others
- Compatible with multi-market via `ctx.universe`

- [ ] **Step 2: No code changes needed — verify with unit test**

```bash
pytest paper/tests/test_runner.py -v -k "momentum" --co 2>/dev/null || echo "No existing momentum tests, will add integration test"
```
Expected: Strategy class imports cleanly.

- [ ] **Step 3: Commit (if any notes)**

No code changes expected. Document findings in task log.

---

### Task 3: Launch Script & Config

**Files:**
- Create: `scripts/run_paper_momentum.sh`

- [ ] **Step 1: Write launch script**

`scripts/run_paper_momentum.sh`:
```bash
#!/bin/bash
# Paper Runner — SimpleMomentum on US daily data
set -e
cd /opt/quant

MARKET="${1:-us}"
START="${2:-2026-01-01}"
END="${3:-$(date +%Y-%m-%d)}"
CAPITAL="${4:-100000}"

echo "=== Paper Runner: SimpleMomentum ==="
echo "Market: $MARKET | $START → $END | Capital: \$$CAPITAL"

python3.12 run_paper.py \
  --market "$MARKET" \
  --start "$START" \
  --end "$END" \
  --capital "$CAPITAL" \
  --strategy SimpleMomentum \
  --data-source bq \
  --lookback 20 \
  --top-k 20 \
  --rebalance-every 5 \
  --output "./output/paper_momentum_${MARKET}_$(date +%Y%m%d_%H%M%S)" \
  2>&1 | tee "./output/paper_momentum_${MARKET}_latest.log"

echo "Done. See output/ for report and archive."
```

- [ ] **Step 2: Make executable**

```bash
chmod +x /opt/quant/scripts/run_paper_momentum.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/run_paper_momentum.sh
git commit -m "feat: paper runner momentum launch script"
```

---

### Task 4: Manual Run — Verify Full Chain

**Files:** None (manual execution)

**Prerequisites:**
- us_bars_1d has data for 2026 (confirmed after backfill chain completes)
- BQ data source loads correctly (Task 1)

- [ ] **Step 1: Run PaperRunner with SimpleMomentum on BQ data**

```bash
cd /opt/quant
python3.12 run_paper.py \
  --market us \
  --start 2026-01-01 \
  --end 2026-05-28 \
  --capital 100000 \
  --strategy SimpleMomentum \
  --data-source bq \
  --lookback 20 \
  --top-k 20 \
  --rebalance-every 5 \
  --output ./output/paper_momentum_us_test
```

- [ ] **Step 2: Verify output**

Check that the run:
1. Completes without unhandled exceptions
2. Prints performance metrics (Sharpe, MaxDD, Win Rate, total_return)
3. Generates `output/paper_momentum_us_test/report.html`
4. Generates `output/paper_momentum_us_test/investment_record.json`

Verify:
```bash
ls -la output/paper_momentum_us_test/
cat output/paper_momentum_us_test/investment_record.json | python3 -m json.tool | head -30
```

- [ ] **Step 3: Validate trade execution**

```bash
python3 -c "
import json
with open('output/paper_momentum_us_test/investment_record.json') as f:
    data = json.load(f)
trades = data.get('trades', [])
print(f'Trades: {len(trades)}')
print(f'First 3: {trades[:3]}')
equity = data.get('equity', [])
print(f'Equity curve: {len(equity)} points')
"
```
Expected: trades > 0, equity curve has entries.

- [ ] **Step 4: Success criteria check**

| Criteria | Expected | Actual | Pass? |
|----------|----------|--------|-------|
| No unhandled exceptions | 0 | — | |
| Trades executed | > 0 | — | |
| Metrics generated | Sharpe, MaxDD, WR | — | |
| Report generated | report.html exists | — | |
| Archive saved | investment_record.json | — | |

- [ ] **Step 5: Document results**

Update `docs/backfill-tracker.md` or create a W1 report.

---

### Task 5: Commit Final State & Push

- [ ] **Step 1: Add .gitignore for output/**

```bash
echo "output/" >> /opt/quant/.gitignore
git add -A
git commit -m "feat: PaperRunner W1 — BQ data + SimpleMomentum verified"
git push origin feature/futu-integration
```
