"""
因子计算引擎 — Alpha158 等价因子 + 港股特色因子

Ported from hk-quant/src/factor_builder.py (Phase 2 Factor Engineering).
Stripped of HKDataPipeline/Engine/Strategy/Portfolio dependencies.
Uses data_loader callback for OHLCV fetching and parquet save/load.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


def _spearmanr(x: pd.Series, y: pd.Series) -> float:
    """Spearman rank correlation with scipy fallback to numpy."""
    try:
        from scipy.stats import spearmanr as _sp

        r, _ = _sp(x, y)
        return r
    except ImportError:
        # Fallback: rank then use Pearson correlation
        rx = x.rank()
        ry = y.rank()
        return rx.corr(ry)

logger = logging.getLogger(__name__)


class TechFactorBuilder:
    """因子计算引擎 — compute 39 factors + 2 forward-return labels from OHLCV data.

    Usage::

        fb = TechFactorBuilder()
        factors = fb.compute_factors(ohlcv_df)          # single stock
        processed = fb.process_factors(factors)          # winsorize + standardize
        dataset = fb.build_factor_dataset(symbols, start, end, my_loader)  # batch
        fb.save_factors(dataset, "factors.parquet")
        loaded = TechFactorBuilder.load_factors("factors.parquet")
    """

    # ── Factor column sets (used by tests and introspection) ──────────

    RETURN_COLS = ["ret_1d", "ret_5d", "ret_10d", "ret_20d", "ret_60d", "ret_120d"]
    VOLATILITY_COLS = ["vol_5d", "vol_10d", "vol_20d", "vol_60d"]
    VOLUME_COLS = ["vol_ratio_5d", "vol_ratio_20d", "corr_vp_20d", "vol_trend"]
    MOMENTUM_COLS = [
        "rsi_14", "macd", "macd_signal", "macd_hist",
        "bb_position", "bb_width", "price_position_20d", "streak",
    ]
    TURNOVER_COLS = ["avg_turnover_5d", "avg_turnover_20d", "turnover_ratio", "turnover_growth"]
    PATTERN_COLS = ["daily_range", "upper_shadow_ratio", "lower_shadow_ratio", "gap", "vp_divergence"]
    SKEW_KURT_COLS = [
        "skew_20d", "kurt_20d", "skew_60d", "kurt_60d", "skew_120d", "kurt_120d",
    ]
    HK_COLS = ["low_vol_proxy", "price_stability"]
    LABEL_COLS = ["fwd_ret_5d", "fwd_ret_20d"]

    ALL_FACTOR_COLS = (
        RETURN_COLS + VOLATILITY_COLS + VOLUME_COLS + MOMENTUM_COLS
        + TURNOVER_COLS + PATTERN_COLS + SKEW_KURT_COLS + HK_COLS
    )

    def __init__(self):
        self.factor_names: list[str] = []

    # ── Alpha158 equivalent factors ───────────────────────────────────────

    @staticmethod
    def _returns(close: pd.Series, periods: list[int] | None = None) -> pd.DataFrame:
        """多周期收益率因子."""
        if periods is None:
            periods = [1, 5, 10, 20, 60, 120]
        df = pd.DataFrame(index=close.index)
        for p in periods:
            df[f"ret_{p}d"] = close.pct_change(p)
        return df

    @staticmethod
    def _volatility(close: pd.Series, periods: list[int] | None = None) -> pd.DataFrame:
        """多周期波动率因子."""
        if periods is None:
            periods = [5, 10, 20, 60]
        df = pd.DataFrame(index=close.index)
        ret = close.pct_change()
        for p in periods:
            df[f"vol_{p}d"] = ret.rolling(p).std()
        return df

    @staticmethod
    def _volume_factors(volume: pd.Series, close: pd.Series) -> pd.DataFrame:
        """成交量因子."""
        df = pd.DataFrame(index=volume.index)

        vol_ma20 = volume.rolling(20).mean()
        df["vol_ratio_5d"] = volume.rolling(5).mean() / vol_ma20.replace(0, np.nan)
        df["vol_ratio_20d"] = volume / vol_ma20.replace(0, np.nan)

        # 量价相关性 (20d)
        df["corr_vp_20d"] = volume.rolling(20).corr(close)

        # 成交量趋势 (5d vs 20d)
        df["vol_trend"] = volume.rolling(5).mean() - volume.rolling(20).mean()
        df["vol_trend"] = df["vol_trend"] / volume.rolling(20).std().replace(0, np.nan)

        return df

    @staticmethod
    def _momentum_factors(close: pd.Series, high: pd.Series, low: pd.Series) -> pd.DataFrame:
        """动量与技术因子."""
        df = pd.DataFrame(index=close.index)

        # RSI (14d)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi_14"] = 100 - 100 / (1 + rs)

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["macd"] = ema12 - ema26
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        # 布林带位置
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        df["bb_position"] = (close - ma20) / (std20.replace(0, np.nan) * 2)
        df["bb_width"] = (std20 * 2) / ma20.replace(0, np.nan)

        # 价格位置 (近20日)
        high20 = high.rolling(20).max()
        low20 = low.rolling(20).min()
        df["price_position_20d"] = (close - low20) / (high20 - low20).replace(0, np.nan)

        # 连涨/连跌天数
        sign = np.sign(delta)
        df["streak"] = sign.groupby((sign != sign.shift()).cumsum()).cumcount() + 1
        df["streak"] = df["streak"] * sign

        return df

    @staticmethod
    def _turnover_factors(volume: pd.Series, close: pd.Series) -> pd.DataFrame:
        """换手率与流动性因子 (基于成交额代理)."""
        df = pd.DataFrame(index=volume.index)

        turnover = volume * close

        df["avg_turnover_5d"] = turnover.rolling(5).mean()
        df["avg_turnover_20d"] = turnover.rolling(20).mean()
        df["turnover_ratio"] = (
            df["avg_turnover_5d"] / df["avg_turnover_20d"].replace(0, np.nan)
        )
        df["turnover_growth"] = turnover.pct_change(20)

        return df

    @staticmethod
    def _price_patterns(
        open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
    ) -> pd.DataFrame:
        """价格形态因子."""
        df = pd.DataFrame(index=close.index)

        # 日内振幅
        df["daily_range"] = (high - low) / close

        # 上影线 / 下影线
        body = (close - open_).abs()
        upper_shadow = high - close.clip(lower=open_)
        lower_shadow = close.clip(upper=open_) - low
        df["upper_shadow_ratio"] = upper_shadow / body.replace(0, np.nan)
        df["lower_shadow_ratio"] = lower_shadow / body.replace(0, np.nan)

        # 跳空缺口
        df["gap"] = open_ / close.shift(1) - 1

        # 量价背离 (5d price vs volume direction)
        price_dir_5d = np.sign(close.pct_change(5))
        vol_dir_5d = np.sign(close.rolling(5).mean().pct_change(5))
        df["vp_divergence"] = (price_dir_5d != vol_dir_5d).astype(float)

        return df

    @staticmethod
    def _skew_kurt(close: pd.Series, periods: list[int] | None = None) -> pd.DataFrame:
        """高阶矩因子 (偏度 + 峰度)."""
        if periods is None:
            periods = [20, 60, 120]
        df = pd.DataFrame(index=close.index)
        ret = close.pct_change()
        for p in periods:
            if p < 3:
                continue
            roll = ret.rolling(p)
            df[f"skew_{p}d"] = roll.skew()
            df[f"kurt_{p}d"] = roll.kurt()
        return df

    # ── HK characteristic factors ───────────────────────────────────────

    @staticmethod
    def _hk_dividend_yield(close: pd.Series) -> pd.DataFrame:
        """股息率代理因子.

        注: 实际股息数据需外部数据源。
        此处用价格稳定性作为高分红倾向代理。
        """
        df = pd.DataFrame(index=close.index)
        ret = close.pct_change()
        df["low_vol_proxy"] = -ret.rolling(60).std()
        df["price_stability"] = -(close.pct_change(60).abs())
        return df

    # ── Main pipeline ───────────────────────────────────────────────────

    def compute_factors(self, df: pd.DataFrame) -> pd.DataFrame:
        """对单只股票计算全部因子.

        Args:
            df: Standard OHLCV DataFrame with columns:
                ``date``, ``open``, ``high``, ``low``, ``close``, ``volume``.

        Raises:
            ValueError: If required columns (date, open, high, low, close, volume) are missing.

        Returns:
            Factor DataFrame indexed by date with 39 factor columns + 2 labels.
        """
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        result = pd.DataFrame(index=pd.DatetimeIndex(df["date"]))

        close = df.set_index("date")["close"]
        open_ = df.set_index("date")["open"]
        high = df.set_index("date")["high"]
        low = df.set_index("date")["low"]
        volume = df.set_index("date")["volume"]

        # 1. Returns (6)
        result = result.join(self._returns(close))
        # 2. Volatility (4)
        result = result.join(self._volatility(close))
        # 3. Volume (4)
        result = result.join(self._volume_factors(volume, close))
        # 4. Momentum/Technical (8)
        result = result.join(self._momentum_factors(close, high, low))
        # 5. Turnover (4)
        result = result.join(self._turnover_factors(volume, close))
        # 6. Price patterns (5)
        result = result.join(self._price_patterns(open_, high, low, close))
        # 7. Higher moments (6)
        result = result.join(self._skew_kurt(close))
        # 8. HK-specific (2)
        result = result.join(self._hk_dividend_yield(close))

        # 9. Forward returns (labels for ML)
        result["fwd_ret_5d"] = close.pct_change(-5)
        result["fwd_ret_20d"] = close.pct_change(-20)

        # Track factor names (exclude labels)
        self.factor_names = [c for c in result.columns if not c.startswith("fwd_")]
        return result

    def compute(self, factor_names: list[str], df: pd.DataFrame) -> pd.DataFrame:
        """Compute only the requested factors for a single stock.

        Args:
            factor_names: List of factor column names to compute, e.g. ["ret_1d", "vol_5d"].
            df: OHLCV DataFrame with date, open, high, low, close, volume columns.

        Returns:
            Factor DataFrame with only the requested columns.
        """
        all_factors = self.compute_factors(df)
        available = [c for c in factor_names if c in all_factors.columns]
        # Always include label columns for ML training
        for label in ["fwd_ret_5d", "fwd_ret_20d"]:
            if label in all_factors.columns and label not in available:
                available.append(label)
        return all_factors[available]

    def process_factors(
        self,
        factor_df: pd.DataFrame,
        winsor_pct: float = 0.01,
    ) -> pd.DataFrame:
        """因子处理 Pipeline: 去极值 → 标准化 → 缺失值填充.

        Args:
            factor_df: Raw factor DataFrame from ``compute_factors``.
            winsor_pct: Two-sided clip percentile (e.g. 0.01 clips top/bottom 1%).

        Returns:
            Processed factor DataFrame (winsorized + z-scored + NaN→0).
        """
        df = factor_df.copy()

        # Identify factor columns (exclude labels, symbol, date)
        factor_cols = [
            c for c in df.columns
            if c not in ("fwd_ret_5d", "fwd_ret_20d", "symbol", "date")
        ]

        # 1. Winsorization (cross-sectional)
        for col in factor_cols:
            lo = df[col].quantile(winsor_pct)
            hi = df[col].quantile(1 - winsor_pct)
            df[col] = df[col].clip(lo, hi)

        # 2. Z-score standardization
        for col in factor_cols:
            mean = df[col].mean()
            std = df[col].std()
            if std is not None and not pd.isna(std) and std > 1e-8:
                df[col] = (df[col] - mean) / std

        # 3. Fill remaining NaN with 0
        df[factor_cols] = df[factor_cols].fillna(0)

        return df

    def build_factor_dataset(
        self,
        symbols: list[str],
        start: str,
        end: str,
        data_loader: Callable[[str, str, str], pd.DataFrame],
    ) -> pd.DataFrame:
        """批量构建多股票因子数据集.

        Args:
            symbols: List of stock symbols.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).
            data_loader: Function ``(symbol, start, end) -> pd.DataFrame``
                returning an OHLCV DataFrame with columns
                ``date, open, high, low, close, volume``.

        Returns:
            Combined factor DataFrame with ``symbol`` and ``date`` columns.
        """
        all_factors: list[pd.DataFrame] = []
        n = len(symbols)

        for i, sym in enumerate(symbols):
            try:
                df = data_loader(sym, start, end)
                if df is None or len(df) == 0:
                    continue
                # Parse dates if they are strings
                if not pd.api.types.is_datetime64_any_dtype(df["date"]):
                    df = df.copy()
                    df["date"] = pd.to_datetime(df["date"])
                # Filter date range
                df = df[(df["date"] >= start) & (df["date"] <= end)]
                if len(df) < 200:
                    continue

                factors = self.compute_factors(df)
                factors["symbol"] = sym
                factors["date"] = df["date"].values
                all_factors.append(factors)

                if (i + 1) % 50 == 0:
                    logger.info("  Factors: %d/%d stocks processed", i + 1, n)

            except Exception:
                logger.debug("  %s: failed", sym, exc_info=True)
                continue

        if not all_factors:
            return pd.DataFrame()

        combined = pd.concat(all_factors, ignore_index=True)
        logger.info(
            "Factor dataset: %d rows x %d cols, %d stocks",
            len(combined), len(combined.columns), combined["symbol"].nunique(),
        )
        return combined

    @staticmethod
    def save_factors(factor_df: pd.DataFrame, path: str) -> None:
        """Save factor DataFrame to Parquet."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        factor_df.to_parquet(str(out), index=False)
        logger.info("Saved factors to %s", out)

    @staticmethod
    def load_factors(path: str) -> pd.DataFrame:
        """Load factor DataFrame from Parquet."""
        return pd.read_parquet(path)

    # ── IC analysis ────────────────────────────────────────────────────

    def compute_ic(
        self, factor_df: pd.DataFrame, label_col: str = "fwd_ret_5d"
    ) -> pd.DataFrame:
        """Compute cross-sectional Rank IC per date.

        For each date, computes Spearman rank correlation between each
        factor column and the forward return label.

        Args:
            factor_df: Cross-sectional factor DataFrame with columns:
                date, symbol, factor columns, label columns.
            label_col: Target label column (default: ``fwd_ret_5d``).

        Returns:
            DataFrame with columns: date, factor, rank_ic.
            Dates with fewer than 30 stocks are skipped.
        """
        factor_cols = [
            c
            for c in factor_df.columns
            if c not in ("symbol", "date", "fwd_ret_5d", "fwd_ret_20d")
        ]

        records: list[dict] = []
        for date_val, group in factor_df.groupby("date"):
            if len(group) < 30:
                continue
            for col in factor_cols:
                if col not in group.columns or group[col].isna().all():
                    continue
                valid = group[[col, label_col]].dropna()
                if len(valid) < 30:
                    continue
                ic = _spearmanr(valid[col], valid[label_col])
                records.append({"date": date_val, "factor": col, "rank_ic": ic})

        return pd.DataFrame(records)

    def ic_summary(self, ic_df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate IC statistics across factors.

        Args:
            ic_df: DataFrame from ``compute_ic()`` with columns date, factor, rank_ic.

        Returns:
            DataFrame sorted by abs_mean_ic descending, with columns:
            factor, mean, std, count, icir, abs_mean_ic.
        """
        if ic_df.empty:
            return pd.DataFrame(columns=["factor", "mean", "std", "count", "icir", "abs_mean_ic"])

        summary = (
            ic_df.groupby("factor")["rank_ic"]
            .agg(["mean", "std", "count"])
            .reset_index()
        )
        summary["icir"] = summary["mean"] / summary["std"].replace(0, np.nan)
        summary["abs_mean_ic"] = summary["mean"].abs()
        summary = summary.sort_values("abs_mean_ic", ascending=False).reset_index(drop=True)
        return summary

    def factor_correlation(self, factor_df: pd.DataFrame) -> pd.DataFrame:
        """Compute factor-to-factor correlation matrix (most recent date).

        Uses the latest date in the dataset to compute cross-sectional
        Pearson correlations between all factor columns.

        Args:
            factor_df: Cross-sectional factor DataFrame.

        Returns:
            Square correlation matrix (DataFrame), or empty DataFrame if
            the latest date has fewer than 30 stocks.
        """
        factor_cols = [
            c
            for c in factor_df.columns
            if c not in ("symbol", "date", "fwd_ret_5d", "fwd_ret_20d")
        ]

        latest = factor_df["date"].max()
        latest_df = factor_df[factor_df["date"] == latest][factor_cols].dropna(axis=1)

        if len(latest_df) < 30:
            return pd.DataFrame()

        return latest_df.corr()

# ── Backward-compatibility alias ──────────────────────────────────────
import warnings

class FactorBuilder(TechFactorBuilder):
    """Deprecated alias for TechFactorBuilder — use TechFactorBuilder directly."""
    def __init__(self, *args, **kwargs):
        warnings.warn("FactorBuilder is deprecated, use TechFactorBuilder", DeprecationWarning, stacklevel=2)
        super().__init__(*args, **kwargs)
