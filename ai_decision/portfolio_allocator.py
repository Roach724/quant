"""Portfolio Allocator — algorithmic portfolio-level rebalancing (Stage 2).

Takes per-symbol StockDecisions + account state + constraints →
produces a final PortfolioDecision with buy/sell orders.

No LLM — pure algorithm with constraint resolution.

Algorithm:
  1. Ingest all stock decisions + account state
  2. Separate buy/sell decisions
  3. Match sells to free cash → fund buys
  4. Apply constraints (max_position, max_sector, min_cash, max_turnover)
  5. Output rebalance plan or no_op
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ai_decision.schemas import (
    StockDecision,
    OrderLeg,
    AllocationSummary,
    PortfolioDecision,
)

logger = logging.getLogger(__name__)


class PortfolioAllocator:
    """Algorithmic portfolio allocation engine.

    Usage:
        allocator = PortfolioAllocator(constraints={...})
        plan = allocator.allocate(decisions, account_state)
    """

    def __init__(
        self,
        max_position_pct: float = 0.15,
        max_sector_pct: float = 0.40,
        min_cash_reserve: float = 0.10,
        max_turnover: float = 0.30,
        min_trade_value: float = 500,
    ):
        self.max_position_pct = max_position_pct
        self.max_sector_pct = max_sector_pct
        self.min_cash_reserve = min_cash_reserve
        self.max_turnover = max_turnover
        self.min_trade_value = min_trade_value

    def allocate(
        self,
        decisions: list[StockDecision],
        account_state: dict[str, Any],
        strategy_id: str = "ai_decision",
        environment: str = "sim",
    ) -> PortfolioDecision:
        """Produce a rebalance plan from per-symbol decisions.

        Args:
            decisions: per-symbol StockDecisions
            account_state:
                total_equity: float — total portfolio value
                cash: float — available cash
                positions: [{symbol, qty, market_value, unrealized_pl, weight_pct}]
                buying_power: float
                sector_map: {symbol: sector} (optional)
            strategy_id: identifier
            environment: "experiment" | "sim" | "real"

        Returns:
            PortfolioDecision with buy/sell orders.
        """
        if not decisions:
            return self._no_op("No stock decisions to process", strategy_id, environment)

        total_equity = float(account_state.get("total_equity", 100000))
        cash = float(account_state.get("cash", total_equity))
        positions = account_state.get("positions", [])
        sector_map = account_state.get("sector_map", {})
        prices = account_state.get("prices", {})

        positions_by_symbol = {p["symbol"]: p for p in positions}

        # ── 1. Classify decisions ──
        buys: list[StockDecision] = []
        sells: list[StockDecision] = []
        holds: list[StockDecision] = []

        for d in decisions:
            if d.action == "buy":
                buys.append(d)
            elif d.action == "sell":
                sells.append(d)
            else:
                holds.append(d)

        logger.info(
            "Allocator: %d buys, %d sells, %d hold/watch from %d decisions",
            len(buys), len(sells), len(holds), len(decisions),
        )

        # ── 2. Quick check: no-op if nothing to do ──
        if not buys and not sells:
            return self._no_op(
                "All positions optimal, no rebalance needed",
                strategy_id, environment,
            )

        # ── 3. Process sells first (free up cash) ──
        sell_orders: list[OrderLeg] = []
        freed_cash = 0.0

        for d in sorted(sells, key=lambda x: _urgency_rank(x.urgency), reverse=True):
            pos = positions_by_symbol.get(d.symbol)
            if not pos or pos.get("qty", 0) <= 0:
                continue  # nothing to sell

            qty = pos["qty"]
            price = prices.get(d.symbol, pos.get("market_value", 0) / max(qty, 1))
            value = qty * price

            if value < self.min_trade_value:
                logger.debug("Skipping sell %s: value %.0f < min %.0f", d.symbol, value, self.min_trade_value)
                continue

            sell_orders.append(OrderLeg(
                symbol=d.symbol,
                side="sell",
                quantity=qty,
                estimated_price=price,
                estimated_value=value,
                reason=d.reason,
            ))
            freed_cash += value

        # ── 4. Process buys (constrained by available cash) ──
        available = cash + freed_cash
        reserve = total_equity * self.min_cash_reserve
        spendable = max(0, available - reserve)

        buy_orders: list[OrderLeg] = []
        total_buy_value = 0.0
        current_sector_weights: dict[str, float] = {}

        # Sort buys: high urgency first, then by suggested_weight
        sorted_buys = sorted(
            buys,
            key=lambda x: (_urgency_rank(x.urgency), x.suggested_weight),
            reverse=True,
        )

        for d in sorted_buys:
            proposed_weight = min(d.suggested_weight, self.max_position_pct)
            proposed_value = total_equity * proposed_weight

            # Check cash
            if total_buy_value + proposed_value > spendable:
                # Scale down to available cash
                remaining = spendable - total_buy_value
                if remaining < self.min_trade_value:
                    logger.debug("Skipping buy %s: insufficient cash", d.symbol)
                    continue
                proposed_value = remaining
                proposed_weight = proposed_value / total_equity

            if proposed_value < self.min_trade_value:
                logger.debug("Skipping buy %s: value %.0f < min %.0f", d.symbol, proposed_value, self.min_trade_value)
                continue

            # Sector constraint check
            sector = sector_map.get(d.symbol)
            if sector:
                current_sector_w = current_sector_weights.get(sector, 0.0)
                if current_sector_w + proposed_weight > self.max_sector_pct:
                    # Scale down to sector limit
                    allowed = max(0, self.max_sector_pct - current_sector_w)
                    proposed_weight = allowed
                    proposed_value = total_equity * allowed
                    if proposed_value < self.min_trade_value:
                        continue
                current_sector_weights[sector] = current_sector_w + proposed_weight

            price = prices.get(d.symbol)
            if not price or price <= 0:
                logger.warning("No price for %s, skipping buy", d.symbol)
                continue

            qty = max(1, int(proposed_value / price))
            actual_value = qty * price

            buy_orders.append(OrderLeg(
                symbol=d.symbol,
                side="buy",
                quantity=qty,
                estimated_price=price,
                estimated_value=actual_value,
                reason=d.reason,
            ))
            total_buy_value += actual_value

        # ── 5. Turnover check ──
        total_sell_value = sum(o.estimated_value or 0 for o in sell_orders)
        turnover = (total_sell_value + total_buy_value) / (2 * total_equity) if total_equity > 0 else 0

        if turnover > self.max_turnover:
            # Trim lowest-priority buys
            logger.warning("Turnover %.1f%% exceeds max %.1f%%, trimming", turnover * 100, self.max_turnover * 100)
            while turnover > self.max_turnover and buy_orders:
                removed = buy_orders.pop()
                total_buy_value -= (removed.estimated_value or 0)
                turnover = (total_sell_value + total_buy_value) / (2 * total_equity) if total_equity > 0 else 0

        # ── 6. Final check: no-op if both sides empty ──
        if not sell_orders and not buy_orders:
            return self._no_op(
                "No executable trades after constraint checks",
                strategy_id, environment,
            )

        # ── 7. Build summary ──
        net_change = total_buy_value - total_sell_value
        remaining_cash = available - net_change

        summary = AllocationSummary(
            action="rebalance",
            net_cash_change=net_change,
            remaining_cash_estimate=remaining_cash,
            turnover_pct=turnover,
            sector_distribution=current_sector_weights if current_sector_weights else {},
        )

        plan = PortfolioDecision(
            strategy_id=strategy_id,
            run_time=datetime.now(timezone.utc),
            environment=environment,
            sell_orders=sell_orders,
            buy_orders=buy_orders,
            summary=summary,
        )

        logger.info(
            "Portfolio plan: %d sells ($%.0f), %d buys ($%.0f), net=$%.0f, turnover=%.1f%%",
            len(sell_orders), total_sell_value,
            len(buy_orders), total_buy_value,
            net_change, turnover * 100,
        )
        return plan

    def _no_op(self, reason: str, strategy_id: str, environment: str) -> PortfolioDecision:
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


def _urgency_rank(urgency: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(urgency, 0)
