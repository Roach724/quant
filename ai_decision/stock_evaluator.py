"""Stock Evaluator — per-symbol LLM execution decisions (Stage 1).

Takes fusion results + AI analysis + real-time data + current holdings,
calls LLM to decide buy/sell/hold/watch for each symbol.

Output: list[StockDecision]
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import jinja2
from openai import AsyncOpenAI

from ai_decision.schemas import (
    AnalysisReport,
    FusionResult,
    MarketData,
    StockDecision,
)

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "prompt_templates"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"


class StockEvaluator:
    """LLM-based per-symbol execution decision engine."""

    def __init__(
        self,
        model: str = DEEPSEEK_MODEL,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        max_position_pct: float = 0.15,
        concurrent: int = 5,
        api_key: str | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_position_pct = max_position_pct
        self.concurrent = concurrent

        import os
        self._client = AsyncOpenAI(
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=DEEPSEEK_BASE_URL,
        )
        self._jinja = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=False,
        )
        self._template = self._jinja.get_template("stock_eval.j2")

    async def evaluate(
        self,
        fusion_results: list[FusionResult],
        reports: list[AnalysisReport],
        market_data: dict[str, MarketData],
        holdings: dict[str, dict] | None = None,
    ) -> list[StockDecision]:
        """Evaluate all fusion-ranked symbols for execution decisions.

        Args:
            fusion_results: ranked fusion results
            reports: AI analysis reports (matched by symbol)
            market_data: real-time market data (matched by symbol)
            holdings: current positions {symbol: {qty, pct, unrealized_pl}}

        Returns:
            StockDecision list with action for each symbol.
        """
        if not fusion_results:
            return []

        reports_by_symbol = {r.symbol: r for r in reports}
        holdings = holdings or {}

        semaphore = asyncio.Semaphore(self.concurrent)

        async def eval_one(fr: FusionResult) -> StockDecision:
            async with semaphore:
                return await self._evaluate_one(
                    fr,
                    reports_by_symbol.get(fr.symbol),
                    market_data.get(fr.symbol),
                    holdings.get(fr.symbol, {}),
                    len(fusion_results),
                )

        tasks = [eval_one(fr) for fr in fusion_results]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        decisions: list[StockDecision] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Stock eval failed for %s: %s", fusion_results[i].symbol, result)
                decisions.append(StockDecision(
                    symbol=fusion_results[i].symbol,
                    action="watch",
                    suggested_weight=0.0,
                    reason=f"Evaluation error: {result}",
                    urgency="low",
                ))
            else:
                decisions.append(result)

        logger.info(
            "Stock evaluator: %d decisions (%d buy, %d sell, %d hold, %d watch)",
            len(decisions),
            sum(1 for d in decisions if d.action == "buy"),
            sum(1 for d in decisions if d.action == "sell"),
            sum(1 for d in decisions if d.action == "hold"),
            sum(1 for d in decisions if d.action == "watch"),
        )
        return decisions

    async def _evaluate_one(
        self,
        fusion: FusionResult,
        report: AnalysisReport | None,
        data: MarketData | None,
        holding: dict,
        total_candidates: int,
    ) -> StockDecision:
        """Evaluate a single symbol."""
        # ── Fast-path: skip LLM for clear-cut cases ──
        # High score + no position → auto-buy (save LLM token)
        if fusion.final_score >= 0.8 and (not holding or holding.get("qty", 0) == 0):
            return StockDecision(
                symbol=fusion.symbol,
                action="buy",
                suggested_weight=min(0.08, self.max_position_pct),
                reason=f"High fusion score ({fusion.final_score:.2f}), auto-buy",
                urgency="high",
            )
        # Low score + no position → auto-watch (not worth LLM)
        if fusion.final_score <= 0.2 and (not holding or holding.get("qty", 0) == 0):
            return StockDecision(
                symbol=fusion.symbol,
                action="watch",
                suggested_weight=0.0,
                reason=f"Low fusion score ({fusion.final_score:.2f}), skip",
                urgency="low",
            )

        # Build snapshot
        snapshot = {}
        if data and data.price:
            snapshot = {
                "price": data.price,
                "ma_20": data.ma_20,
                "rsi_14": data.rsi_14,
                "macd": data.macd,
                "macd_signal": data.macd_signal,
            }

        # Default analysis stub
        analysis = {
            "direction": "neutral",
            "confidence": 0.0,
            "rating": None,
            "key_arguments": ["No analysis available"],
            "risk_factors": [],
            "suggested_weight_modifier": 0.0,
        }
        if report:
            analysis = {
                "direction": report.direction,
                "confidence": report.confidence,
                "rating": report.rating,
                "key_arguments": report.key_arguments,
                "risk_factors": report.risk_factors,
                "suggested_weight_modifier": report.suggested_weight_modifier,
            }

        prompt = self._template.render(
            symbol=fusion.symbol,
            rank=fusion.rank,
            total=total_candidates,
            final_score=fusion.final_score,
            analysis=analysis,
            snapshot=snapshot,
            holding=holding if holding.get("qty", 0) > 0 else None,
            max_position_pct=self.max_position_pct,
        )

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a trading decision engine. Respond ONLY with valid JSON, no markdown.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            raw_text = response.choices[0].message.content or ""
        except Exception as e:
            logger.error("LLM call failed for %s: %s", fusion.symbol, e)
            return StockDecision(
                symbol=fusion.symbol,
                action="watch",
                suggested_weight=0.0,
                reason=f"LLM error: {e}",
                urgency="low",
            )

        parsed = self._parse_json(raw_text)
        if parsed is None:
            return StockDecision(
                symbol=fusion.symbol,
                action="watch",
                suggested_weight=0.0,
                reason="Failed to parse LLM output",
                urgency="low",
            )

        return StockDecision(
            symbol=fusion.symbol,
            action=parsed.get("action", "watch"),
            suggested_weight=float(parsed.get("suggested_weight", 0.0)),
            reason=parsed.get("reason", ""),
            urgency=parsed.get("urgency", "medium"),
        )

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None
