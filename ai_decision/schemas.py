"""Pydantic data models for the AI Decision Engine pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── ① Recall Layer ──────────────────────────────────────────────

class StrategySignal(BaseModel):
    """A single signal from one strategy for one symbol."""

    symbol: str
    strategy: str
    direction: Literal["buy", "sell", "hold"]
    score: float = Field(description="Raw strategy score (strategy-native scale, normalized in fusion layer)")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    timestamp: datetime | None = None


class RecallResult(BaseModel):
    """Aggregated signals for one symbol across all strategies."""

    symbol: str
    signals: list[StrategySignal]
    aggregate_score: float = Field(description="Combined score across strategies")
    hitting_count: int = Field(description="Number of strategies that fired")


# ── ② Candidate Pool ────────────────────────────────────────────

class Candidate(BaseModel):
    """A symbol that passed the signal threshold and entered the candidate pool."""

    symbol: str
    aggregate_score: float
    hitting_count: int
    hitting_strategies: list[dict] = Field(
        default_factory=list,
        description="[{'strategy': '...', 'score': 0.82, 'direction': 'buy'}]",
    )
    rank: int = 0


# ── ③ Analysis Layer ─────────────────────────────────────────────

class AnalysisReport(BaseModel):
    """LLM-produced deep analysis for a single symbol."""

    symbol: str
    direction: Literal["bullish", "neutral", "bearish"]
    confidence: float = Field(ge=0.0, le=1.0)
    rating: str | None = Field(default=None, description="e.g. A-, B+")
    key_arguments: list[str] = Field(default_factory=list, min_length=1, max_length=5)
    risk_factors: list[str] = Field(default_factory=list, min_length=1, max_length=3)
    suggested_weight_modifier: float = Field(
        ge=-0.5, le=0.5, default=0.0,
        description="Adjustment to strategy signal weight",
    )
    data_coverage: dict[str, bool] = Field(
        default_factory=dict,
        description="e.g. {'technical': True, 'fundamental': True, 'sentiment': False}",
    )
    timestamp: datetime | None = None


# ── ④ Fusion Layer ───────────────────────────────────────────────

class FusionResult(BaseModel):
    """Final ranked score for a symbol after fusion."""

    symbol: str
    final_score: float
    rank: int
    fusion_mode: str  # "voting" | "weighted"
    contributing_signals: list[str] = Field(default_factory=list)


# ── ⑤ Execution Layer: Stock Evaluator ──────────────────────────

class StockDecision(BaseModel):
    """Per-symbol LLM decision (Stage 1)."""

    symbol: str
    action: Literal["buy", "sell", "hold", "watch"]
    suggested_weight: float = Field(ge=0.0, le=1.0, description="Fraction of portfolio")
    reason: str
    urgency: Literal["high", "medium", "low"] = "medium"


# ── ⑤ Execution Layer: Portfolio Allocator ───────────────────────

class OrderLeg(BaseModel):
    """A single buy or sell leg in the rebalance plan."""

    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    estimated_price: float | None = None
    estimated_value: float | None = None
    reason: str


class AllocationSummary(BaseModel):
    """Portfolio-level summary of the rebalance plan."""

    action: Literal["rebalance", "no_op"] = "rebalance"
    net_cash_change: float = 0.0
    remaining_cash_estimate: float = 0.0
    turnover_pct: float = 0.0
    sector_distribution: dict[str, float] = Field(default_factory=dict)
    no_op_reason: str | None = Field(
        default=None,
        description="Reason when action='no_op' (all positions optimal)",
    )


class PortfolioDecision(BaseModel):
    """Final output of the Execution Layer (Stage 2)."""

    strategy_id: str
    run_time: datetime
    environment: Literal["experiment", "sim", "real"]
    sell_orders: list[OrderLeg] = Field(default_factory=list)
    buy_orders: list[OrderLeg] = Field(default_factory=list)
    summary: AllocationSummary


# ── Data Models ──────────────────────────────────────────────────

class MarketData(BaseModel):
    """Aggregated market data for a single symbol from all sources."""

    symbol: str
    timestamp: datetime | None = None

    # Technical (from BigQuery)
    price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    ma_20: float | None = None
    ma_50: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bb_position: float | None = None
    bb_width: float | None = None
    atr_14: float | None = None

    # Fundamental (from LLMQuant)
    pe: float | None = None
    forward_pe: float | None = None
    revenue_growth: float | None = None
    net_margin: float | None = None
    debt_equity: float | None = None

    # Sentiment / News (from LLMQuant)
    news_sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)
    news_headlines: list[dict] = Field(default_factory=list)

    # Source tracking
    data_coverage: dict[str, bool] = Field(default_factory=dict)


class DataSourceStatus(BaseModel):
    """Status report from a single data source."""

    source: str  # "bigquery" | "llmquant"
    available: bool
    fields_available: list[str] = Field(default_factory=list)
    fields_missing: list[str] = Field(default_factory=list)
    error: str | None = None
