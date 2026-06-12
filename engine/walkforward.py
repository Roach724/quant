"""Walk-forward cross-validation with rolling-origin windows."""

import pandas as pd
from engine.engine import Engine
from engine.metrics import summary
from engine.data import DataFrameSource


def _parse_window(window_str: str) -> int:
    """Convert '6M', '1Y', '3M' to approximate bar count (assumes daily data)."""
    n = int("".join(c for c in window_str if c.isdigit()))
    if "Y" in window_str.upper():
        return n * 252
    if "M" in window_str.upper():
        return n * 21
    if "W" in window_str.upper():
        return n * 5
    return n  # raw number


class WalkForward:
    """Rolling-origin walk-forward cross-validation.

    Splits data into consecutive train/test windows.
    For each window: train the strategy on in-sample data,
    test on out-of-sample data. Path-dependent strategies receive
    the trained portfolio state as warm-start.

    Args:
        strategy: An instantiated Strategy object (reused per fold).
        data: DataSource covering the full backtest period.
        config: BacktestConfig.
        train_window: Length of in-sample period (e.g., '6M', '1Y', or integer bars).
        test_window: Length of out-of-sample period.
        step_size: Step between folds (default: same as test_window).
    """

    def __init__(self, strategy, data, config, train_window="6M", test_window="1M", step_size=None):
        self.strategy = strategy
        self.data = data
        self.config = config
        self.train_len = _parse_window(train_window) if isinstance(train_window, str) else train_window
        self.test_len = _parse_window(test_window) if isinstance(test_window, str) else test_window
        self.step = step_size or self.test_len

    def run(self):
        """Run walk-forward and return list of dicts per fold."""
        n = len(self.data)
        folds = []
        start = 0
        while start + self.train_len + self.test_len <= n:
            train_end = start + self.train_len
            test_end = train_end + self.test_len

            train_data = self._slice(start, train_end)
            test_data = self._slice(train_end, test_end)

            train_result = Engine(self.config).run(self.strategy, train_data)
            # Pass final portfolio state from train to test for path-dependent strategies
            test_result = Engine(self.config).run(
                self.strategy, test_data,
                initial_portfolio=train_result.portfolio,
            )

            folds.append({
                "fold": len(folds),
                "train_start": str(self.data.timestamp[start]),
                "train_end": str(self.data.timestamp[train_end - 1]),
                "test_start": str(self.data.timestamp[train_end]),
                "test_end": str(self.data.timestamp[test_end - 1]),
                "train_metrics": summary(train_result),
                "test_metrics": summary(test_result),
            })
            start += self.step
        return folds

    def summary(self):
        """Aggregate out-of-sample metrics across all folds."""
        folds = self.run()
        if not folds:
            return {}
        keys = ["total_return", "annual_return", "sharpe_ratio", "max_drawdown", "win_rate"]
        agg = {}
        for k in keys:
            vals = [f["test_metrics"].get(k, 0) for f in folds]
            agg[k + "_mean"] = round(sum(vals) / len(vals), 4) if vals else 0
        agg["n_folds"] = len(folds)
        return agg

    def _slice(self, start: int, end: int):
        """Create a DataFrameSource slice of the data for a time window."""
        close = self.data.close.iloc[start:end].copy()
        pred = None
        if hasattr(self.data, 'pred') and self.data.pred is not None:
            pred = self.data.pred.iloc[start:end].copy()
        kwargs = {"close": close, "pred": pred}
        for field in ("open", "high", "low", "volume"):
            if hasattr(self.data, field) and getattr(self.data, field) is not None:
                kwargs[field] = getattr(self.data, field).iloc[start:end].copy()
        return DataFrameSource(**kwargs)
