"""Factor research utilities: factor definitions, IC computation, factor returns."""

from dataclasses import dataclass
from typing import Callable
import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class Factor:
    """A factor is a named function that transforms market data into a signal Series."""
    name: str
    fn: Callable[[pd.DataFrame], pd.Series]


def compute_ic(factors: list[Factor], forward_returns: pd.DataFrame,
               data_source) -> dict:
    """Compute Spearman rank IC for each factor vs forward returns.

    Args:
        factors: List of Factor definitions.
        forward_returns: DataFrame of forward N-period returns (same shape as close).
        data_source: The engine DataSource with .close DataFrame.

    Returns:
        Dict[str, pd.Series] mapping factor name to IC time series (one IC per date).
    """
    results = {}
    for factor in factors:
        try:
            factor_vals = factor.fn(data_source.close)
        except Exception:
            continue
        aligned = pd.DataFrame({
            "factor": factor_vals.stack(),
            "fwd_ret": forward_returns.stack(),
        }).dropna()

        if len(aligned) < 5:
            results[factor.name] = pd.Series(dtype=float)
            continue

        # Cross-sectional IC per date
        ic_series = {}
        for dt in aligned.index.get_level_values(0).unique():
            cross = aligned.loc[dt]
            if len(cross) < 3:
                continue
            ic, _ = stats.spearmanr(cross["factor"], cross["fwd_ret"])
            ic_series[dt] = ic if not np.isnan(ic) else 0.0

        results[factor.name] = pd.Series(ic_series, name=f"{factor.name}_IC")
    return results


def factor_returns(portfolio_returns: pd.Series, factor_exposures: pd.DataFrame) -> dict:
    """Run cross-sectional regression: portfolio returns ~ factor exposures.

    Args:
        portfolio_returns: T-length Series of portfolio returns.
        factor_exposures: T x F DataFrame of factor exposures.

    Returns:
        Dict with 'alpha', 'betas' (dict of factor→coefficient), 'r_squared'.
    """
    aligned = pd.concat([portfolio_returns, factor_exposures], axis=1).dropna()
    if len(aligned) < 10:
        return {"alpha": 0, "betas": {}, "r_squared": 0}

    y = aligned.iloc[:, 0].values
    X = aligned.iloc[:, 1:].values
    X = np.column_stack([np.ones(len(X)), X])  # add intercept

    coeffs, residuals, _, _ = np.linalg.lstsq(X, y, rcond=None)
    y_pred = X @ coeffs
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    return {
        "alpha": round(float(coeffs[0]), 6),
        "betas": {col: round(float(c), 4) for col, c in zip(factor_exposures.columns, coeffs[1:])},
        "r_squared": round(float(r2), 4),
    }
