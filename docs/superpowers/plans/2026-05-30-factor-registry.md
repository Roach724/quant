# Factor Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build FactorRegistry — a BQ-backed registration system for quant factors with admission criteria (IC>0.05, t-stat>3, coverage>90%)

**Architecture:** Two BQ tables (factor_registry + factor_evaluations) managed by a Python FactorRegistry class. Existing FactorBuilder gets a `compute(factor_names)` method. An init script seeds the registry with existing 39+ factors.

**Tech Stack:** Python 3.12, BigQuery, pandas, numpy, google-cloud-bigquery

---

## File Map

| File | Purpose |
|------|---------|
| `sql/factor_registry_schema.sql` | CREATE TABLE DDL for both BQ tables |
| `factors/registry.py` | FactorRegistry class — register, evaluate, query |
| `factors/evaluation.py` | IC/decay/correlation computation |
| `scripts/init_factor_registry.py` | Seed registry with existing 39+ factors |
| `factors/builder.py` | Modify: add `compute(factor_names, data)` |
| `factors/tests/test_registry.py` | Unit tests for FactorRegistry |
| `factors/tests/test_evaluation.py` | Unit tests for evaluation logic |

---

### Task 1: Create BQ Tables

**Files:**
- Create: `sql/factor_registry_schema.sql`

- [ ] **Step 1: Write DDL file**

```sql
-- Factor Registry Schema
CREATE TABLE IF NOT EXISTS quant.factor_registry (
    factor_id      STRING    NOT NULL,
    name           STRING    NOT NULL,
    market         STRING    NOT NULL,
    category       STRING,
    source         STRING,
    formula        STRING,
    description    STRING,
    is_active      BOOL      DEFAULT TRUE,
    admitted_at    TIMESTAMP,
    last_evaluated TIMESTAMP,
    created_by     STRING,
    latest_ic_mean     FLOAT64,
    latest_ic_tstat    FLOAT64,
    latest_coverage    FLOAT64,
    latest_eval_id     STRING,
    tags           ARRAY<STRING>,
    metadata       JSON
)
PARTITION BY DATE(admitted_at)
CLUSTER BY market, is_active;

CREATE TABLE IF NOT EXISTS quant.factor_evaluations (
    eval_id        STRING    NOT NULL,
    factor_id      STRING    NOT NULL,
    evaluated_at   TIMESTAMP NOT NULL,
    ic_mean        FLOAT64,
    ic_std         FLOAT64,
    ic_tstat       FLOAT64,
    ic_ir          FLOAT64,
    ic_decay_1d    FLOAT64,
    ic_decay_5d    FLOAT64,
    ic_decay_20d   FLOAT64,
    coverage       FLOAT64,
    skewness       FLOAT64,
    kurtosis       FLOAT64,
    top_correlated   ARRAY<STRING>,
    max_correlation  FLOAT64,
    passes_admission  BOOL,
    admission_details STRING,
    eval_period_start  DATE,
    eval_period_end    DATE,
    eval_market        STRING,
    data_version       STRING,
    metadata  JSON
)
PARTITION BY DATE(evaluated_at)
CLUSTER BY factor_id;
```

- [ ] **Step 2: Execute DDL in BQ**

```bash
bq query --project_id=deductive-notch-495015-c2 --location=asia-east2 "$(cat sql/factor_registry_schema.sql)"
```

- [ ] **Step 3: Verify tables exist**

```bash
bq ls --format=json quant | jq '.[] | select(.tableReference.tableId | startswith("factor_")) | .tableReference.tableId'
```
Expected: `factor_registry` and `factor_evaluations`.

- [ ] **Step 4: Commit**

```bash
git add sql/factor_registry_schema.sql
git commit -m "feat: factor registry BQ schema"
```

---

### Task 2: FactorRegistry — Register & Query

**Files:**
- Create: `factors/registry.py`
- Create: `factors/tests/test_registry.py`

- [ ] **Step 1: Write failing test**

`factors/tests/test_registry.py`:
```python
import pytest
from unittest.mock import patch
import pandas as pd
from factors.registry import FactorRegistry

@pytest.fixture
def registry():
    return FactorRegistry(project="test-project")

def test_register_inserts_row(registry):
    with patch.object(registry, '_client') as mock_client:
        mock_client.query.return_value.result.return_value = None
        result = registry.register(
            factor_id="us_momentum_20d", name="20日动量",
            market="us", source="Alpha158",
            formula="factors/builder.py::momentum_20d",
            category="momentum", tags=["trend"],
        )
        assert result is True
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest factors/tests/test_registry.py::test_register_inserts_row -v
```
Expected: FAIL (ImportError).

- [ ] **Step 3: Write FactorRegistry**

`factors/registry.py`:
```python
import os
import logging
from datetime import datetime, timezone
import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger(__name__)
PROJECT = os.environ.get("GCP_PROJECT", "deductive-notch-495015-c2")
DATASET = "quant"

class FactorRegistry:
    def __init__(self, project=PROJECT, dataset=DATASET):
        self.project = project
        self.dataset = dataset
        self._client = bigquery.Client(project=project)

    def register(self, factor_id, name, market, source=None,
                 formula=None, category=None, description=None, tags=None):
        row = {"factor_id": factor_id, "name": name, "market": market,
               "category": category, "source": source, "formula": formula,
               "description": description, "is_active": True,
               "admitted_at": datetime.now(timezone.utc).isoformat(),
               "tags": tags or []}
        table_ref = f"{self.project}.{self.dataset}.factor_registry"
        errors = self._client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error("Register failed for %s: %s", factor_id, errors)
            return False
        return True

    def get_active(self, market="us"):
        query = f"""
            SELECT * FROM `{self.project}.{self.dataset}.factor_registry`
            WHERE market = @market AND is_active = TRUE
            ORDER BY latest_ic_mean DESC
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("market", "STRING", market)])
        return self._client.query(query, job_config=job_config).to_dataframe()

    def deactivate(self, factor_id, reason=None):
        query = f"""
            UPDATE `{self.project}.{self.dataset}.factor_registry`
            SET is_active = FALSE WHERE factor_id = @factor_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("factor_id", "STRING", factor_id)])
        self._client.query(query, job_config=job_config).result()
        logger.info("Deactivated: %s (%s)", factor_id, reason)
        return True
```

- [ ] **Step 4: Run test**

```bash
pytest factors/tests/test_registry.py::test_register_inserts_row -v
```
Expected: PASS.

- [ ] **Step 5: Add get_active test**

Append to `factors/tests/test_registry.py`:
```python
def test_get_active_returns_dataframe(registry):
    mock_df = pd.DataFrame({"factor_id": ["us_momentum_20d", "us_vol_10d"],
                            "is_active": [True, True], "latest_ic_mean": [0.06, 0.04]})
    with patch.object(registry._client, 'query') as mock_query:
        mock_query.return_value.to_dataframe.return_value = mock_df
        result = registry.get_active("us")
        assert len(result) == 2
        assert result.iloc[0]["factor_id"] == "us_momentum_20d"
```

- [ ] **Step 6: Run test**

```bash
pytest factors/tests/test_registry.py::test_get_active_returns_dataframe -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add factors/registry.py factors/tests/test_registry.py
git commit -m "feat: FactorRegistry register + get_active + deactivate"
```

---

### Task 3: Factor Evaluation Logic

**Files:**
- Create: `factors/evaluation.py`
- Create: `factors/tests/test_evaluation.py`

- [ ] **Step 1: Write tests**

`factors/tests/test_evaluation.py`:
```python
import numpy as np
import pandas as pd
from factors.evaluation import compute_ic, compute_ic_decay, evaluate_factor

def test_compute_ic_perfect_positive():
    n = 100
    x = pd.Series(np.arange(n))
    y = pd.Series(np.arange(n))
    assert abs(compute_ic(x, y) - 1.0) < 0.01

def test_compute_ic_negative():
    n = 100
    x = pd.Series(np.arange(n))
    y = pd.Series(np.arange(n)[::-1])
    assert abs(compute_ic(x, y) + 1.0) < 0.01

def test_compute_ic_handles_nan():
    x = pd.Series([1, 2, np.nan, 4, 5])
    y = pd.Series([2, 3, 4, 5, 6])
    ic = compute_ic(x, y)
    assert not np.isnan(ic)

def test_compute_ic_decay_shape():
    n = 100
    x = pd.Series(np.random.randn(n))
    decay = compute_ic_decay(x, {"1d": pd.Series(np.random.randn(n)),
                                  "5d": pd.Series(np.random.randn(n)),
                                  "20d": pd.Series(np.random.randn(n))})
    assert set(decay.keys()) == {"1d", "5d", "20d"}
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest factors/tests/test_evaluation.py -v
```
Expected: FAIL (ImportError).

- [ ] **Step 3: Write evaluation module**

`factors/evaluation.py`:
```python
import numpy as np
import pandas as pd

def spearmanr(x, y):
    mask = x.notna() & y.notna()
    if mask.sum() < 10:
        return np.nan
    return x[mask].rank().corr(y[mask].rank())

def compute_ic(factor_values, fwd_returns):
    return spearmanr(factor_values, fwd_returns)

def compute_ic_decay(factor_values, fwd_returns):
    return {k: compute_ic(factor_values, v) for k, v in fwd_returns.items()}

def compute_coverage(factor_values):
    return factor_values.notna().mean()

def evaluate_factor(factor_values, fwd_ret_1d, fwd_ret_5d, fwd_ret_20d):
    ic = compute_ic(factor_values, fwd_ret_20d)
    t = len(factor_values.dropna())
    ic_tstat = abs(ic) * np.sqrt(t) if ic and not np.isnan(ic) else 0.0
    ic_decay = compute_ic_decay(factor_values, {"1d": fwd_ret_1d, "5d": fwd_ret_5d, "20d": fwd_ret_20d})
    coverage = compute_coverage(factor_values)
    passes = True
    details = []
    if abs(ic) <= 0.05:
        passes = False; details.append("ic_low")
    if ic_tstat <= 3.0:
        passes = False; details.append("ic_insignificant")
    if coverage <= 0.90:
        passes = False; details.append("coverage_low")
    decay20 = ic_decay.get("20d", np.nan)
    if not np.isnan(decay20) and abs(ic) > 0.03 and abs(decay20) < 0.01:
        details.append("ic_decay_reversal")
    return {"ic_mean": ic, "ic_std": np.nan, "ic_tstat": ic_tstat, "ic_ir": np.nan,
            "ic_decay_1d": ic_decay.get("1d"), "ic_decay_5d": ic_decay.get("5d"),
            "ic_decay_20d": decay20, "coverage": coverage,
            "skewness": factor_values.skew(), "kurtosis": factor_values.kurtosis(),
            "passes_admission": passes,
            "admission_details": ",".join(details) if details else None}
```

- [ ] **Step 4: Run tests**

```bash
pytest factors/tests/test_evaluation.py -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add factors/evaluation.py factors/tests/test_evaluation.py
git commit -m "feat: factor evaluation — IC, decay, coverage, admission check"
```

---

### Task 4: evaluate() Method on FactorRegistry

**Files:**
- Modify: `factors/registry.py`

- [ ] **Step 1: Append evaluate methods to FactorRegistry**

Append to `factors/registry.py`:
```python
    def evaluate(self, factor_id, factor_values, fwd_ret_1d, fwd_ret_5d,
                 fwd_ret_20d, eval_period_start=None, eval_period_end=None, force=False):
        from factors.evaluation import evaluate_factor as _eval
        if not force:
            latest = self._latest_eval_date(factor_id)
            if latest and (datetime.now(timezone.utc) - latest).days < 30:
                logger.info("Skipping %s: evaluated recently", factor_id)
                return None
        result = _eval(factor_values, fwd_ret_1d, fwd_ret_5d, fwd_ret_20d)
        eval_id = f"{factor_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self._write_evaluation(eval_id=eval_id, factor_id=factor_id,
                               eval_period_start=eval_period_start,
                               eval_period_end=eval_period_end, **result)
        self._update_registry_snapshot(factor_id, eval_id, result["ic_mean"],
                                       result["ic_tstat"], result["coverage"])
        if not result["passes_admission"]:
            self.deactivate(factor_id, result["admission_details"])
        return result

    def _write_evaluation(self, eval_id, factor_id, ic_mean=None, ic_std=None,
                          ic_tstat=None, ic_ir=None, ic_decay_1d=None, ic_decay_5d=None,
                          ic_decay_20d=None, coverage=None, skewness=None, kurtosis=None,
                          max_correlation=None, passes_admission=False,
                          admission_details=None, eval_period_start=None,
                          eval_period_end=None):
        row = {"eval_id": eval_id, "factor_id": factor_id,
               "evaluated_at": datetime.now(timezone.utc).isoformat(),
               "ic_mean": ic_mean, "ic_std": ic_std, "ic_tstat": ic_tstat,
               "ic_ir": ic_ir, "ic_decay_1d": ic_decay_1d, "ic_decay_5d": ic_decay_5d,
               "ic_decay_20d": ic_decay_20d, "coverage": coverage,
               "skewness": skewness, "kurtosis": kurtosis,
               "max_correlation": max_correlation, "passes_admission": passes_admission,
               "admission_details": admission_details,
               "eval_period_start": eval_period_start,
               "eval_period_end": eval_period_end}
        table_ref = f"{self.project}.{self.dataset}.factor_evaluations"
        errors = self._client.insert_rows_json(table_ref, [row])
        return not bool(errors)

    def _update_registry_snapshot(self, factor_id, eval_id, ic_mean, ic_tstat, coverage):
        query = f"""
            UPDATE `{self.project}.{self.dataset}.factor_registry`
            SET latest_ic_mean=@ic_mean, latest_ic_tstat=@ic_tstat,
                latest_coverage=@coverage, latest_eval_id=@eval_id,
                last_evaluated=@now
            WHERE factor_id=@factor_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("ic_mean", "FLOAT64", ic_mean),
            bigquery.ScalarQueryParameter("ic_tstat", "FLOAT64", ic_tstat),
            bigquery.ScalarQueryParameter("coverage", "FLOAT64", coverage),
            bigquery.ScalarQueryParameter("eval_id", "STRING", eval_id),
            bigquery.ScalarQueryParameter("now", "STRING", datetime.now(timezone.utc).isoformat()),
            bigquery.ScalarQueryParameter("factor_id", "STRING", factor_id),
        ])
        self._client.query(query, job_config=job_config).result()

    def _latest_eval_date(self, factor_id):
        query = f"""
            SELECT MAX(evaluated_at) as latest
            FROM `{self.project}.{self.dataset}.factor_evaluations`
            WHERE factor_id=@factor_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("factor_id", "STRING", factor_id)])
        rows = list(self._client.query(query, job_config=job_config))
        return rows[0].latest if rows else None
```

- [ ] **Step 2: Add test**

`factors/tests/test_registry.py` (append):
```python
def test_evaluate_writes_to_bq(registry):
    import numpy as np
    n = 200
    fv = pd.Series(np.random.randn(n))
    f1 = pd.Series(np.random.randn(n))
    f5 = pd.Series(np.random.randn(n))
    f20 = pd.Series(np.random.randn(n))
    with patch.object(registry._client, 'query') as mq, \
         patch.object(registry._client, 'insert_rows_json') as mi:
        mq.return_value.result.return_value = None
        mi.return_value = []
        result = registry.evaluate("us_momentum_20d", fv, f1, f5, f20, force=True)
        assert result is not None
        assert "ic_mean" in result
```

- [ ] **Step 3: Run test**

```bash
pytest factors/tests/test_registry.py::test_evaluate_writes_to_bq -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add factors/registry.py factors/tests/test_registry.py
git commit -m "feat: FactorRegistry.evaluate() — write evaluations to BQ"
```

---

### Task 5: FactorBuilder.compute()

**Files:**
- Modify: `factors/builder.py`

- [ ] **Step 1: Write test**

`factors/tests/test_builder.py` (append):
```python
def test_compute_selects_only_requested_factors():
    from factors.builder import FactorBuilder
    import pandas as pd
    fb = FactorBuilder()
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=50, freq="B"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000000})
    result = fb.compute(["ret_1d", "vol_5d"], df)
    assert "ret_1d" in result.columns
    assert "vol_5d" in result.columns
    assert "ret_5d" not in result.columns
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest factors/tests/test_builder.py::test_compute_selects_only_requested_factors -v
```
Expected: FAIL (AttributeError).

- [ ] **Step 3: Add compute() to FactorBuilder**

Append to `factors/builder.py` after `compute_factors()`:
```python
    def compute(self, factor_names, df):
        """Compute only requested factors."""
        all_factors = self.compute_factors(df)
        available = [c for c in factor_names if c in all_factors.columns]
        return all_factors[available]
```

- [ ] **Step 4: Run test**

```bash
pytest factors/tests/test_builder.py::test_compute_selects_only_requested_factors -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add factors/builder.py factors/tests/test_builder.py
git commit -m "feat: FactorBuilder.compute() — selective factor computation"
```

---

### Task 6: Init Script

**Files:**
- Create: `scripts/init_factor_registry.py`

- [ ] **Step 1: Write script**

`scripts/init_factor_registry.py`:
```python
#!/usr/bin/env python3.12
"""Seed factor_registry from FactorBuilder."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from factors.builder import FactorBuilder
from factors.registry import FactorRegistry

CATEGORY_MAP = {
    "ret_": "return", "vol_": "volatility", "vol_ratio": "volume",
    "corr_vp": "volume", "vol_trend": "volume", "rsi": "momentum",
    "macd": "momentum", "bb_": "momentum", "price_position": "momentum",
    "streak": "momentum", "avg_turnover": "turnover",
    "turnover_": "turnover", "daily_range": "price_pattern",
    "upper_shadow": "price_pattern", "lower_shadow": "price_pattern",
    "gap": "price_pattern", "vp_divergence": "price_pattern",
    "skew": "higher_moment", "kurt": "higher_moment", "hk_": "hk_specific",
}

def classify(name):
    for prefix, cat in CATEGORY_MAP.items():
        if name.startswith(prefix):
            return cat
    return "other"

def main():
    fb = FactorBuilder()
    dummy = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=100, freq="B"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000000})
    fb.compute_factors(dummy)
    registry = FactorRegistry()
    for name in fb.factor_names:
        registry.register(factor_id=f"us_{name}", name=name.replace("_", " ").title(),
                          market="us", source="Alpha158",
                          formula=f"factors/builder.py (group: {classify(name)})",
                          category=classify(name))
    print(f"Registered {len(fb.factor_names)} factors.")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify factor_names**

```bash
cd /opt/quant && python3.12 -c "
from factors.builder import FactorBuilder; import pandas as pd
fb = FactorBuilder()
fb.compute_factors(pd.DataFrame({'date': pd.date_range('2024-01-01',periods=100,freq='B'),
    'open':100.0,'high':101.0,'low':99.0,'close':100.5,'volume':1000000}))
print(f'{len(fb.factor_names)} factors')
"
```
Expected: 39 factors.

- [ ] **Step 3: Commit**

```bash
git add scripts/init_factor_registry.py
git commit -m "feat: init script to seed factor_registry from FactorBuilder"
```

---

### Task 7: Integration Test & Final Push

- [ ] **Step 1: End-to-end test**

`factors/tests/test_integration.py` (update):
```python
def test_pipeline_register_evaluate():
    from factors.builder import FactorBuilder
    from factors.evaluation import evaluate_factor
    import pandas as pd, numpy as np
    fb = FactorBuilder()
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=500, freq="B"),
        "open": 100+np.cumsum(np.random.randn(500)*0.5),
        "high": 102+np.cumsum(np.random.randn(500)*0.5),
        "low": 98+np.cumsum(np.random.randn(500)*0.5),
        "close": 101+np.cumsum(np.random.randn(500)*0.5),
        "volume": 1000000+np.cumsum(np.random.randint(-10000,10000,500))})
    factors = fb.compute_factors(df)
    result = evaluate_factor(factors["ret_20d"], factors["fwd_ret_5d"],
                             factors["fwd_ret_5d"], factors["fwd_ret_20d"])
    assert "ic_mean" in result
    assert "passes_admission" in result
    assert isinstance(result["passes_admission"], bool)
```

- [ ] **Step 2: Run integration test**

```bash
pytest factors/tests/test_integration.py::test_pipeline_register_evaluate -v
```
Expected: PASS.

- [ ] **Step 3: Run full test suite**

```bash
pytest factors/tests/ -v
```
Expected: All tests PASS.

- [ ] **Step 4: Commit & push**

```bash
git add -A
git commit -m "feat: complete Factor Registry implementation with tests"
git push origin feature/futu-integration
```
