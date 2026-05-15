"""Parameter optimization: grid search and random search over Strategy parameters."""

import itertools
import random
from engine.engine import Engine
from engine.metrics import summary


def _run_one(strategy_class, params, data, config):
    """Run a single backtest with given parameters, return (params, metrics_dict)."""
    s = strategy_class()
    for k, v in params.items():
        setattr(s, k, v)
    result = Engine(config).run(s, data)
    return dict(params), summary(result)


def _sort_by_metric(results, metric):
    return sorted(results, key=lambda x: x[1].get(metric, 0), reverse=True)


class GridSearch:
    """Exhaustive search over the cartesian product of param_grid values."""

    def __init__(self, strategy_class, param_grid, data, config, metric="sharpe_ratio"):
        self.strategy_class = strategy_class
        self.param_grid = param_grid
        self.data = data
        self.config = config
        self.metric = metric

    def run(self):
        keys = list(self.param_grid.keys())
        values = list(self.param_grid.values())
        results = []
        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            results.append(_run_one(self.strategy_class, params, self.data, self.config))
        return _sort_by_metric(results, self.metric)


class RandomSearch:
    """Random sampling of N parameter combinations."""

    def __init__(self, strategy_class, param_grid, data, config, n_iter=50, metric="sharpe_ratio"):
        self.strategy_class = strategy_class
        self.param_grid = param_grid
        self.data = data
        self.config = config
        self.n_iter = n_iter
        self.metric = metric

    def run(self):
        keys = list(self.param_grid.keys())
        results = []
        for _ in range(self.n_iter):
            params = {k: random.choice(self.param_grid[k]) for k in keys}
            results.append(_run_one(self.strategy_class, params, self.data, self.config))
        return _sort_by_metric(results, self.metric)
