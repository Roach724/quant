"""虚拟子账户 — 多策略共享单一 Futu 账户的资金分配"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session as DBSession

from trading.models import VirtualAccount, VirtualPosition, TradeRecord

logger = logging.getLogger(__name__)


class CapitalManager:
    """管理所有策略的虚拟子账户。

    每个策略分配固定的 initial_capital，策略内部独立计算 P&L。
    多个策略的净仓位聚合后发送到 Futu。
    """

    def __init__(self, session: DBSession):
        self.session = session

    # ── Allocation ──

    def allocate(self, strategy_id: int, initial_capital: float) -> VirtualAccount:
        """为策略分配虚拟子账户。已存在则重置资金。"""
        existing = self.session.query(VirtualAccount).filter_by(
            strategy_id=strategy_id
        ).first()
        if existing:
            existing.cash = initial_capital
            existing.initial_capital = initial_capital
            existing.peak_equity = initial_capital
            self.session.commit()
            return existing

        acct = VirtualAccount(
            strategy_id=strategy_id,
            cash=initial_capital,
            initial_capital=initial_capital,
            peak_equity=initial_capital,
        )
        self.session.add(acct)
        self.session.commit()
        logger.info("Allocated $%.0f to strategy %d", initial_capital, strategy_id)
        return acct

    def get_account(self, strategy_id: int) -> Optional[VirtualAccount]:
        return self.session.query(VirtualAccount).filter_by(
            strategy_id=strategy_id
        ).first()

    def get_positions(self, strategy_id: int) -> list[VirtualPosition]:
        return self.session.query(VirtualPosition).filter_by(
            strategy_id=strategy_id
        ).all()

    # ── Position updates ──

    def update_position(
        self,
        strategy_id: int,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        commission: float,
    ):
        """更新虚拟持仓 — 买入累加，卖出扣减"""
        pos = self.session.query(VirtualPosition).filter_by(
            strategy_id=strategy_id, symbol=symbol
        ).first()

        if side == "BUY":
            if pos:
                total_cost = pos.avg_entry_price * pos.qty + price * qty
                pos.qty += qty
                pos.avg_entry_price = total_cost / max(pos.qty, 1)
            else:
                pos = VirtualPosition(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side="LONG",
                    qty=qty,
                    avg_entry_price=price,
                )
                self.session.add(pos)
        else:  # SELL
            if not pos or pos.qty < qty:
                raise ValueError(
                    f"Cannot sell {qty} {symbol}: only {pos.qty if pos else 0} held"
                )
            pos.qty -= qty
            if pos.qty == 0:
                self.session.delete(pos)
                pos = None

        # 更新现金
        acct = self.get_account(strategy_id)
        if acct:
            if side == "BUY":
                acct.cash -= price * qty + commission
            else:
                acct.cash += price * qty - commission

        # 记录交易
        record = TradeRecord(
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            commission=commission,
        )
        self.session.add(record)
        self.session.commit()

    # ── Aggregation ──

    def aggregate_positions(self) -> dict[str, int]:
        """聚合所有策略的净仓位 — 用于向 Futu 下单"""
        all_positions = self.session.query(VirtualPosition).all()
        net: dict[str, int] = {}
        for p in all_positions:
            net[p.symbol] = net.get(p.symbol, 0) + p.qty
        return net

    # ── Lifecycle ──

    def release(self, strategy_id: int):
        """释放策略资金 — 清空持仓和账户"""
        self.session.query(VirtualPosition).filter_by(
            strategy_id=strategy_id
        ).delete()
        self.session.query(VirtualAccount).filter_by(
            strategy_id=strategy_id
        ).delete()
        self.session.commit()
        logger.info("Released capital for strategy %d", strategy_id)
