"""交易状态持久化 — SQLite + JSON checkpoint"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session as DBSession

from trading.models import TradingStrategy, VirtualAccount, VirtualPosition

logger = logging.getLogger(__name__)


class TradingStateManager:
    """交易状态管理。

    - SQLite 存储结构化数据（策略配置、虚拟账户、持仓）
    - JSON checkpoint 存储快照（用于故障恢复）
    """

    def __init__(
        self,
        session: DBSession,
        state_dir: str = "/var/quant/trading/state",
    ):
        self.session = session
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ── Checkpoint ──

    def save_checkpoint(self, strategy_id: int, data: dict):
        """保存策略状态快照（JSON）"""
        path = self.state_dir / f"strategy_{strategy_id}.json"
        data["_saved_at"] = datetime.now(timezone.utc).isoformat()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug("Checkpoint saved: strategy %d", strategy_id)

    def load_checkpoint(self, strategy_id: int) -> Optional[dict]:
        """加载策略状态快照"""
        path = self.state_dir / f"strategy_{strategy_id}.json"
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            logger.warning("Corrupted checkpoint for strategy %d", strategy_id)
            return None

    # ── Position restore ──

    def restore_positions(self, strategy_id: int) -> list[VirtualPosition]:
        """从 DB 恢复策略的虚拟持仓"""
        return self.session.query(VirtualPosition).filter_by(
            strategy_id=strategy_id
        ).all()

    # ── Lifecycle ──

    def reset_strategy(self, strategy_id: int):
        """重置策略 — 清除所有状态"""
        self.session.query(VirtualPosition).filter_by(
            strategy_id=strategy_id
        ).delete()
        self.session.query(VirtualAccount).filter_by(
            strategy_id=strategy_id
        ).delete()
        self.session.commit()
        path = self.state_dir / f"strategy_{strategy_id}.json"
        path.unlink(missing_ok=True)
        logger.info("Strategy %d reset", strategy_id)

    # ── Reconciliation (Task 12) ──

    def reconcile(self, broker_positions: dict[str, int]):
        """Reconcile 虚拟持仓与 Futu 实际持仓。偏差时同步虚拟到实际。"""
        virtual = {
            p.symbol: p.qty
            for p in self.session.query(VirtualPosition).all()
        }
        for symbol, actual_qty in broker_positions.items():
            virt_qty = virtual.get(symbol, 0)
            if virt_qty != actual_qty:
                delta = actual_qty - virt_qty
                logger.warning(
                    "Position drift: %s virtual=%d actual=%d (delta=%+d)",
                    symbol, virt_qty, actual_qty, delta,
                )
                if actual_qty == 0:
                    self.session.query(VirtualPosition).filter_by(
                        symbol=symbol
                    ).delete()
                else:
                    pos = self.session.query(VirtualPosition).filter_by(
                        symbol=symbol
                    ).first()
                    if pos:
                        pos.qty = actual_qty
        self.session.commit()
        logger.info("Reconciliation complete")

    def reconcile_and_continue(self, strategy_id: int) -> bool:
        """策略恢复前检查偏差 — 不一致时同步到实际并记录 checkpoint。"""
        virtual = {p.symbol: p.qty for p in self.restore_positions(strategy_id)}

        from oms.broker.futu_stock_broker import FutuStockBroker
        import asyncio

        async def _get_actual():
            b = FutuStockBroker()
            p = await b.get_positions()
            b._get_ctx().close()
            return {x.symbol: int(x.qty) for x in p}

        actual = asyncio.run(_get_actual())

        drift = any(
            virtual.get(s, 0) != actual.get(s, 0)
            for s in set(virtual) | set(actual)
        )
        if drift:
            self.reconcile(actual)
            self.save_checkpoint(strategy_id, {
                "status": "dirty",
                "reason": "manual trade?",
                "virtual": virtual,
                "actual": actual,
            })
        return drift
