"""信号 → Futu 订单桥梁 — 处理资金约束、佣金、滑点、可选执行算法"""

from __future__ import annotations

import logging

from oms.broker import BrokerOrder
from oms.broker.futu_stock_broker import FutuStockBroker
from trading.adapter import TradingSignal
from trading.capital import CapitalManager

logger = logging.getLogger(__name__)


class SignalBridge:
    """接收策略信号 → 检查资金约束 → 通过 FutuStockBroker 下单。

    可选: 配置执行算法 (TWAP / VWAP) 拆分大单减少市场冲击。
    不配置则一次性全量下单。
    """

    def __init__(
        self,
        broker: FutuStockBroker,
        capital: CapitalManager,
        slippage_bps: float = 5.0,
        commission_bps: float = 1.0,
        min_commission: float = 1.0,
        execution_algo: str | None = None,  # "twap" | "vwap" | None
        execution_slices: int = 10,
        execution_window: int = 1800,
    ):
        self.broker = broker
        self.capital = capital
        self.slippage_bps = slippage_bps
        self.commission_bps = commission_bps
        self.min_commission = min_commission
        self.execution_algo = execution_algo
        self.execution_slices = execution_slices
        self.execution_window = execution_window

    def _get_executor(self):
        """根据配置创建执行算法实例"""
        if self.execution_algo == "twap":
            from execution.twap import TWAPExecutor

            return TWAPExecutor(
                window_seconds=self.execution_window,
                slices=self.execution_slices,
            )
        elif self.execution_algo == "vwap":
            from execution.vwap import VWAPExecutor

            return VWAPExecutor(
                window_seconds=self.execution_window,
                slices=self.execution_slices,
            )
        return None  # 不拆分

    def _exec_price(self, signal: TradingSignal, current_price: float) -> float:
        """计算含滑点的执行价格"""
        slip = current_price * self.slippage_bps / 10000
        if signal.side == "buy":
            return current_price + slip
        return current_price - slip

    def _commission(self, qty: int, exec_price: float) -> float:
        """计算佣金"""
        notional = qty * exec_price
        return max(self.min_commission, notional * self.commission_bps / 10000)

    async def execute(
        self,
        signal: TradingSignal,
        current_price: float,
    ) -> list[BrokerOrder] | None:
        """执行单个信号。配置了执行算法则拆单，否则全量下单。"""
        if self.broker is None:
            from oms.broker.futu_stock_broker import FutuStockBroker

            self.broker = FutuStockBroker()
            logger.info("Lazy-initialized FutuStockBroker")

        acct = self.capital.get_account(signal.strategy_id)
        if not acct:
            logger.warning("No account for strategy %d", signal.strategy_id)
            return None

        exec_price = self._exec_price(signal, current_price)

        # 计算数量
        if signal.qty is None:
            weight = signal.weight or 1.0
            cash_avail = acct.cash * weight
            qty = max(1, int(cash_avail / exec_price))
        else:
            qty = signal.qty

        if qty <= 0:
            return None

        commission = self._commission(qty, exec_price)

        # 买入资金检查
        if signal.side == "buy":
            required = qty * exec_price + commission
            if required > acct.cash:
                qty = max(1, int((acct.cash - commission) / exec_price))
                if qty <= 0:
                    logger.debug("Insufficient cash for %s buy", signal.symbol)
                    return None
                commission = self._commission(qty, exec_price)

        # 下单
        executor = self._get_executor()
        try:
            if executor is not None:
                signal_dict = {
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "qty": qty,
                }
                orders = await executor.run(signal_dict, self.broker)
            else:
                order = await self.broker.submit_order(
                    symbol=signal.symbol,
                    side=signal.side,
                    qty=qty,
                    order_type=signal.order_type,
                    limit_price=signal.limit_price,
                )
                orders = [order] if order else []
        except Exception as e:
            logger.error("Order failed for %s: %s", signal.symbol, e)
            return None

        # 更新虚拟账户（按总成交量）
        total_filled = 0
        total_cost = 0.0
        for o in orders:
            fq = int(o.filled_qty) if o.filled_qty else qty // max(len(orders), 1)
            fp = float(o.avg_price) if o.avg_price else exec_price
            total_filled += fq
            total_cost += fq * fp

        if total_filled > 0:
            avg_price = total_cost / total_filled if total_filled > 0 else exec_price
            actual_comm = self._commission(total_filled, avg_price)
            try:
                self.capital.update_position(
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    side="BUY" if signal.side == "buy" else "SELL",
                    qty=total_filled,
                    price=avg_price,
                    commission=actual_comm,
                )
            except ValueError as e:
                logger.warning("Position update rejected: %s", e)

        return orders
