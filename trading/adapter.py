"""StrategyAdapter — 复用现有 engine.strategy.Strategy 到交易系统"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradingSignal:
    """交易信号 — 策略输出 → 交易引擎处理"""

    symbol: str
    side: str  # buy / sell / close
    weight: float = 1.0
    qty: Optional[int] = None
    order_type: str = "market"  # market / limit
    limit_price: Optional[float] = None
    score: float = 0.0
    strategy_id: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class StrategyAdapter:
    """加载策略类 → 注入上下文 → 调用 on_bar → 产生信号列表"""

    def __init__(self, strategy_name: str, strategy_kwargs: dict, market: str):
        self.strategy_name = strategy_name
        self.strategy_kwargs = strategy_kwargs
        self.market = market
        self._strategy = None
        self._symbols: list[str] = []
        self._last_bar: int = 0

    def load(self, symbols: list[str], ctx=None):
        """加载策略实例并调用 on_init"""
        from strategies import get_strategy

        cls = get_strategy(self.strategy_name)
        try:
            self._strategy = cls(**self.strategy_kwargs)
        except TypeError:
            self._strategy = cls()
            for k, v in self.strategy_kwargs.items():
                if hasattr(self._strategy, k):
                    setattr(self._strategy, k, v)
        self._symbols = list(symbols)

        if ctx and self._strategy:
            try:
                self._strategy.on_init(ctx, symbols=symbols)
            except TypeError:
                self._strategy.on_init(ctx)

        logger.info(
            "Loaded %s with %d symbols", self.strategy_name, len(symbols)
        )

    def generate_signals(
        self, ctx, bar: int, strategy_id: int
    ) -> list[TradingSignal]:
        """调用策略的 on_bar，返回 TradingSignal 列表"""
        if not self._strategy:
            return []
        try:
            signals = self._strategy.on_bar(ctx, bar)
        except Exception:
            logger.exception(
                "%s.on_bar failed at bar %d", self.strategy_name, bar
            )
            return []
        return [
            TradingSignal(
                symbol=s.symbol,
                side=s.side,
                weight=getattr(s, "weight", 1.0) or 1.0,
                order_type=getattr(s, "order_type", "market") or "market",
                limit_price=getattr(s, "limit_price", None),
                score=getattr(s, "score", 0.0) or 0.0,
                strategy_id=strategy_id,
            )
            for s in signals
        ]
