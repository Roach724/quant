"""Fusion Layer — combine strategy signals with AI analysis into a final ranking.

Supports two modes (configurable):
  A) Voting: count strategies that selected each symbol × AI confidence boost
  B) Weighted (default): z-score normalize per-strategy → weighted sum + AI score → rank

Output: list[FusionResult] sorted by final_score descending.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np

from ai_decision.schemas import (
    StrategySignal,
    AnalysisReport,
    FusionResult,
)

logger = logging.getLogger(__name__)


class FusionEngine:
    """Fuse strategy signals and AI analysis into a final ranked list.

    Usage:
        engine = FusionEngine(mode="weighted", weights={...})
        results = engine.fuse(recall_signals, analysis_reports)
        # results sorted by final_score descending
    """

    def __init__(
        self,
        mode: str = "weighted",
        weights: dict[str, float] | None = None,
    ):
        """
        Args:
            mode: "voting" or "weighted"
            weights: per-strategy weights for weighted mode.
                     Must include "ai_analysis" key for AI report weighting.
        """
        if mode not in ("voting", "weighted"):
            raise ValueError(f"Unknown fusion mode: {mode}")
        self.mode = mode
        self.weights = weights or {}

    def fuse(
        self,
        signals: list[StrategySignal],
        reports: list[AnalysisReport] | None = None,
    ) -> list[FusionResult]:
        """Combine signals and AI reports into final ranking.

        Args:
            signals: raw strategy signals from RecallEngine
            reports: AI analysis reports from StockAnalyst (optional)

        Returns:
            Sorted list of FusionResult by final_score descending.
        """
        if not signals:
            logger.info("Fusion: no signals to fuse")
            return []

        reports_by_symbol: dict[str, AnalysisReport] = {}
        if reports:
            reports_by_symbol = {r.symbol: r for r in reports}

        if self.mode == "voting":
            return self._fuse_voting(signals, reports_by_symbol)
        else:
            return self._fuse_weighted(signals, reports_by_symbol)

    # ── Mode A: Voting ─────────────────────────────────────────────

    def _fuse_voting(
        self,
        signals: list[StrategySignal],
        reports_by_symbol: dict[str, AnalysisReport],
    ) -> list[FusionResult]:
        """Voting: count strategies that selected each symbol × AI boost."""
        # Count votes per symbol
        vote_counts: dict[str, int] = defaultdict(int)
        strategies_per_symbol: dict[str, list[str]] = defaultdict(list)

        for s in signals:
            vote_counts[s.symbol] += 1
            strategies_per_symbol[s.symbol].append(s.strategy)

        results: list[FusionResult] = []
        for symbol, votes in vote_counts.items():
            # AI confidence boost (1.0 if no AI report)
            ai_boost = 1.0
            report = reports_by_symbol.get(symbol)
            if report and report.confidence > 0:
                # Map confidence to boost: 0.5→1.0x, 0.8→1.8x, 1.0→2.0x
                ai_boost = 1.0 + report.confidence * 1.0

            final_score = votes * ai_boost
            results.append(FusionResult(
                symbol=symbol,
                final_score=final_score,
                rank=0,  # assigned after sort
                fusion_mode="voting",
                contributing_signals=list(set(strategies_per_symbol[symbol])),
            ))

        # Sort and rank
        results.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        logger.info(
            "Fusion (voting): %d symbols ranked (top: %s)",
            len(results),
            ", ".join(r.symbol for r in results[:5]),
        )
        return results

    # ── Mode B: Weighted ───────────────────────────────────────────

    def _fuse_weighted(
        self,
        signals: list[StrategySignal],
        reports_by_symbol: dict[str, AnalysisReport],
    ) -> list[FusionResult]:
        """Weighted fusion with z-score normalization per strategy.

        Step 1: Group signals by strategy
        Step 2: Z-score normalize scores within each strategy
        Step 3: Weighted sum per symbol = Σ(w_strategy × z_score) + w_ai × ai_direction_score
        Step 4: Final rank
        """
        # ── Group by strategy ──
        by_strategy: dict[str, list[StrategySignal]] = defaultdict(list)
        for s in signals:
            by_strategy[s.strategy].append(s)

        # ── Z-score normalize per strategy ──
        z_scores: dict[str, dict[str, float]] = {}  # strategy → {symbol → z_score}
        for strategy_name, strat_signals in by_strategy.items():
            scores = np.array([abs(s.score) for s in strat_signals])
            mean = scores.mean()
            std = scores.std()

            z_scores[strategy_name] = {}
            for s in strat_signals:
                if std > 0:
                    z = (abs(s.score) - mean) / std
                else:
                    z = 0.0  # all same score
                # Apply direction sign
                z_scores[strategy_name][s.symbol] = z if s.direction == "buy" else -z

        # ── Weighted sum per symbol ──
        ai_weight = self.weights.get("ai_analysis", 1.5)
        symbol_scores: dict[str, float] = defaultdict(float)
        symbol_strategies: dict[str, list[str]] = defaultdict(list)

        for strategy_name, symbol_zs in z_scores.items():
            w = self.weights.get(strategy_name, 1.0)
            for symbol, z in symbol_zs.items():
                symbol_scores[symbol] += w * z
                symbol_strategies[symbol].append(strategy_name)

        # ── Add AI analysis scores ──
        for symbol, report in reports_by_symbol.items():
            if report.confidence > 0:
                # Convert AI report to a score signal
                ai_direction_score = 0.0
                if report.direction == "bullish":
                    ai_direction_score = report.confidence
                elif report.direction == "bearish":
                    ai_direction_score = -report.confidence

                symbol_scores[symbol] += ai_weight * ai_direction_score

        # ── Build results ──
        all_symbols = set(symbol_scores.keys()) | set(reports_by_symbol.keys())
        results: list[FusionResult] = []

        for symbol in all_symbols:
            results.append(FusionResult(
                symbol=symbol,
                final_score=symbol_scores.get(symbol, 0.0),
                rank=0,
                fusion_mode="weighted",
                contributing_signals=list(set(symbol_strategies.get(symbol, []))),
            ))

        # Sort and rank
        results.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        logger.info(
            "Fusion (weighted): %d symbols ranked (top: %s)",
            len(results),
            ", ".join(f"{r.symbol}({r.final_score:.2f})" for r in results[:5]),
        )
        return results
