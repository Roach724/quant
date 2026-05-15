# Phase 2b: Parameter Optimization & Walk-Forward — Design

**Date:** 2026-05-16  
**Scope:** Extend quant.engine with optimization, walk-forward CV, and factor research

## Components

### 1. Optimizer (`engine/optimize.py`)
- `GridSearch(strategy_class, param_grid, data, config, metric)` — exhaustive
- `RandomSearch(strategy_class, param_grid, data, config, metric, n_iter)` — random sampling
- Both return `list[tuple[dict, dict]]` sorted by metric descending

### 2. Walk-Forward (`engine/walkforward.py`)
- `WalkForward(strategy, data, config, train_window, test_window, step_size)`
- Runs rolling-origin: train on in-sample, test on out-of-sample
- `run()` → list of fold results; `summary()` → aggregate OOS metrics

### 3. Factor Research (`engine/factors.py`)
- `Factor(name, fn)` — factor as a function of market data
- `compute_ic(factors, forward_returns, data)` — Spearman rank IC
- Wraps `scipy.stats.spearmanr` for IC computation
