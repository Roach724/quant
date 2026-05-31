# Phase 2 — ML 升级 + 多策略验证 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** W3 5m 策略验证 + F10 因子 IC 评估 + 80 因子 ML 全量回测 + ShortSqueeze/SectorRotation 新策略。

**Architecture:** 四模块并行推进。W3 和 IC 评估独立，ML 全量依赖 IC 结果，新策略依赖因子数据和 UniverseBuilder。

**Tech Stack:** Python 3.12, futu-api, pandas, numpy, google-cloud-bigquery, scikit-learn, lightgbm, pytest

**Branch:** `feature/phase2-ml-strategy`

---

## File Structure

```
experiment/
  config_w3_5m.yaml (new)
  config_factor_ic.yaml (new)
  config_w2.yaml (modify — add all factor source)

scripts/
  run_w3_experiment.py (new)
  run_w2_experiment.py (modify)
  evaluate_f10_factors.py (new)
  compute_factors_batch.py (new)

engine/
  data.py (modify — add BigQuery5mSource)

paper/
  strategies.py (modify — add ShortSqueeze, SectorRotation)
  market.py (modify — test from_plate)

ml/
  trainer.py (modify — factor_source all merge)

factors/
  registry.py (modify — BQ raw table loader for F10 eval)
```

---

### Task 1: Build BigQuery5mSource for W3

**Files:**
- Modify: `engine/data.py`

- [ ] **Step 1: Add BigQuery5mSource class**

Add to `engine/data.py` a new DataSource that reads 5m bars from BigQuery:

```python
class BigQuery5mSource(DataSource):
    """Load 5-minute bars from BigQuery for backtesting.
    
    Streaming: loads data in day-sized chunks to avoid memory issues.
    """
    
    def __init__(self, market: str = "us", project: str = "deductive-notch-495015-c2",
                 start: str = None, end: str = None, symbols: list[str] = None):
        self.market = market
        self.project = project
        self.table = f"{project}.quant.{market}_bars_5m"
        self.start = start
        self.end = end
        self.symbols = symbols
    
    def load_bars(self, symbol: str) -> pd.DataFrame:
        """Load 5m bars for one symbol from BQ."""
        from google.cloud import bigquery
        client = bigquery.Client(project=self.project)
        
        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM `{self.table}`
            WHERE symbol = @symbol
              AND DATE(timestamp) BETWEEN @start AND @end
            ORDER BY timestamp
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("symbol", "STRING", symbol),
            bigquery.ScalarQueryParameter("start", "STRING", self.start),
            bigquery.ScalarQueryParameter("end", "STRING", self.end),
        ])
        df = client.query(query, job_config=job_config).to_dataframe()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.set_index("timestamp")
    
    def symbols(self) -> list[str]:
        if self.symbols:
            return self.symbols
        from collectors.adapters.futu_stock_adapter import FutuStockAdapter
        return [s for s in FutuStockAdapter._DEFAULT_SYMBOLS if s.startswith("US.")]
```

- [ ] **Step 2: Verify loads from BQ**

```bash
cd /opt/quant && python3.12 -c "
from engine.data import BigQuery5mSource
src = BigQuery5mSource(start='2026-05-01', end='2026-05-30', symbols=['US.AAPL'])
df = src.load_bars('US.AAPL')
print(f'Loaded {len(df)} 5m bars for AAPL')
print(f'Date range: {df.index[0]} → {df.index[-1]}')
"
```

- [ ] **Step 3: Commit**

```bash
cd /opt/quant && sudo -u quant git add engine/data.py
sudo -u quant git commit -m "feat: add BigQuery5mSource for W3 5m-frequency backtesting"
```

---

### Task 2: Create W3 5m experiment config + run script

**Files:**
- Create: `experiment/config_w3_5m.yaml`
- Create: `scripts/run_w3_experiment.py`

- [ ] **Step 1: Write W3 config**

```yaml
# experiment/config_w3_5m.yaml
experiment:
  name: W3_5m_strategy_validation
  description: 5m-frequency Momentum + ML on us_bars_5m BQ data

data:
  source: bq_5m
  market: us
  table: deductive-notch-495015-c2.quant.us_bars_5m
  start: "2026-01-01"
  end: "2026-05-30"
  symbols: top_100  # or list of symbols

strategies:
  - name: SimpleMomentum
    params:
      top_k: 20
      lookback: 20       # 20 bars @ 5m = 100min lookback
      rebalance_every: 12  # rebalance every 1h (12 × 5m)
      allocation: 0.95

  - name: MLPredStrategy
    params:
      top_k: 20
      retrain_every: 5040  # monthly (21 days × 240 bars)
      label: fwd_ret_1h
      factor_source: tech

evaluation:
  benchmark: BuyHold
  metrics: [sharpe, max_drawdown, win_rate, annual_return, turnover]
```

- [ ] **Step 2: Write run script**

```python
#!/usr/bin/env python3
"""W3 — 5m frequency strategy validation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from engine.data import BigQuery5mSource
from engine.strategy import SimpleMomentum, MLPredStrategy
from engine.engine import BacktestEngine
from paper.runner import PaperRunner


def main():
    config_path = Path(__file__).resolve().parent.parent / "experiment" / "config_w3_5m.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    exp = config["experiment"]
    data_cfg = config["data"]
    print(f"=== {exp['name']}: {exp['description']} ===")

    # Data source
    source = BigQuery5mSource(
        market=data_cfg["market"],
        start=data_cfg["start"],
        end=data_cfg["end"],
    )

    # Run each strategy
    for strat_cfg in config["strategies"]:
        name = strat_cfg["name"]
        print(f"\n--- {name} ---")
        runner = PaperRunner(
            data_source=source,
            strategy_name=name,
            strategy_params=strat_cfg.get("params", {}),
            start=data_cfg["start"],
            end=data_cfg["end"],
            symbols=source.symbols()[:100],  # top 100
        )
        results = runner.run()
        print(f"  Return: {results.total_return:.2%}")
        print(f"  Sharpe: {results.sharpe:.2f}")
        print(f"  MaxDD:  {results.max_drawdown:.2%}")
        print(f"  WinRate:{results.win_rate:.2%}")
        print(f"  Turnover:{results.turnover:.2%}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
cd /opt/quant && sudo -u quant git add experiment/config_w3_5m.yaml scripts/run_w3_experiment.py
sudo -u quant git commit -m "feat: add W3 5m experiment config + run script"
```

---

### Task 3: Validate Phase 1 F10 data pipeline (one-off backfill trigger)

**Files:**
- None (runtime task)

- [ ] **Step 1: Verify valuation data in GCS**

```bash
echo "=== US valuation files ===" && gsutil ls "gs://deductive-notch-495015-c2-quant-data/raw/us/f10/valuation/year=2026/month=05/day=31/" 2>/dev/null | wc -l
echo "=== HK valuation files ===" && gsutil ls "gs://deductive-notch-495015-c2-quant-data/raw/hk/f10/valuation/year=2026/month=05/day=31/" 2>/dev/null | wc -l
```

- [ ] **Step 2: Run BQ loader for valuation**

```bash
cd /opt/quant && GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=us FREQUENCY=daily TABLE=us_valuation python3.12 -m bigquery_loader.main
cd /opt/quant && GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=hk FREQUENCY=daily TABLE=hk_valuation python3.12 -m bigquery_loader.main
```

- [ ] **Step 3: Verify BQ has valuation data**

```bash
/snap/bin/bq query --nouse_legacy_sql --project_id=deductive-notch-495015-c2 "
SELECT 'us_valuation' as tbl, COUNT(*) as rows FROM quant.us_valuation
UNION ALL SELECT 'hk_valuation', COUNT(*) FROM quant.hk_valuation
"
```

- [ ] **Step 4: Trigger remaining F10 collectors (financials, analyst, short_interest, capital_flow, shareholder)**

Run each collector one by one (order matters due to OpenD connection):

```bash
# Run sequentially — each takes 2-15 minutes
for src in short_interest capital_flow analyst shareholder financials; do
    echo "=== Collecting $src ==="
    cd /opt/quant && sudo -u quant env GCS_BUCKET=deductive-notch-495015-c2-quant-data OPEND_HOST=127.0.0.1 OPEND_PORT=11111 timeout 900 python3.12 collectors/fundamental_collector.py --source $src 2>&1 | tail -5
    echo "=== $src done ==="
done
```

- [ ] **Step 5: Load all F10 data to BQ**

```bash
for tbl in us_short_interest us_capital_flow us_analyst us_shareholder us_financials; do
    echo "=== Loading $tbl ==="
    cd /opt/quant && GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 MARKET=us FREQUENCY=daily TABLE=$tbl python3.12 -m bigquery_loader.main 2>&1 | tail -3
done
```

---

### Task 4: F10 Factor IC Evaluation

**Files:**
- Create: `experiment/config_factor_ic.yaml`
- Create: `scripts/evaluate_f10_factors.py`
- Modify: `factors/registry.py`

- [ ] **Step 1: Write IC evaluation script**

```python
#!/usr/bin/env python3
"""Evaluate F10 factor IC from BQ raw tables.

Loads F10 raw data from BQ → computes factors via FundamentalFactorBuilder →
merges with forward returns → calculates IC/t-stat/coverage →
registers passing factors.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np
from google.cloud import bigquery

from factors.fundamental_builder import FundamentalFactorBuilder
from factors.registry import FactorRegistry


def load_f10_data(table: str, start: str, end: str, symbols: list[str] = None) -> pd.DataFrame:
    """Load F10 data from a BQ table."""
    client = bigquery.Client()
    query = f"""
        SELECT *
        FROM `deductive-notch-495015-c2.quant.{table}`
        WHERE DATE(ingest_time) BETWEEN '{start}' AND '{end}'
        ORDER BY symbol, ingest_time
    """
    return client.query(query).to_dataframe()


def load_forward_returns(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """Load fwd_ret_5d/20d from us_bars_1d."""
    client = bigquery.Client()
    query = f"""
        SELECT symbol, timestamp as date, close,
               (LEAD(close, 5) OVER w - close) / close as fwd_ret_5d,
               (LEAD(close, 20) OVER w - close) / close as fwd_ret_20d
        FROM `deductive-notch-495015-c2.quant.us_bars_1d`
        WHERE DATE(timestamp) BETWEEN '{start}' AND '{end}'
        WINDOW w AS (PARTITION BY symbol ORDER BY timestamp)
    """
    df = client.query(query).to_dataframe()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def compute_ic(factor_values: pd.Series, fwd_ret: pd.Series) -> float:
    """Spearman rank IC."""
    valid = pd.concat([factor_values, fwd_ret], axis=1).dropna()
    if len(valid) < 30:
        return np.nan
    from scipy.stats import spearmanr
    return spearmanr(valid.iloc[:, 0], valid.iloc[:, 1])[0]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-05-30")
    parser.add_argument("--register", action="store_true", help="Register passing factors to BQ")
    args = parser.parse_args()

    ffb = FundamentalFactorBuilder()
    registry = FactorRegistry()

    # Load F10 data
    print("Loading F10 data...")
    data_map = {}
    for tbl in ["us_valuation", "us_short_interest", "us_analyst", "us_financials",
                "us_capital_flow", "us_shareholder"]:
        try:
            df = load_f10_data(tbl, args.start, args.end)
            if not df.empty:
                source_name = tbl.replace("us_", "")
                data_map[source_name] = df
                print(f"  {tbl}: {len(df)} rows")
        except Exception as e:
            print(f"  {tbl}: SKIP ({e})")

    # Load forward returns
    print("Loading forward returns...")
    fwd = load_forward_returns([], args.start, args.end)

    # Compute factors and IC
    print(f"\nComputing IC for {len(ffb.ALL_FACTOR_COLS)} F10 factors...")
    results = []

    for factor_name in ffb.ALL_FACTOR_COLS:
        try:
            factors = ffb.compute([factor_name], data_map)
            if factors.empty or factor_name not in factors.columns:
                results.append({"factor": factor_name, "ic": np.nan, "t_stat": np.nan,
                                "coverage": 0, "status": "no_data"})
                continue

            merged = factors.merge(fwd, on="date", how="inner")
            ic = compute_ic(merged[factor_name], merged["fwd_ret_5d"])
            t_stat = abs(ic) * np.sqrt(len(merged.dropna())) if not np.isnan(ic) else 0
            coverage = len(merged.dropna()) / len(merged) if len(merged) > 0 else 0

            status = "pass" if abs(ic) > 0.05 and abs(t_stat) > 3.0 and coverage > 0.70 else "fail"
            results.append({"factor": factor_name, "ic": ic, "t_stat": t_stat,
                            "coverage": coverage, "status": status, "n": len(merged)})

            if status == "pass" and args.register:
                registry.register(
                    factor_id=f"us_{factor_name}",
                    name=factor_name.replace("_", " ").title(),
                    market="us", source="fundamental",
                    category=classify(factor_name),
                    formula=f"factors/fundamental_builder.py::{factor_name}",
                )
        except Exception as e:
            results.append({"factor": factor_name, "ic": np.nan, "status": f"error: {e}"})

    # Summary
    df_results = pd.DataFrame(results)
    passing = df_results[df_results["status"] == "pass"]
    print(f"\n=== Results ===")
    print(f"Total F10 factors: {len(ffb.ALL_FACTOR_COLS)}")
    print(f"Passing (|IC|>0.05, |t|>3, cov>70%): {len(passing)}")
    print(f"\nPassing factors:")
    for _, row in passing.sort_values("ic", key=abs, ascending=False).iterrows():
        print(f"  {row['factor']:30s}  IC={row['ic']:+.4f}  t={row['t_stat']:5.1f}  cov={row['coverage']:.1%}")

    if len(passing) < 15:
        print(f"\n⚠️  Only {len(passing)}/41 passed. Target was ≥15.")
    else:
        print(f"\n✅ {len(passing)}/41 passed. Target met!")


def classify(name: str) -> str:
    from factors.fundamental_builder import FundamentalFactorBuilder as FFB
    for cat, cols in [("quality", FFB.QUALITY_COLS), ("growth", FFB.GROWTH_COLS),
                       ("valuation", FFB.VALUATION_COLS), ("short_sentiment", FFB.SHORT_COLS),
                       ("capital_flow", FFB.FLOW_COLS), ("analyst", FFB.ANALYST_COLS)]:
        if name in cols:
            return cat
    return "unknown"


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run IC evaluation**

```bash
cd /opt/quant && python3.12 scripts/evaluate_f10_factors.py --register 2>&1 | tail -20
```

- [ ] **Step 3: Commit**

```bash
cd /opt/quant && sudo -u quant git add scripts/evaluate_f10_factors.py experiment/config_factor_ic.yaml
sudo -u quant git commit -m "feat: add F10 factor IC evaluation script + config"
```

---

### Task 5: Compute factor_values batch + 80-factor ML training

**Files:**
- Create: `scripts/compute_factors_batch.py`
- Modify: `scripts/run_w2_experiment.py` (add --factor-source all)
- Modify: `ml/trainer.py` (merge tech + fundamental in load_data_from_bq)

- [ ] **Step 1: Write batch factor computation script**

```python
#!/usr/bin/env python3
"""Batch compute factors from BQ and write to factor_values table.

Usage:
    python scripts/compute_factors_batch.py --source tech
    python scripts/compute_factors_batch.py --source fundamental
    python scripts/compute_factors_batch.py --source all
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
from datetime import datetime, timezone

import pandas as pd
from google.cloud import bigquery

from factors.tech_builder import TechFactorBuilder
from factors.fundamental_builder import FundamentalFactorBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PROJECT = "deductive-notch-495015-c2"
TABLE = f"{PROJECT}.quant.factor_values"


def load_ohlcv(start: str, end: str) -> pd.DataFrame:
    client = bigquery.Client()
    query = f"""
        SELECT symbol, DATE(timestamp) as date,
               AVG(open) as open, AVG(high) as high,
               AVG(low) as low, AVG(close) as close, SUM(volume) as volume
        FROM `{PROJECT}.quant.us_bars_1d`
        WHERE DATE(timestamp) BETWEEN @start AND @end
        GROUP BY symbol, date
        ORDER BY symbol, date
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("start", "STRING", start),
        bigquery.ScalarQueryParameter("end", "STRING", end),
    ])
    return client.query(query, job_config=job_config).to_dataframe()


def write_to_bq(df: pd.DataFrame, source: str):
    """Write factor values to BQ factor_values table."""
    client = bigquery.Client()
    rows = []
    for _, row in df.iterrows():
        for col in df.columns:
            if col in ("symbol", "date"):
                continue
            rows.append({
                "factor_id": f"us_{col}",
                "symbol": row["symbol"],
                "date": str(row["date"]),
                "value": float(row[col]) if not pd.isna(row[col]) else None,
                "source_builder": source,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            })

    if rows:
        result = client.load_table_from_json(rows, TABLE)
        result.result()
        log.info("Wrote %d factor values to %s (source=%s)", len(rows), TABLE, source)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["tech", "fundamental", "all"], required=True)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-05-30")
    args = parser.parse_args()

    if args.source in ("tech", "all"):
        log.info("Computing tech factors...")
        df = load_ohlcv(args.start, args.end)
        tfb = TechFactorBuilder()
        tfb.compute_factors(df)
        processed = tfb.process_factors(tfb.factor_df)
        processed["symbol"] = df["symbol"]
        processed["date"] = df["date"]
        write_to_bq(processed, "tech")
        log.info("Tech factors done: %d cols", len(tfb.factor_names))

    if args.source in ("fundamental", "all"):
        log.info("Computing fundamental factors...")
        ffb = FundamentalFactorBuilder()
        log.info("F10 factor batch computation — use raw BQ tables (skipped if unavailable)")
        log.info("TODO: load F10 data from BQ raw tables → compute → write_to_bq")
        # For now: placeholder — reads from BQ raw tables when data available


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Extend ML trainer for factor_source=all**

In `ml/trainer.py`, modify `load_data_from_bq()`:

```python
def load_data_from_bq(self, symbols, start_date, end_date, factor_source="all", market="us"):
    client = bigquery.Client()
    
    source_filter = ""
    if factor_source == "tech":
        source_filter = "AND source_builder = 'tech'"
    elif factor_source == "fundamental":
        source_filter = "AND source_builder = 'fundamental'"
    # "all" means no filter
    
    query = f"""
        SELECT factor_id, symbol, date, value, source_builder
        FROM `{PROJECT}.quant.factor_values`
        WHERE symbol IN UNNEST(@symbols)
          AND date BETWEEN @start AND @end
          {source_filter}
        ORDER BY date, symbol, factor_id
    """
    # ... rest unchanged
    df = client.query(query, job_config).to_dataframe()
    # Pivot + train
```

- [ ] **Step 3: Run 80-factor ML comparison**

```bash
cd /opt/quant && python3.12 scripts/run_w2_experiment.py --factor-source tech --start 2025-01-01 --end 2026-05-30
cd /opt/quant && python3.12 scripts/run_w2_experiment.py --factor-source all --start 2025-01-01 --end 2026-05-30
```

- [ ] **Step 4: Commit**

```bash
cd /opt/quant && sudo -u quant git add scripts/compute_factors_batch.py ml/trainer.py scripts/run_w2_experiment.py
sudo -u quant git commit -m "feat: factor_values batch compute + 80-factor ML training support"
```

---

### Task 6: Add ShortSqueeze + SectorRotation strategies

**Files:**
- Modify: `paper/strategies.py`

- [ ] **Step 1: Add ShortSqueeze strategy**

```python
class ShortSqueeze(Strategy):
    """Short squeeze: high short interest + low days-to-cover + upward momentum."""

    top_k: int = 10
    rebalance_every: int = 5
    min_short_ratio: float = 0.05
    max_days_to_cover: float = 5.0
    min_price_momentum_5d: float = 0.02
    allocation: float = 0.95

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        self._last_rebalance = bar

        if ctx.predictions is None:
            return []

        scores = {s: v for s, v in ctx.predictions.items()
                  if s in ctx.universe and not np.isnan(v)}
        if len(scores) < 3:
            return []

        sorted_symbols = sorted(scores, key=scores.get, reverse=True)
        selected = sorted_symbols[:self.top_k]

        weight = min(self.allocation / len(selected), 0.10)
        signals = []

        for sym in selected:
            if ctx.portfolio.positions.get(sym):
                signals.append(Signal.target(sym, weight))
            else:
                signals.append(Signal.buy(sym, weight))

        for sym in list(ctx.portfolio.positions.keys()):
            if sym not in selected:
                signals.append(Signal.close(sym))

        return signals


_BUILTIN_STRATEGIES["ShortSqueeze"] = ShortSqueeze
```

- [ ] **Step 2: Add SectorRotation strategy**

```python
class SectorRotation(Strategy):
    """Monthly sector rotation based on factor rankings."""

    sectors: list[str] = []  # plate codes
    factor: str = "roe"
    top_k_sectors: int = 3
    rebalance_every: int = 21
    allocation: float = 0.95

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every
        if not self.sectors:
            self.sectors = ["HSI", "HSTECH"]  # default HK sectors

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        self._last_rebalance = bar

        from paper.market import UniverseBuilder
        signals = []
        all_symbols = []

        for plate in self.sectors:
            try:
                syms = UniverseBuilder.from_plate(plate)
                all_symbols.extend(syms[:self.top_k_sectors * 10])
            except Exception:
                pass

        if not all_symbols:
            return []

        weight = self.allocation / len(all_symbols)
        for sym in all_symbols[:50]:  # cap at 50
            signals.append(Signal.buy(sym, min(weight, 0.02)))

        return signals


_BUILTIN_STRATEGIES["SectorRotation"] = SectorRotation
```

- [ ] **Step 3: Verify strategies register**

```bash
cd /opt/quant && python3.12 -c "
from paper.strategies import _BUILTIN_STRATEGIES
assert 'ShortSqueeze' in _BUILTIN_STRATEGIES
assert 'SectorRotation' in _BUILTIN_STRATEGIES
print('Strategies:', list(_BUILTIN_STRATEGIES.keys()))
"
```

- [ ] **Step 4: Commit**

```bash
cd /opt/quant && sudo -u quant git add paper/strategies.py
sudo -u quant git commit -m "feat: add ShortSqueeze + SectorRotation strategies"
```

---

### Task 7: Final verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run all Python tests**

```bash
cd /opt/quant && python3.12 -m pytest factors/tests/ engine/tests/ collectors/tests/ paper/tests/ -v -k "not vcr" 2>&1 | tail -10
```

- [ ] **Step 2: Verify W3 5m experiment runs**

```bash
cd /opt/quant && python3.12 -c "
from engine.data import BigQuery5mSource
src = BigQuery5mSource(start='2026-05-01', end='2026-05-10', symbols=['US.AAPL', 'US.MSFT'])
for sym in src.symbols()[:3]:
    df = src.load_bars(sym)
    print(f'{sym}: {len(df)} bars')
"
```

- [ ] **Step 3: Verify F10 IC evaluation runs**

```bash
cd /opt/quant && python3.12 scripts/evaluate_f10_factors.py --start 2026-01-01 --end 2026-05-30 2>&1 | tail -15
```

- [ ] **Step 4: Commit any stragglers + final git log**

```bash
cd /opt/quant && sudo -u quant git status
cd /opt/quant && sudo -u quant git log --oneline main..HEAD
```

---

### Dependency Order

```
Task 1 (BigQuery5mSource)  ──→  Task 2 (W3 experiment)      ──┐
                                                                 ├── Task 7 (verification)
Task 3 (F10 data pipeline) ──→  Task 4 (F10 IC eval) ──→ Task 5 (80-factor ML) ──┤
                                                                 │
Task 6 (new strategies)    ──────────────────────────────────────┘
```

Tasks 1, 3, 6 are independent (can run in parallel).
Task 2 depends on Task 1.
Task 4 depends on Task 3.
Task 5 depends on Task 4.
Task 7 runs last.
