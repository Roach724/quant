"""Analysis Layer — deep LLM analysis for top-K candidate stocks.

Takes candidates from the Candidate Pool, collects technical + fundamental
data via DataProvider, assembles a prompt, calls LLM, and returns structured
AnalysisReport objects.

Concurrent: calls LLM in parallel for multiple symbols (configurable).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import jinja2
from openai import AsyncOpenAI

from ai_decision.schemas import AnalysisReport, Candidate, MarketData
from ai_decision.data_provider import DataProvider
from ai_decision.config import AIDecisionConfig

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "prompt_templates"

# DeepSeek API config (from OpenClaw provider config)
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_API_KEY = "sk-2b1489de68a54d58864585dd6c34fd30"
DEEPSEEK_MODEL = "deepseek-v4-pro"


class StockAnalyst:
    """LLM-powered deep analysis for individual stocks.

    Usage:
        analyst = StockAnalyst(config)
        reports = await analyst.analyze(candidates[:10])
    """

    def __init__(self, config: AIDecisionConfig):
        self.config = config
        self.top_k = config.top_k
        self.llm_config = config.analysis_llm
        self.concurrent = self.llm_config.get("concurrent", 5)

        self._client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        self._data = DataProvider()
        self._jinja = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=False,
        )
        self._template = self._jinja.get_template("analyst.j2")

    async def analyze(self, candidates: list[Candidate]) -> list[AnalysisReport]:
        """Run deep analysis for all candidates.

        Args:
            candidates: ranked candidates from CandidatePool

        Returns:
            List of AnalysisReport, one per candidate.
        """
        if not candidates:
            logger.info("No candidates to analyze")
            return []

        top = candidates[: self.top_k]
        logger.info(
            "Analyzing %d candidates (top_k=%d)",
            len(top), self.top_k,
        )

        # Collect all data in parallel
        fields = [
            "price", "ma_20", "ma_50", "rsi_14", "macd", "macd_signal",
            "bb_position", "bb_width", "volume",
            "pe", "forward_pe", "revenue_growth", "net_margin", "debt_equity",
            "news_sentiment", "news_headlines",
        ]
        symbols = [c.symbol for c in top]
        market_data = await self._data.get_multi(symbols, fields)

        # Run LLM calls with limited concurrency
        semaphore = asyncio.Semaphore(self.concurrent)

        async def analyze_one(candidate: Candidate) -> AnalysisReport:
            async with semaphore:
                data = market_data.get(candidate.symbol)
                if data is None:
                    return self._empty_report(candidate.symbol, "No data available")
                return await self._analyze_one(candidate, data)

        tasks = [analyze_one(c) for c in top]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        reports: list[AnalysisReport] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Analysis failed for %s: %s",
                    top[i].symbol, result,
                )
                reports.append(self._empty_report(
                    top[i].symbol, str(result),
                ))
            else:
                reports.append(result)

        logger.info(
            "Analysis complete: %d reports (%d success)",
            len(reports),
            sum(1 for r in reports if r.confidence > 0),
        )
        return reports

    async def _analyze_one(
        self, candidate: Candidate, data: MarketData,
    ) -> AnalysisReport:
        """Run LLM analysis for a single stock."""
        # ── Build prompt context ──
        technical = {}
        if data.price:
            technical = {
                "price": data.price,
                "ma_20": data.ma_20,
                "ma_50": data.ma_50,
                "rsi_14": data.rsi_14,
                "macd": data.macd,
                "macd_signal": data.macd_signal,
                "bb_position": data.bb_position,
                "volume": data.volume,
            }

        fundamentals = {}
        if data.pe is not None:
            fundamentals = {
                "pe": data.pe,
                "forward_pe": data.forward_pe,
                "revenue_growth": data.revenue_growth,
                "net_margin": data.net_margin,
                "debt_equity": data.debt_equity,
                "sector": getattr(data, "sector", None),
                "industry": getattr(data, "industry", None),
            }

        news = []
        if data.news_headlines:
            news = data.news_headlines

        prompt = self._template.render(
            symbol=candidate.symbol,
            market="US",
            signals=candidate.hitting_strategies,
            technical=technical,
            fundamentals=fundamentals,
            news=news,
            coverage=data.data_coverage,
        )

        # ── Call LLM ──
        try:
            response = await self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a quantitative analyst. Respond ONLY with valid JSON, no markdown.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.llm_config.get("temperature", 0.3),
                max_tokens=self.llm_config.get("max_tokens", 2000),
            )
            raw_text = response.choices[0].message.content or ""
        except Exception as e:
            logger.error("LLM call failed for %s: %s", candidate.symbol, e)
            return self._empty_report(candidate.symbol, f"LLM error: {e}")

        # ── Parse JSON ──
        parsed = self._parse_json(raw_text)

        if parsed is None:
            logger.warning("Failed to parse LLM output for %s", candidate.symbol)
            return self._empty_report(
                candidate.symbol, "JSON parse failed",
            )

        return AnalysisReport(
            symbol=candidate.symbol,
            direction=parsed.get("direction", "neutral"),
            confidence=float(parsed.get("confidence", 0.0)),
            rating=parsed.get("rating"),
            key_arguments=parsed.get("key_arguments", []),
            risk_factors=parsed.get("risk_factors", []),
            suggested_weight_modifier=float(
                parsed.get("suggested_weight_modifier", 0.0)
            ),
            data_coverage=data.data_coverage,
            timestamp=datetime.now(timezone.utc),
        )

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Extract JSON from LLM output (robust against markdown wrapping)."""
        if not text:
            return None

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` fence
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _empty_report(symbol: str, error: str) -> AnalysisReport:
        return AnalysisReport(
            symbol=symbol,
            direction="neutral",
            confidence=0.0,
            key_arguments=[f"Analysis unavailable: {error}"],
            risk_factors=["Analysis error"],
            suggested_weight_modifier=0.0,
            data_coverage={},
            timestamp=datetime.now(timezone.utc),
        )
