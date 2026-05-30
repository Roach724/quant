# Quant 下一阶段 — 统一实施计划

> **For agentic workers:** Use subagent-driven-development or executing-plans. Steps use checkbox syntax.

**Goal:** 实施 Factor Registry + Paper Runner W1，为 W2 ML 双策略铺路。

**Architecture:** Phase A (Factor Registry BQ双表+API) 和 Phase B (PaperRunner BQ数据源+SimpleMomentum) 可并行推进。

**Prerequisite:** us_bars_1d BQ 表就绪（回填 chain at-job 16:00 UTC）

---

## File Map

| File | Action |
|------|--------|
| sql/factor_registry_schema.sql | Create |
| factors/registry.py | Create |
| factors/evaluation.py | Create |
| scripts/init_factor_registry.py | Create |
| scripts/run_paper_momentum.sh | Create |
| factors/builder.py | Modify: +compute() |
| run_paper.py | Modify: BQ data loader |
| factors/tests/test_registry.py | Create |
| factors/tests/test_evaluation.py | Create |
| paper/tests/test_w1_integration.py | Create |

---

# Phase A: Factor Registry

## A1: BQ Tables

**Files:** Create sql/factor_registry_schema.sql

- [ ] **Step 1:** Write DDL (see sql/factor_registry_schema.sql in design doc section 3.2)
- [ ] **Step 2:** Execute `bq query --project_id=deductive-notch-495015-c2 --location=asia-east2 "$(cat sql/factor_registry_schema.sql)"`
- [ ] **Step 3:** Verify `bq ls quant | grep factor_`
- [ ] **Step 4:** Commit

## A2: FactorRegistry register + get_active

**Files:** Create factors/registry.py, factors/tests/test_registry.py

- [ ] **Step 1:** Write test_registry.py with test_register_inserts_row, test_get_active_returns_dataframe
- [ ] **Step 2:** Run pytest → FAIL
- [ ] **Step 3:** Implement FactorRegistry class with register(), get_active(), deactivate()
- [ ] **Step 4:** Run pytest → PASS
- [ ] **Step 5:** Commit

## A3: Factor Evaluation

**Files:** Create factors/evaluation.py, factors/tests/test_evaluation.py

- [ ] **Step 1:** Write tests: test_compute_ic_perfect, test_compute_ic_handles_nan, test_compute_ic_decay
- [ ] **Step 2:** Run pytest → FAIL
- [ ] **Step 3:** Implement spearmanr, compute_ic, compute_ic_decay, evaluate_factor
- [ ] **Step 4:** Run pytest → PASS
- [ ] **Step 5:** Commit

## A4: evaluate() on FactorRegistry

**Files:** Modify factors/registry.py

- [ ] **Step 1:** Add evaluate(), _write_evaluation(), _update_registry_snapshot(), _latest_eval_date() methods
- [ ] **Step 2:** Add test_evaluate_writes_to_bq to test_registry.py
- [ ] **Step 3:** Run pytest → PASS
- [ ] **Step 4:** Commit

## A5: FactorBuilder.compute()

**Files:** Modify factors/builder.py

- [ ] **Step 1:** Write test_compute_selects_only_requested_factors
- [ ] **Step 2:** Run pytest → FAIL
- [ ] **Step 3:** Add compute(factor_names, df) method
- [ ] **Step 4:** Run pytest → PASS
- [ ] **Step 5:** Commit

## A6: Init Script

**Files:** Create scripts/init_factor_registry.py

- [ ] **Step 1:** Write script that classifies 39 factors and calls registry.register()
- [ ] **Step 2:** Verify: python3.12 -c "..." shows 39 factors
- [ ] **Step 3:** Commit

## A7: Integration + Seed BQ

- [ ] **Step 1:** Run all factor tests: pytest factors/tests/ -v → PASS
- [ ] **Step 2:** Run init: python3.12 scripts/init_factor_registry.py
- [ ] **Step 3:** Verify BQ: bq query 'SELECT factor_id FROM quant.factor_registry LIMIT 5'
- [ ] **Step 4:** Commit

---

# Phase B: Paper Runner W1

## B1: BQ Data Source for PaperRunner

**Files:** Modify run_paper.py (replace _sdk_data)

- [ ] **Step 1:** Replace _sdk_data() with BQ direct OHLCV loader:
  - Query us_bars_1d with symbol/timestamp filter
  - Strip prefix (US./HK.) back from symbol names
  - Pivot close/open/high/low/volume, forward-fill
  - Return DataFrameSource
- [ ] **Step 2:** Create paper/tests/test_w1_integration.py: test_bq_data_loads_ohlcv
- [ ] **Step 3:** Run pytest → PASS
- [ ] **Step 4:** Commit

## B2: Launch Script

**Files:** Create scripts/run_paper_momentum.sh

- [ ] **Step 1:** Write bash script: run_paper.py --market us --data-source sdk --strategy SimpleMomentum
- [ ] **Step 2:** chmod +x
- [ ] **Step 3:** Commit

## B3: Manual Run & Verify

- [ ] **Step 1:** Run: bash scripts/run_paper_momentum.sh us 2026-01-01 2026-05-28 100000
- [ ] **Step 2:** Check output/ for report.html + investment_record.json
- [ ] **Step 3:** Verify: trades > 0, metrics generated, no exceptions
- [ ] **Step 4:** Add output/ to .gitignore, commit, push
