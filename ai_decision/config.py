"""Configuration loader for AI Decision Engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "default_config.yaml"


class AIDecisionConfig:
    """Typed accessor for ai_decision configuration."""

    def __init__(self, config_dict: dict[str, Any]):
        raw = config_dict.get("ai_decision", config_dict)
        self._raw = raw

    # ── Top-level ──

    @property
    def market(self) -> str:
        return self._raw.get("market", "us")

    # ── Pipeline ──

    @property
    def pipeline(self) -> dict:
        return self._raw.get("pipeline", {})

    # ── ① Recall ──

    @property
    def enabled_strategies(self) -> list[str]:
        return self.pipeline.get("recall", {}).get("enabled_strategies", [])

    # ── ② Candidate Pool ──

    @property
    def min_signal_threshold(self) -> float:
        return self.pipeline.get("candidate_pool", {}).get("min_signal_threshold", 0.20)

    @property
    def aggregation_method(self) -> str:
        return self.pipeline.get("candidate_pool", {}).get("aggregation", "max_abs")

    # ── ③ Analysis ──

    @property
    def top_k(self) -> int:
        return self.pipeline.get("analysis", {}).get("top_k", 10)

    @property
    def analysis_llm(self) -> dict:
        return self.pipeline.get("analysis", {}).get("llm", {})

    @property
    def data_sources(self) -> list[str]:
        return self.pipeline.get("analysis", {}).get("data", {}).get("sources", ["bigquery", "llmquant"])

    @property
    def data_timeout(self) -> int:
        return self.pipeline.get("analysis", {}).get("data", {}).get("timeout_seconds", 30)

    # ── ④ Fusion ──

    @property
    def fusion_mode(self) -> str:
        return self.pipeline.get("fusion", {}).get("mode", "weighted")

    @property
    def fusion_weights(self) -> dict[str, float]:
        return self.pipeline.get("fusion", {}).get("weights", {})

    # ── ⑤ Execution ──

    @property
    def stock_eval_llm(self) -> dict:
        return self.pipeline.get("execution", {}).get("stock_eval", {}).get("llm", {})

    @property
    def stock_eval_batch_size(self) -> int:
        return self.pipeline.get("execution", {}).get("stock_eval", {}).get("batch_size", 5)

    @property
    def max_position_pct(self) -> float:
        return self._exec_constraints().get("max_position_pct", 0.15)

    @property
    def max_sector_pct(self) -> float:
        return self._exec_constraints().get("max_sector_pct", 0.40)

    @property
    def min_cash_reserve(self) -> float:
        return self._exec_constraints().get("min_cash_reserve", 0.10)

    @property
    def max_turnover(self) -> float:
        return self._exec_constraints().get("max_turnover", 0.30)

    @property
    def min_trade_value(self) -> float:
        return self._exec_constraints().get("min_trade_value", 500)

    def _exec_constraints(self) -> dict:
        return self.pipeline.get("execution", {}).get("constraints", {})

    # ── Schedule ──

    @property
    def schedule(self) -> dict:
        return self._raw.get("schedule", {})

    # ── Logging ──

    @property
    def logging(self) -> dict:
        return self._raw.get("logging", {})


def load_config(path: str | Path | None = None) -> AIDecisionConfig:
    """Load AI Decision Engine configuration from a YAML file.

    Args:
        path: Path to a YAML config file. Defaults to default_config.yaml.

    Returns:
        AIDecisionConfig instance.
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty or invalid YAML: {config_path}")

    return AIDecisionConfig(raw)
