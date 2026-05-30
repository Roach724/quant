import numpy as np
import pandas as pd
from factors.evaluation import compute_ic, compute_ic_decay, compute_coverage, evaluate_factor


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
    decay = compute_ic_decay(x, {
        "1d": pd.Series(np.random.randn(n)),
        "5d": pd.Series(np.random.randn(n)),
        "20d": pd.Series(np.random.randn(n)),
    })
    assert set(decay.keys()) == {"1d", "5d", "20d"}


def test_compute_coverage():
    x = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0])
    cov = compute_coverage(x)
    assert abs(cov - 0.8) < 0.01


def test_evaluate_factor_returns_all_keys():
    n = 200
    np.random.seed(42)
    fv = pd.Series(np.random.randn(n))
    f1 = pd.Series(np.random.randn(n))
    f5 = pd.Series(np.random.randn(n))
    f20 = pd.Series(np.random.randn(n))
    result = evaluate_factor(fv, f1, f5, f20)
    required = ["ic_mean", "ic_tstat", "ic_decay_1d", "ic_decay_5d", "ic_decay_20d",
                "coverage", "passes_admission"]
    for k in required:
        assert k in result, f"Missing key: {k}"
    assert isinstance(result["passes_admission"], bool)
