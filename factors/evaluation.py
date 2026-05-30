"""Factor evaluation — Rank IC, IC decay, coverage, admission criteria."""
import numpy as np
import pandas as pd


def spearmanr(x, y):
    """Spearman rank correlation between two series, dropping NaN pairs."""
    mask = x.notna() & y.notna()
    if mask.sum() < 3:
        return np.nan
    return x[mask].rank().corr(y[mask].rank())


def compute_ic(factor_values, fwd_returns):
    """Compute Rank IC between factor values and forward returns."""
    return spearmanr(factor_values, fwd_returns)


def compute_ic_decay(factor_values, fwd_returns):
    """Compute IC across multiple forward horizons.
    
    fwd_returns: dict of {label: series}, e.g. {"1d": ..., "5d": ...}
    """
    return {k: compute_ic(factor_values, v) for k, v in fwd_returns.items()}


def compute_coverage(factor_values):
    """Fraction of non-NaN values."""
    return float(factor_values.notna().mean())


def evaluate_factor(factor_values, fwd_ret_1d, fwd_ret_5d, fwd_ret_20d):
    """Run full factor evaluation.
    
    Returns dict matching factor_evaluations schema.
    """
    ic = compute_ic(factor_values, fwd_ret_20d)
    t = len(factor_values.dropna())
    ic_tstat = abs(ic) * np.sqrt(t) if ic is not None and not np.isnan(ic) else 0.0

    ic_decay = compute_ic_decay(
        factor_values,
        {"1d": fwd_ret_1d, "5d": fwd_ret_5d, "20d": fwd_ret_20d},
    )
    coverage = compute_coverage(factor_values)
    skew = factor_values.skew()
    kurt = factor_values.kurtosis()

    # Admission check
    passes = True
    details = []
    if abs(ic) <= 0.05:
        passes = False
        details.append("ic_low")
    if ic_tstat <= 3.0:
        passes = False
        details.append("ic_insignificant")
    if coverage <= 0.90:
        passes = False
        details.append("coverage_low")

    decay20 = ic_decay.get("20d", np.nan)
    if not np.isnan(decay20) and abs(ic) > 0.03 and abs(decay20) < 0.01:
        details.append("ic_decay_reversal")

    return {
        "ic_mean": ic,
        "ic_std": np.nan,
        "ic_tstat": ic_tstat,
        "ic_ir": np.nan,
        "ic_decay_1d": ic_decay.get("1d"),
        "ic_decay_5d": ic_decay.get("5d"),
        "ic_decay_20d": decay20,
        "coverage": coverage,
        "skewness": None if np.isnan(skew) else float(skew),
        "kurtosis": None if np.isnan(kurt) else float(kurt),
        "passes_admission": passes,
        "admission_details": ",".join(details) if details else None,
    }
