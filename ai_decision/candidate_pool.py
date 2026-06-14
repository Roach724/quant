"""Candidate Pool — filter and rank signals from the Recall Layer.

Takes raw StrategySignals, aggregates per-symbol, filters by threshold,
and produces a ranked candidate list for the Analysis Layer.

The pool size is dynamic — only symbols that pass the threshold survive.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ai_decision.schemas import StrategySignal, Candidate

logger = logging.getLogger(__name__)


class CandidatePool:
    """Filter and aggregate recall signals into ranked candidates.

    Usage:
        pool = CandidatePool(min_threshold=0.20)
        candidates = pool.filter(recall_signals)
        # candidates sorted by aggregate_score descending
    """

    def __init__(
        self,
        min_threshold: float = 0.20,
        aggregation: str = "max_abs",
    ):
        """
        Args:
            min_threshold: minimum |score| * confidence to survive the filter.
            aggregation: how to combine multiple strategies' scores per symbol.
                - "max_abs": take the strategy with the highest absolute score
                - "mean": average all strategies' scores
                - "weighted_mean": weighted by confidence
        """
        self.min_threshold = min_threshold
        self.aggregation = aggregation
        if aggregation not in ("max_abs", "mean", "weighted_mean"):
            raise ValueError(f"Unknown aggregation: {aggregation}")

    def filter(self, signals: list[StrategySignal]) -> list[Candidate]:
        """Filter and rank signals into candidates.

        Args:
            signals: raw signals from RecallEngine.run()

        Returns:
            Sorted list of Candidate, highest aggregate_score first.
        """
        if not signals:
            logger.info("Candidate pool: no signals to filter")
            return []

        # ── 1. Group by symbol ──
        by_symbol: dict[str, list[StrategySignal]] = defaultdict(list)
        for s in signals:
            by_symbol[s.symbol].append(s)

        logger.info(
            "Candidate pool: %d signals → %d unique symbols",
            len(signals), len(by_symbol),
        )

        # ── 2. Compute aggregate score per symbol ──
        candidates: list[Candidate] = []
        dropped_count = 0

        for symbol, sym_signals in by_symbol.items():
            agg_score = self._aggregate(sym_signals)
            composite = abs(agg_score) * max(s.confidence for s in sym_signals)

            if composite < self.min_threshold:
                dropped_count += 1
                logger.debug(
                    "Dropped %s: aggregate=%.3f, composite=%.3f < threshold=%.2f",
                    symbol, agg_score, composite, self.min_threshold,
                )
                continue

            hitting_strategies = [
                {
                    "strategy": s.strategy,
                    "score": s.score,
                    "direction": s.direction,
                }
                for s in sym_signals
            ]

            candidates.append(Candidate(
                symbol=symbol,
                aggregate_score=agg_score,
                hitting_count=len(sym_signals),
                hitting_strategies=hitting_strategies,
            ))

        if dropped_count:
            logger.info(
                "Candidate pool: dropped %d symbols below threshold %.2f",
                dropped_count, self.min_threshold,
            )

        # ── 3. Sort by aggregate score descending, assign ranks ──
        candidates.sort(key=lambda c: abs(c.aggregate_score), reverse=True)
        for i, c in enumerate(candidates):
            c.rank = i + 1

        logger.info(
            "Candidate pool: %d candidates survived (top: %s)",
            len(candidates),
            ", ".join(c.symbol for c in candidates[:5]),
        )

        return candidates

    def _aggregate(self, signals: list[StrategySignal]) -> float:
        """Combine multiple strategy scores for one symbol."""
        if self.aggregation == "max_abs":
            best = max(signals, key=lambda s: abs(s.score))
            return best.score

        elif self.aggregation == "mean":
            return sum(s.score for s in signals) / len(signals)

        elif self.aggregation == "weighted_mean":
            total_weight = sum(s.confidence for s in signals)
            if total_weight == 0:
                return 0.0
            return sum(s.score * s.confidence for s in signals) / total_weight

        return 0.0

    def top_k(self, candidates: list[Candidate], k: int) -> list[Candidate]:
        """Convenience: return top-K candidates (for analysis layer)."""
        return candidates[:k]
