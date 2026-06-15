"""AI Decision Engine — main orchestrator.

Ties together all five pipeline layers:
  Recall → Candidate Pool → Analysis → Fusion → Execution

Usage:
    engine = AIDecisionEngine(config)
    plan = await engine.run()
    # plan is a PortfolioDecision with buy/sell orders
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ai_decision.config import AIDecisionConfig, load_config
from ai_decision.recall import RecallEngine
from ai_decision.candidate_pool import CandidatePool
from ai_decision.analyst import StockAnalyst
from ai_decision.fusion import FusionEngine
from ai_decision.stock_evaluator import StockEvaluator
from ai_decision.portfolio_allocator import PortfolioAllocator
from ai_decision.schemas import (
    StrategySignal,
    Candidate,
    AnalysisReport,
    FusionResult,
    StockDecision,
    PortfolioDecision,
)

logger = logging.getLogger(__name__)


class AIDecisionEngine:
    """Full AI decision pipeline orchestrator.

    Run the complete pipeline:
        engine = AIDecisionEngine.from_config()
        plan = await engine.run(account_state={...})

    Or run individual layers:
        signals = engine.run_recall()
        candidates = engine.run_candidate_pool(signals)
        reports = await engine.run_analysis(candidates)
        fusion = engine.run_fusion(signals, reports)
        decisions = await engine.run_stock_eval(fusion, reports, market_data, holdings)
        plan = engine.run_portfolio_alloc(decisions, account_state)
    """

    def __init__(self, config: AIDecisionConfig):
        self.config = config
        self._recall: RecallEngine | None = None
        self._pool: CandidatePool | None = None
        self._analyst: StockAnalyst | None = None
        self._fusion: FusionEngine | None = None
        self._evaluator: StockEvaluator | None = None
        self._allocator: PortfolioAllocator | None = None

        # State tracking
        self.signals: list[StrategySignal] = []
        self.candidates: list[Candidate] = []
        self.reports: list[AnalysisReport] = []
        self.fusion_results: list[FusionResult] = []
        self.stock_decisions: list[StockDecision] = []
        self.portfolio_plan: PortfolioDecision | None = None
        self.last_run: datetime | None = None

    @classmethod
    def from_config(cls, config_path: str | None = None) -> "AIDecisionEngine":
        """Create engine from config file."""
        cfg = load_config(config_path)
        return cls(cfg)

    # ── Full Pipeline ──

    async def run(
        self,
        account_state: dict | None = None,
        strategy_id: str = "ai_decision",
        environment: str = "sim",
    ) -> PortfolioDecision:
        """Run the complete AI decision pipeline.

        Args:
            account_state: {total_equity, cash, positions, buying_power, sector_map, prices}
            strategy_id: identifier for this strategy instance
            environment: "experiment" | "sim" | "real"

        Returns:
            PortfolioDecision with buy/sell orders.
        """
        account_state = account_state or {}
        logger.info("AI Decision Engine: starting full pipeline")

        # ① Recall
        self.signals = self.run_recall()
        if not self.signals:
            logger.info("Pipeline stopped: no signals from recall")
            return self._empty_plan(strategy_id, environment, "No strategy signals")

        # ② Candidate Pool
        self.candidates = self.run_candidate_pool(self.signals)
        if not self.candidates:
            logger.info("Pipeline stopped: no candidates passed threshold")
            return self._empty_plan(strategy_id, environment, "No candidates above threshold")

        # ③ Analysis
        self.reports = await self.run_analysis(self.candidates)

        # ④ Fusion
        self.fusion_results = self.run_fusion(self.signals, self.reports)

        # ⑤ Execution: Stage 1 — Stock Evaluator
        market_data = account_state.get("market_data", {})
        holdings = account_state.get("holdings", {})
        self.stock_decisions = await self.run_stock_eval(
            self.fusion_results, self.reports, market_data, holdings,
        )

        # ⑤ Execution: Stage 2 — Portfolio Allocator
        self.portfolio_plan = self.run_portfolio_alloc(
            self.stock_decisions, account_state, strategy_id, environment,
        )

        self.last_run = datetime.now(timezone.utc)
        logger.info("AI Decision Engine: pipeline complete")
        return self.portfolio_plan

    # ── Individual Layers (callable independently) ──

    def run_recall(self) -> list[StrategySignal]:
        """① Recall: aggregate strategy signals."""
        self._recall = RecallEngine(
            enabled_strategies=self.config.enabled_strategies,
            symbols=None,
        )
        signals = self._recall.run()
        logger.info("① Recall: %d signals", len(signals))
        return signals

    def run_candidate_pool(self, signals: list[StrategySignal]) -> list[Candidate]:
        """② Candidate Pool: threshold filter."""
        self._pool = CandidatePool(
            min_threshold=self.config.min_signal_threshold,
            aggregation=self.config.aggregation_method,
        )
        candidates = self._pool.filter(signals)
        logger.info("② Candidate Pool: %d candidates", len(candidates))
        return candidates

    async def run_analysis(self, candidates: list[Candidate]) -> list[AnalysisReport]:
        """③ Analysis: LLM deep analysis."""
        self._analyst = StockAnalyst(self.config)
        reports = await self._analyst.analyze(candidates)
        logger.info("③ Analysis: %d reports", len(reports))
        return reports

    def run_fusion(
        self,
        signals: list[StrategySignal],
        reports: list[AnalysisReport],
    ) -> list[FusionResult]:
        """④ Fusion: combine signals + analysis."""
        self._fusion = FusionEngine(
            mode=self.config.fusion_mode,
            weights=self.config.fusion_weights,
        )
        results = self._fusion.fuse(signals, reports)
        logger.info("④ Fusion: %d ranked symbols", len(results))
        return results

    async def run_stock_eval(
        self,
        fusion_results: list[FusionResult],
        reports: list[AnalysisReport],
        market_data: dict,
        holdings: dict,
    ) -> list[StockDecision]:
        """⑤ Stage 1: per-symbol LLM execution decisions."""
        self._evaluator = StockEvaluator(
            model=self.config.stock_eval_llm.get("model", "deepseek-v4-pro"),
            temperature=self.config.stock_eval_llm.get("temperature", 0.2),
            max_tokens=self.config.stock_eval_llm.get("max_tokens", 1000),
            max_position_pct=self.config.max_position_pct,
            concurrent=self.config.stock_eval_batch_size,
        )
        decisions = await self._evaluator.evaluate(
            fusion_results, reports, market_data, holdings,
        )
        logger.info("⑤ Stock Eval: %d decisions", len(decisions))
        return decisions

    def run_portfolio_alloc(
        self,
        decisions: list[StockDecision],
        account_state: dict,
        strategy_id: str = "ai_decision",
        environment: str = "sim",
    ) -> PortfolioDecision:
        """⑤ Stage 2: algorithmic portfolio allocation."""
        self._allocator = PortfolioAllocator(
            max_position_pct=self.config.max_position_pct,
            max_sector_pct=self.config.max_sector_pct,
            min_cash_reserve=self.config.min_cash_reserve,
            max_turnover=self.config.max_turnover,
            min_trade_value=self.config.min_trade_value,
        )
        plan = self._allocator.allocate(decisions, account_state, strategy_id, environment)
        logger.info(
            "⑤ Portfolio Alloc: %s — %d sells, %d buys",
            plan.summary.action,
            len(plan.sell_orders),
            len(plan.buy_orders),
        )
        return plan

    # ── Helpers ──

    @staticmethod
    def _empty_plan(strategy_id: str, environment: str, reason: str) -> PortfolioDecision:
        from ai_decision.schemas import AllocationSummary
        return PortfolioDecision(
            strategy_id=strategy_id,
            run_time=datetime.now(timezone.utc),
            environment=environment,
            sell_orders=[],
            buy_orders=[],
            summary=AllocationSummary(
                action="no_op",
                net_cash_change=0.0,
                remaining_cash_estimate=0.0,
                turnover_pct=0.0,
                no_op_reason=reason,
            ),
        )

    @property
    def summary(self) -> dict:
        """Quick summary of last run."""
        return {
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "signals": len(self.signals),
            "candidates": len(self.candidates),
            "reports": len(self.reports),
            "fusion": len(self.fusion_results),
            "decisions": len(self.stock_decisions),
            "plan": (
                {
                    "action": self.portfolio_plan.summary.action,
                    "sells": len(self.portfolio_plan.sell_orders),
                    "buys": len(self.portfolio_plan.buy_orders),
                    "turnover": self.portfolio_plan.summary.turnover_pct,
                }
                if self.portfolio_plan else None
            ),
        }
