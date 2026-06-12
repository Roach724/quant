# Futu 模拟交易系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入 Futu 模拟交易账户，策略产生交易信号 → FutuStockBroker 下单。支持多策略共享单一账户的虚拟子账户架构。

**Architecture:** 新建 `trading/` 模块与实验系统隔离。核心创新是 **虚拟子账户**：每个策略分配固定资金池，策略内部维护虚拟持仓，交易引擎聚合净仓位后向 Futu 下单。状态持久化到 SQLite + BQ。

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy + futu-api + React/Ant Design Pro

---

## 文件结构

```
trading/                          # 新建 — 交易模块（与实验隔离）
├── __init__.py
├── models.py                     # SQLAlchemy: TradingStrategy, VirtualAccount, TradeRecord
├── config.py                     # YAML 配置加载
├── capital.py                    # 虚拟子账户资金分配
├── runner.py                     # 交易运行器主循环
├── state.py                      # 策略状态持久化
├── signal_bridge.py              # 策略信号 → Futu 订单转换
└── adapter.py                    # 复用现有策略的适配层

admin/frontend/src/pages/
├── TradingCenter.tsx              # 重写 — 4 个一级 Tab 的交易中心
├── TradingDashboard.tsx           # 新建 — 量化看板（模拟/实盘子 Tab）
├── TradingStrategies.tsx          # 新建 — 量化交易策略管理（原 TradingSim）
├── TradingConfig.tsx              # 新建 — 量化配置模板管理
├── TradingAccount.tsx             # 新建 — 交易账户页（模拟/真实子Tab + 下单）
├── TradingSim.tsx                 # 删除 — 合并到 TradingStrategies
├── TradingReal.tsx                # 删除 — 合并到 TradingStrategies

admin/
├── server.py                      # 修改 — 交易 API 端点
├── worker.py                      # 修改 — 增加 trading 任务类型支持

config/
├── trading_sim_template.yaml      # 新建 — 模拟交易配置模板
```

---

### Task 1: 交易数据模型

**Files:**
- Create: `trading/__init__.py`
- Create: `trading/models.py`

- [ ] **Step 1: 创建交易 SQLAlchemy 模型**

```python
# trading/models.py
"""交易模块数据模型 — SQLAlchemy ORM"""

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

Base = declarative_base()

class TradingStrategy(Base):
    """注册的策略配置"""
    __tablename__ = "trading_strategies"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)
    market = Column(String(8), nullable=False)        # us / hk
    strategy_class = Column(String(64), nullable=False) # SimpleMomentum, MACD, etc.
    capital_allocated = Column(Float, nullable=False)  # 分配资金
    config_yaml = Column(Text, nullable=False)          # 策略参数 YAML
    status = Column(String(16), default="stopped")      # running / stopped / paused
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class VirtualAccount(Base):
    """虚拟子账户 — 每个策略一个，资金隔离"""
    __tablename__ = "trading_virtual_accounts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    cash = Column(Float, nullable=False, default=0.0)
    initial_capital = Column(Float, nullable=False)
    peak_equity = Column(Float, nullable=False, default=0.0)
    total_commission = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class VirtualPosition(Base):
    """虚拟持仓 — 策略级别的持仓记录"""
    __tablename__ = "trading_virtual_positions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)             # LONG / SHORT
    qty = Column(Integer, nullable=False)
    avg_entry_price = Column(Float, nullable=False)
    market_value = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class TradeRecord(Base):
    """交易记录 — 每笔成交"""
    __tablename__ = "trading_trade_records"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)              # BUY / SELL
    qty = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    commission = Column(Float, nullable=False, default=0.0)
    order_id = Column(String(64))
    signal_info = Column(JSON)                            # 原始信号信息
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 2: 初始化数据库**

```python
# trading/__init__.py
"""交易模块 — Futu 模拟/真实交易"""

from .models import Base, TradingStrategy, VirtualAccount, VirtualPosition, TradeRecord

def init_db(db_path: str = "/var/quant/trading/trading.db"):
    """初始化交易数据库"""
    import os
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return engine
```

- [ ] **Step 3: 验证模型可创建**

```bash
cd /opt/quant-prod && python3 -c "
from trading.models import Base
from sqlalchemy import create_engine
import os
os.makedirs('/tmp/trading_test', exist_ok=True)
engine = create_engine('sqlite:////tmp/trading_test/trading.db')
Base.metadata.create_all(engine)
print('Tables created:', list(Base.metadata.tables.keys()))
"
# Expected: Tables created: ['trading_strategies', 'trading_virtual_accounts', 'trading_virtual_positions', 'trading_trade_records']
```

- [ ] **Step 5: Commit**

```bash
git add trading/__init__.py trading/models.py
git commit -m "feat(trading): 交易数据模型 — SQLAlchemy ORM"
```

---

### Task 2: 虚拟子账户资金管理

**Files:**
- Create: `trading/capital.py`

- [ ] **Step 1: 实现 CapitalManager**

```python
# trading/capital.py
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
    
    def allocate(self, strategy_id: int, initial_capital: float) -> VirtualAccount:
        """为策略分配虚拟子账户。已存在则返回现有账户。"""
        existing = self.session.query(VirtualAccount).filter_by(
            strategy_id=strategy_id
        ).first()
        if existing:
            # 重置资金（重新开始）
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
    
    def update_position(
        self, strategy_id: int, symbol: str, side: str,
        qty: int, price: float, commission: float,
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
                    strategy_id=strategy_id, symbol=symbol,
                    side="LONG", qty=qty, avg_entry_price=price,
                )
                self.session.add(pos)
        else:  # SELL
            if not pos or pos.qty < qty:
                raise ValueError(f"Cannot sell {qty} {symbol}: only {pos.qty if pos else 0} held")
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
            strategy_id=strategy_id, symbol=symbol,
            side=side, qty=qty, price=price, commission=commission,
        )
        self.session.add(record)
        self.session.commit()
    
    def aggregate_positions(self) -> dict[str, int]:
        """聚合所有策略的净仓位 — 用于向 Futu 下单"""
        all_positions = self.session.query(VirtualPosition).all()
        net: dict[str, int] = {}
        for p in all_positions:
            net[p.symbol] = net.get(p.symbol, 0) + p.qty
        return net
    
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
```

- [ ] **Step 2: 单元测试**

```bash
cd /opt/quant-prod && python3 -c "
from trading.models import Base
from trading.capital import CapitalManager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
os.makedirs('/tmp/trading_test', exist_ok=True)
engine = create_engine('sqlite:////tmp/trading_test/trading.db')
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
s = S()
mgr = CapitalManager(s)
acct = mgr.allocate(1, 100000)
print(f'Allocated: cash={acct.cash}')
mgr.update_position(1, 'HK.00700', 'BUY', 100, 500.0, 5.0)
mgr.update_position(1, 'HK.00700', 'SELL', 50, 510.0, 2.5)
pos = mgr.get_positions(1)
print(f'Positions: {len(pos)}, qty={pos[0].qty if pos else 0}')
print(f'Cash: {mgr.get_account(1).cash:.2f}')
print('Test passed')
"
# Expected: Cash should reflect buys - sells - commissions
```

- [ ] **Step 3: Commit**

```bash
git add trading/capital.py
git commit -m "feat(trading): 虚拟子账户 CapitalManager"
```

---

### Task 3: 策略适配层 — 复用现有策略产生交易信号

**Files:**
- Create: `trading/adapter.py`

- [ ] **Step 1: 实现 StrategyAdapter**

```python
# trading/adapter.py
"""复用现有策略 — 将 engine.strategy.Strategy 适配到交易系统"""

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
    side: str                    # buy / sell / close
    weight: float = 1.0
    qty: Optional[int] = None
    order_type: str = "market"   # market / limit
    limit_price: Optional[float] = None
    score: float = 0.0
    strategy_id: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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
        
        logger.info("Loaded %s with %d symbols", self.strategy_name, len(symbols))
    
    def generate_signals(self, ctx, bar: int, strategy_id: int) -> list[TradingSignal]:
        """调用策略的 on_bar，返回 TradingSignal 列表"""
        if not self._strategy:
            return []
        try:
            signals = self._strategy.on_bar(ctx, bar)
        except Exception:
            logger.exception("Strategy %s.on_bar failed at bar %d", self.strategy_name, bar)
            return []
        return [
            TradingSignal(
                symbol=s.symbol, side=s.side,
                weight=getattr(s, 'weight', 1.0) or 1.0,
                order_type=getattr(s, 'order_type', 'market') or 'market',
                limit_price=getattr(s, 'limit_price', None),
                score=getattr(s, 'score', 0.0) or 0.0,
                strategy_id=strategy_id,
            )
            for s in signals
        ]
```

- [ ] **Step 2: 验证能加载已有策略**

```bash
cd /opt/quant-prod && python3 -c "
from trading.adapter import StrategyAdapter
a = StrategyAdapter('SimpleMomentum', {'lookback': 20, 'top_k': 5}, 'us')
print(f'Loaded: {a.strategy_name}')
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add trading/adapter.py
git commit -m "feat(trading): StrategyAdapter — 复用现有策略"
```

---

### Task 4: 交易信号桥接 — 信号 → Futu 订单

**Files:**
- Create: `trading/signal_bridge.py`

- [ ] **Step 1: 实现 SignalBridge**

```python
# trading/signal_bridge.py
"""信号 → Futu 订单桥梁 — 处理资金约束、佣金、滑点"""

from __future__ import annotations
import logging
import asyncio
from typing import Optional

from trading.adapter import TradingSignal
from trading.capital import CapitalManager
from oms.broker.futu_stock_broker import FutuStockBroker
from oms.broker import BrokerOrder

logger = logging.getLogger(__name__)


class SignalBridge:
    """接收策略信号 → 检查资金约束 → 通过 FutuStockBroker 下单
    
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
        execution_algo: Optional[str] = None,       # "twap" | "vwap" | None
        execution_slices: int = 10,                  # 拆单份数
        execution_window: int = 1800,                # 执行窗口（秒），默认 30min
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
        return None  # 不拆分，直接全量下单
    
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
        self, signal: TradingSignal, current_price: float,
    ) -> Optional[list[BrokerOrder]]:
        """执行单个信号。
        
        如果配置了 execution_algo，拆单执行并返回多个 BrokerOrder。
        否则一次性全量下单。
        """
        acct = self.capital.get_account(signal.strategy_id)
        if not acct:
            logger.warning("No account for strategy %d, skipping", signal.strategy_id)
            return None
        
        exec_price = self._exec_price(signal, current_price)
        
        # 计算数量（weight * cash / price）
        if signal.qty is None:
            weight = signal.weight or 1.0
            cash_avail = acct.cash * weight
            qty = max(1, int(cash_avail / exec_price))
        else:
            qty = signal.qty
        
        if qty <= 0:
            return None
        
        commission = self._commission(qty, exec_price)
        
        # 资金检查
        if signal.side == "buy":
            required = qty * exec_price + commission
            if required > acct.cash:
                qty = max(1, int((acct.cash - commission) / exec_price))
                if qty <= 0:
                    logger.debug("Insufficient cash for %s buy", signal.symbol)
                    return None
                commission = self._commission(qty, exec_price)
        
        # 通过 Futu broker 下单（可选执行算法拆分）
        executor = self._get_executor()
        try:
            if executor is not None:
                # 用执行算法拆单
                signal_dict = {"symbol": signal.symbol, "side": signal.side, "qty": qty}
                orders = await executor.run(signal_dict, self.broker)
            else:
                # 直接全量下单
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
        total_filled = sum(int(o.filled_qty) if o.filled_qty else qty // len(orders) for o in orders) if orders else 0
        if total_filled > 0:
            avg_price = sum(float(o.avg_price) * (int(o.filled_qty) if o.filled_qty else 1)
                           for o in orders) / max(len(orders), 1)
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
```

- [ ] **Step 2: Commit**

```bash
git add trading/signal_bridge.py
git commit -m "feat(trading): SignalBridge — 信号→Futu 订单"
```

---

### Task 5: 交易状态持久化

**Files:**
- Create: `trading/state.py`

- [ ] **Step 1: 实现 TradingStateManager**

```python
# trading/state.py
"""交易状态持久化 — SQLite + JSON 双写"""

from __future__ import annotations
import json
import logging
import os
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
    - BQ 存储交易记录和权益曲线（长期分析）
    """
    
    def __init__(self, session: DBSession, state_dir: str = "/var/quant/trading/state"):
        self.session = session
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
    
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
    
    def restore_positions(self, strategy_id: int) -> list[VirtualPosition]:
        """从 DB 恢复策略的虚拟持仓"""
        return self.session.query(VirtualPosition).filter_by(
            strategy_id=strategy_id
        ).all()
    
    def reset_strategy(self, strategy_id: int):
        """重置策略 — 清除所有状态"""
        self.session.query(VirtualPosition).filter_by(
            strategy_id=strategy_id
        ).delete()
        self.session.query(VirtualAccount).filter_by(
            strategy_id=strategy_id
        ).delete()
        self.session.commit()
        # 清除 checkpoint
        path = self.state_dir / f"strategy_{strategy_id}.json"
        path.unlink(missing_ok=True)
        logger.info("Strategy %d reset", strategy_id)
```

- [ ] **Step 2: Commit**

```bash
git add trading/state.py
git commit -m "feat(trading): TradingStateManager — 状态持久化"
```

---

### Task 6: 交易运行器主循环

**Files:**
- Create: `trading/runner.py`

- [ ] **Step 1: 实现 TradingRunner**

```python
# trading/runner.py
"""交易运行器 — 数据源 → 策略信号 → Futu 下单的主循环"""

from __future__ import annotations
import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session as DBSession

from engine.data import DataFrameSource
from engine.strategy import StrategyContext
from live.bq_datasource import BQDataSource
from trading.adapter import StrategyAdapter
from trading.capital import CapitalManager
from trading.state import TradingStateManager
from trading.signal_bridge import SignalBridge
from oms.broker.futu_stock_broker import FutuStockBroker
from trading.models import TradingStrategy as TSModel

logger = logging.getLogger(__name__)


class TradingRunner:
    """交易运行器。
    
    每个已启用的策略在独立的数据轮询循环中运行。
    多个策略共享同一个 Futu broker 连接。
    """
    
    def __init__(
        self,
        broker: FutuStockBroker,
        capital: CapitalManager,
        state: TradingStateManager,
        bridge: SignalBridge,
        db: DBSession,
        market: str = "us",
        bar_interval: int = 60,
    ):
        self.broker = broker
        self.capital = capital
        self.state = state
        self.bridge = bridge
        self.db = db
        self.market = market
        self.bar_interval = bar_interval
        self._running = False
        self._adapters: dict[int, StrategyAdapter] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._stop_events: dict[int, threading.Event] = {}
    
    def _load_strategies(self) -> list[TSModel]:
        """从 DB 加载所有 running 状态的策略"""
        return self.db.query(TSModel).filter(TSModel.status == "running").all()
    
    def start_strategy(self, strat: TSModel):
        """启动单个策略"""
        if strat.id in self._threads and self._threads[strat.id].is_alive():
            logger.warning("Strategy %d already running", strat.id)
            return
        
        import yaml
        cfg = yaml.safe_load(strat.config_yaml) or {}
        strategy_kwargs = cfg.get("strategy", {})
        kwargs = {k: v for k, v in strategy_kwargs.items() if k != "name"}
        
        adapter = StrategyAdapter(strat.strategy_class, kwargs, strat.market)
        self._adapters[strat.id] = adapter
        
        self.capital.allocate(strat.id, strat.capital_allocated)
        
        stop_event = threading.Event()
        self._stop_events[strat.id] = stop_event
        
        thread = threading.Thread(
            target=self._run_strategy_loop,
            args=(strat.id, adapter, stop_event),
            daemon=True,
            name=f"trading-{strat.id}",
        )
        self._threads[strat.id] = thread
        thread.start()
        logger.info("Started strategy %s (#%d) with $%.0f", strat.name, strat.id, strat.capital_allocated)
    
    def stop_strategy(self, strategy_id: int):
        """停止单个策略"""
        if strategy_id in self._stop_events:
            self._stop_events[strategy_id].set()
            logger.info("Stop signal sent to strategy %d", strategy_id)
    
    def _run_strategy_loop(self, strategy_id: int, adapter: StrategyAdapter, stop: threading.Event):
        """策略主循环 — 轮询 BQ 数据 → 生成信号 → 下单"""
        try:
            # 初始化数据源和上下文
            source = BQDataSource(market=self.market, symbols=[])
            source.start()
            
            # 等待初始化数据
            time.sleep(5)
            
            bar_count = 0
            while not stop.is_set():
                try:
                    bar_data = source.get_latest()
                    if bar_data is None:
                        time.sleep(self.bar_interval)
                        continue
                    
                    ctx = self._build_context(bar_data, adapter._symbols)
                    if adapter._strategy is None:
                        adapter.load(adapter._symbols, ctx)
                    
                    signals = adapter.generate_signals(ctx, bar_count, strategy_id)
                    if signals:
                        logger.info("Strategy %d: %d signals at bar %d", strategy_id, len(signals), bar_count)
                        self._execute_signals(signals, bar_data)
                    
                    bar_count += 1
                    time.sleep(self.bar_interval)
                    
                except Exception:
                    logger.exception("Error in strategy %d loop", strategy_id)
                    time.sleep(self.bar_interval)
        finally:
            logger.info("Strategy %d loop exited", strategy_id)
    
    def _build_context(self, bar_data: dict, symbols: list[str]) -> StrategyContext:
        """构造策略上下文"""
        import pandas as pd
        close = pd.DataFrame([bar_data.get("close", {})])
        src = DataFrameSource(close=close)
        from engine.portfolio import Portfolio
        pf = Portfolio(initial_capital=0)
        return StrategyContext(data=src, portfolio=pf, config={"symbols": symbols})
    
    def _execute_signals(self, signals, bar_data: dict):
        """执行信号列表"""
        close_prices = bar_data.get("close", {})
        for sig in signals:
            current_price = close_prices.get(sig.symbol, 0)
            if current_price <= 0:
                continue
            asyncio.run(self.bridge.execute(sig, current_price))
```

- [ ] **Step 2: Commit**

```bash
git add trading/runner.py
git commit -m "feat(trading): TradingRunner — 策略主循环"
```

---

### Task 7: Admin API 端点 — 交易管理

**Files:**
- Modify: `admin/server.py` — 添加交易 API 端点

- [ ] **Step 1: 添加交易 API 路由**

```python
# 在 admin/server.py 末尾添加

from trading.models import TradingStrategy as TSModel, VirtualAccount, VirtualPosition, TradeRecord
from trading.capital import CapitalManager
from trading.state import TradingStateManager

# ── Trading API ────────────────────────────────────────────────────

@app.get("/api/admin/trading/strategies")
def admin_trading_strategies():
    """列出所有交易策略"""
    session = get_session()
    strats = session.query(TSModel).all()
    result = []
    for s in strats:
        acct = session.query(VirtualAccount).filter_by(strategy_id=s.id).first()
        positions = session.query(VirtualPosition).filter_by(strategy_id=s.id).all()
        result.append({
            "id": s.id, "name": s.name, "market": s.market,
            "strategy_class": s.strategy_class, "status": s.status,
            "capital_allocated": s.capital_allocated,
            "cash": acct.cash if acct else s.capital_allocated,
            "equity": (acct.cash if acct else 0) + sum(p.market_value for p in positions),
            "positions": len(positions),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    return result


@app.post("/api/admin/trading/strategies")
def admin_trading_create_strategy(body: dict = Body(...)):
    """创建交易策略"""
    session = get_session()
    strat = TSModel(
        name=body["name"], market=body["market"],
        strategy_class=body["strategy_class"],
        capital_allocated=float(body.get("capital_allocated", 100000)),
        config_yaml=body.get("config_yaml", ""),
        status="stopped",
    )
    session.add(strat)
    session.commit()
    return {"id": strat.id, "status": "created"}


@app.post("/api/admin/trading/strategies/{strategy_id}/start")
def admin_trading_start_strategy(strategy_id: int):
    """启动交易策略"""
    session = get_session()
    strat = session.get(TSModel, strategy_id)
    if not strat:
        raise HTTPException(404, "Strategy not found")
    strat.status = "running"
    strat.updated_at = datetime.now(timezone.utc)
    session.commit()
    # TODO: 触发 runner 启动策略
    return {"status": "ok", "strategy_id": strategy_id}


@app.post("/api/admin/trading/strategies/{strategy_id}/stop")
def admin_trading_stop_strategy(strategy_id: int):
    """停止交易策略"""
    session = get_session()
    strat = session.get(TSModel, strategy_id)
    if not strat:
        raise HTTPException(404, "Strategy not found")
    strat.status = "stopped"
    strat.updated_at = datetime.now(timezone.utc)
    session.commit()
    return {"status": "ok", "strategy_id": strategy_id}


@app.get("/api/admin/trading/strategies/{strategy_id}/trades")
def admin_trading_trades(strategy_id: int, limit: int = 100):
    """策略交易记录"""
    session = get_session()
    trades = session.query(TradeRecord).filter_by(
        strategy_id=strategy_id
    ).order_by(TradeRecord.created_at.desc()).limit(limit).all()
    return [{
        "id": t.id, "symbol": t.symbol, "side": t.side,
        "qty": t.qty, "price": t.price, "commission": t.commission,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in trades]
```

- [ ] **Step 2: Commit**

```bash
git add admin/server.py
git commit -m "feat(trading): Admin API — 交易策略 CRUD"
```

---

### Task 8: 交易中心 — 4 Tab 大改版

**文件:**
- Create: `admin/frontend/src/pages/TradingCenter.tsx` (重写)
- Create: `admin/frontend/src/pages/TradingDashboard.tsx`
- Create: `admin/frontend/src/pages/TradingDashboardPanel.tsx`
- Create: `admin/frontend/src/pages/TradingStrategies.tsx`
- Create: `admin/frontend/src/pages/StrategyPanel.tsx`
- Create: `admin/frontend/src/pages/TradingConfig.tsx`
- Create: `admin/frontend/src/pages/ConfigPanel.tsx`
- Create: `admin/frontend/src/pages/TradingAccount.tsx`
- Create: `admin/frontend/src/pages/AccountPanel.tsx`
- Delete: `admin/frontend/src/pages/TradingSim.tsx`
- Delete: `admin/frontend/src/pages/TradingReal.tsx`
- Modify: `admin/frontend/src/App.tsx`

架构：

```
交易中心 (TradingCenter)
├── 量化看板 (TradingDashboard)
│   ├── 模拟看板  ← TradingDashboardPanel env="sim"
│   └── 实盘看板  ← TradingDashboardPanel env="real" (预留)
├── 量化交易 (TradingStrategies)
│   ├── 模拟策略  ← StrategyPanel env="sim" (原 TradingSim 逻辑)
│   └── 实盘策略  ← StrategyPanel env="real" (预留)
├── 量化配置 (TradingConfig)
│   ├── 模拟配置  ← ConfigPanel env="sim" (对标实验配置页)
│   └── 实盘配置  ← ConfigPanel env="real" (预留)
└── 交易账户 (TradingAccount)
    ├── 模拟账户  ← AccountPanel env="sim"
    └── 真实账户  ← AccountPanel env="real" (预留)
```

- [ ] **Step 1: TradingCenter.tsx — 4 Tab 外壳**

```tsx
import { Tabs } from 'antd';
import { useState } from 'react';
import TradingDashboard from './TradingDashboard';
import TradingStrategies from './TradingStrategies';
import TradingConfig from './TradingConfig';
import TradingAccount from './TradingAccount';

export default function TradingCenter() {
  const [tab, setTab] = useState('dashboard');
  return (
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: 'dashboard', label: '量化看板', children: <TradingDashboard /> },
      { key: 'strategies', label: '量化交易', children: <TradingStrategies /> },
      { key: 'config', label: '量化配置', children: <TradingConfig /> },
      { key: 'account', label: '交易账户', children: <TradingAccount /> },
    ]} />
  );
}
```

- [ ] **Step 2: TradingDashboard.tsx + TradingDashboardPanel.tsx — 量化看板**

```tsx
// TradingDashboard.tsx
import { Tabs } from 'antd'; import { useState } from 'react';
import TradingDashboardPanel from './TradingDashboardPanel';
export default function TradingDashboard() {
  const [sub, setSub] = useState('sim');
  return <Tabs activeKey={sub} onChange={setSub} items={[
    { key: 'sim', label: '模拟看板', children: <TradingDashboardPanel env="sim" /> },
    { key: 'real', label: '实盘看板', children: <TradingDashboardPanel env="real" /> },
  ]} />;
}

// TradingDashboardPanel.tsx — 对标实验看板
// 数据源: /api/admin/trading/strategies/{id}/equity + positions + trades
// 复用 ExperimentDetail 的 Dashboard 展示逻辑: 权益曲线 + 回撤图 + 持仓表 + 交易记录
```

- [ ] **Step 3: TradingStrategies.tsx + StrategyPanel.tsx — 量化交易**

```tsx
// TradingStrategies.tsx
import { Tabs } from 'antd'; import { useState } from 'react';
import StrategyPanel from './StrategyPanel';
export default function TradingStrategies() {
  const [sub, setSub] = useState('sim');
  return <Tabs activeKey={sub} onChange={setSub} items={[
    { key: 'sim', label: '模拟策略', children: <StrategyPanel env="sim" /> },
    { key: 'real', label: '实盘策略', children: <StrategyPanel env="real" /> },
  ]} />;
}

// StrategyPanel.tsx — 原 TradingSim 逻辑
// ProTable: 名称 | 市场 | 策略类 | 状态(Tag) | 分配资金 | 现金 | 权益 | 持仓数 | 操作(启停)
// 顶部概览卡片: 策略数 / 总权益 / 可用现金
// 新建策略 Modal: 名称 / 市场(US/HK) / 策略类 / 分配资金
// API: GET/POST /api/admin/trading/strategies, POST start/stop
import { useRef, useState } from 'react';
import ProTable from '@ant-design/pro-table';
import { Button, Space, message, Modal, Form, Input, Select, InputNumber, Popconfirm, Tag, Card, Statistic, Row, Col } from 'antd';
import { PlayCircleOutlined, PauseCircleOutlined, PlusOutlined } from '@ant-design/icons';
import { api } from '../api';

function StrategyPanel({ env }: { env: string }) {
  const actionRef = useRef<any>();
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [summary, setSummary] = useState({ totalEquity: 0, totalCash: 0, strategies: 0 });

  const columns = [
    { title: '名称', dataIndex: 'name' },
    { title: '市场', dataIndex: 'market', width: 60, render: (v: string) => <Tag>{v?.toUpperCase()}</Tag> },
    { title: '策略', dataIndex: 'strategy_class' },
    { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag color={v === 'running' ? 'green' : 'default'}>{v}</Tag> },
    { title: '分配资金', dataIndex: 'capital_allocated', render: (v: number) => `$${v?.toLocaleString()}` },
    { title: '现金', dataIndex: 'cash', render: (v: number) => `$${v?.toLocaleString()}` },
    { title: '权益', dataIndex: 'equity', render: (v: number) => `$${v?.toLocaleString()}` },
    { title: '持仓', dataIndex: 'positions' },
    { title: '操作', key: 'actions', width: 160, render: (_: any, r: any) => r.status !== 'running'
      ? <Button size="small" type="primary" icon={<PlayCircleOutlined />}
          onClick={async () => { await api.post(`/api/admin/trading/strategies/${r.id}/start`); actionRef.current?.reload(); }}>启动</Button>
      : <Popconfirm title="停止交易？" onConfirm={async () => { await api.post(`/api/admin/trading/strategies/${r.id}/stop`); actionRef.current?.reload(); }}>
          <Button size="small" danger icon={<PauseCircleOutlined />}>停止</Button></Popconfirm> },
  ];

  return (<>
    <Row gutter={16} style={{ marginBottom: 16 }}>
      <Col span={8}><Card><Statistic title="策略数" value={summary.strategies} /></Card></Col>
      <Col span={8}><Card><Statistic title="总权益" value={summary.totalEquity} prefix="$" precision={0} /></Card></Col>
      <Col span={8}><Card><Statistic title="可用现金" value={summary.totalCash} prefix="$" precision={0} /></Card></Col>
    </Row>
    <ProTable headerTitle={env === 'sim' ? '模拟策略' : '实盘策略'} actionRef={actionRef} rowKey="id" search={false}
      columns={columns} pagination={{ pageSize: 20 }}
      request={async () => { const d = await api.get('/api/admin/trading/strategies'); setSummary({ strategies: d.length, totalEquity: d.reduce((s: number, x: any) => s + (x.equity || 0), 0), totalCash: d.reduce((s: number, x: any) => s + (x.cash || 0), 0) }); return { data: d, success: true, total: d.length }; }}
      toolBarRender={() => [<Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建策略</Button>]} />
    <Modal title="新建交易策略" open={createOpen} onCancel={() => setCreateOpen(false)}
      onOk={async () => { const v = await form.validateFields(); await api.post('/api/admin/trading/strategies', v); message.success('Created'); setCreateOpen(false); actionRef.current?.reload(); }}>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="策略名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="market" label="市场" initialValue="us"><Select options={[{value:'us',label:'US'},{value:'hk',label:'HK'}]} /></Form.Item>
        <Form.Item name="strategy_class" label="策略类" rules={[{ required: true }]}><Input placeholder="SimpleMomentum" /></Form.Item>
        <Form.Item name="capital_allocated" label="分配资金" initialValue={100000}><InputNumber min={1000} style={{ width: '100%' }} /></Form.Item>
      </Form>
    </Modal>
  </>);
}
```

- [ ] **Step 4: TradingConfig.tsx + ConfigPanel.tsx — 量化配置**

```tsx
// TradingConfig.tsx — 对标实验配置页
import { Tabs } from 'antd'; import { useState } from 'react';
import ConfigPanel from './ConfigPanel';
export default function TradingConfig() {
  const [sub, setSub] = useState('sim');
  return <Tabs activeKey={sub} onChange={setSub} items={[
    { key: 'sim', label: '模拟配置', children: <ConfigPanel env="sim" /> },
    { key: 'real', label: '实盘配置', children: <ConfigPanel env="real" /> },
  ]} />;
}

// ConfigPanel.tsx
// YAML 配置模板列表 (读取 config/trading_*.yaml)
// 新建/编辑/查看/重命名/删除 — 对标现有 ConfigsTab 组件
// API: GET /api/admin/experiments/configs (复用, 前缀过滤 trading_sim_ / trading_real_)
```

- [ ] **Step 5: TradingAccount.tsx + AccountPanel.tsx — 交易账户**

```tsx
// TradingAccount.tsx
import { Tabs } from 'antd'; import { useState } from 'react';
import AccountPanel from './AccountPanel';
export default function TradingAccount() {
  const [sub, setSub] = useState('sim');
  return <Tabs activeKey={sub} onChange={setSub} items={[
    { key: 'sim', label: '模拟账户', children: <AccountPanel env="sim" /> },
    { key: 'real', label: '真实账户', children: <AccountPanel env="real" /> },
  ]} />;
}

// AccountPanel.tsx — 账户信息 + 手动下单 + 持仓 + 订单
import { useState, useEffect } from 'react';
import { Card, Row, Col, Statistic, Table, Button, Space, Input, InputNumber, Select, Radio, Tag, message } from 'antd';
import { api } from '../api';

function AccountPanel({ env }: { env: 'sim' | 'real' }) {
  const [acct, setAcct] = useState<any>({});
  const [orders, setOrders] = useState<any[]>([]);
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [symbol, setSymbol] = useState('');
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market');
  const [price, setPrice] = useState(0);
  const [qty, setQty] = useState(100);
  const [loading, setLoading] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try { const [a, o] = await Promise.all([api.get(`/api/admin/trading/account/${env}`), api.get(`/api/admin/trading/orders/${env}`)]); setAcct(a); setOrders(o); }
    catch { message.error('加载失败'); }
    setLoading(false);
  };
  useEffect(() => { fetchData(); const i = setInterval(fetchData, 10000); return () => clearInterval(i); }, [env]);

  const placeOrder = async () => {
    if (!symbol) { message.warning('请输入代码'); return; }
    try { const r = await api.post(`/api/admin/trading/order/${env}`, { symbol, side, qty, order_type: orderType, limit_price: orderType === 'limit' ? price : undefined }); message.success(`已提交: ${r.order_id}`); fetchData(); }
    catch (e: any) { message.error(`下单失败: ${e.message}`); }
  };

  const posCols = [
    { title: '代码', dataIndex: 'symbol', width: 100 },
    { title: '市值', dataIndex: 'market_value', render: (v: number) => `$${v?.toLocaleString()}` },
    { title: '数量', dataIndex: 'qty' },
    { title: '成本价', dataIndex: 'avg_entry_price', render: (v: number) => `$${(v||0).toFixed(2)}` },
    { title: '盈亏', dataIndex: 'unrealized_pnl', render: (v: number) => <span style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>${(v||0).toFixed(2)}</span> },
  ];
  const ordCols = [
    { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag>{v}</Tag> },
    { title: '代码', dataIndex: 'symbol', width: 100 },
    { title: '方向', dataIndex: 'side', width: 50, render: (v: string) => <Tag color={v === 'buy' ? 'green' : 'red'}>{v?.toUpperCase()}</Tag> },
    { title: '数量', dataIndex: 'qty', width: 60 },
    { title: '类型', dataIndex: 'order_type', width: 60 },
    { title: '时间', dataIndex: 'created_at', width: 160 },
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card><Statistic title="资产净值" value={acct.equity || 0} prefix="$" precision={0} /></Card></Col>
        <Col span={6}><Card><Statistic title="持仓市值" value={acct.market_value || 0} prefix="$" precision={0} /></Card></Col>
        <Col span={6}><Card><Statistic title="持仓盈亏" value={acct.total_pnl || 0} prefix="$" precision={2} valueStyle={{ color: (acct.total_pnl || 0) >= 0 ? '#3f8600' : '#cf1322' }} /></Card></Col>
        <Col span={6}><Card><Statistic title="盈亏率" value={acct.pnl_pct || 0} suffix="%" precision={2} valueStyle={{ color: (acct.pnl_pct || 0) >= 0 ? '#3f8600' : '#cf1322' }} /></Card></Col>
      </Row>
      <Card title="下单" size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Radio.Group value={side} onChange={e => setSide(e.target.value)}>
              <Radio.Button value="buy" style={{ color: '#3f8600' }}>买入</Radio.Button>
              <Radio.Button value="sell" style={{ color: '#cf1322' }}>卖出</Radio.Button>
            </Radio.Group>
            <Select value={orderType} onChange={setOrderType} style={{ width: 100 }} options={[{value:'market',label:'市价单'},{value:'limit',label:'限价单'}]} />
          </Space>
          <Space>
            <Input placeholder="代码 (e.g. HK.00700)" value={symbol} onChange={e => setSymbol(e.target.value)} style={{ width: 160 }} />
            <span>价格:</span>
            <Button size="small" onClick={() => setPrice(p => Math.max(0, p - 0.1))}>-</Button>
            <InputNumber value={price} onChange={v => setPrice(v || 0)} step={0.1} style={{ width: 100 }} disabled={orderType === 'market'} />
            <Button size="small" onClick={() => setPrice(p => p + 0.1)}>+</Button>
            <span>数量:</span>
            <Button size="small" onClick={() => setQty(q => Math.max(1, q - 1))}>-</Button>
            <InputNumber value={qty} onChange={v => setQty(v || 1)} step={1} min={1} style={{ width: 80 }} />
            <Button size="small" onClick={() => setQty(q => q + 1)}>+</Button>
            <span>金额: ${((price || 0) * qty).toLocaleString()}</span>
            <Button type="primary" onClick={placeOrder}>下单</Button>
          </Space>
        </Space>
      </Card>
      <Card title="持仓" size="small" style={{ marginBottom: 16 }}>
        <Table dataSource={acct.positions || []} columns={posCols} rowKey="symbol" size="small" pagination={false} loading={loading} />
      </Card>
      <Card title="订单" size="small">
        <Table dataSource={orders} columns={ordCols} rowKey="broker_id" size="small" pagination={{ pageSize: 20 }} loading={loading} />
      </Card>
    </div>
  );
}
```

- [ ] **Step 6: App.tsx — 侧边栏路由**

```tsx
const menuData = [
  { path: '/market', name: '行情中心', icon: <LineChartOutlined /> },
  { path: '/trade', name: '交易中心', icon: <DollarOutlined /> },
  { path: '/board', name: '实验看板', icon: <DashboardOutlined /> },
  { path: '/lab', name: '实验管理', icon: <ExperimentOutlined /> },
  { path: '/models', name: '模型 & 策略', icon: <DashboardOutlined /> },
  { path: '/data', name: '数据中心', icon: <CloudServerOutlined /> },
  { path: '/logs', name: '日志中心', icon: <FileTextOutlined /> },
  { path: '/cron', name: '调度中心', icon: <ClockCircleOutlined /> },
  { path: '/cache', name: '缓存管理', icon: <DatabaseOutlined /> },
];
// 路由: <Route path="/trade" element={<TradingCenter />} />
```

- [ ] **Step 7: Commit**

```bash
git add admin/frontend/src/pages/TradingCenter.tsx \
        admin/frontend/src/pages/TradingDashboard.tsx \
        admin/frontend/src/pages/TradingDashboardPanel.tsx \
        admin/frontend/src/pages/TradingStrategies.tsx \
        admin/frontend/src/pages/StrategyPanel.tsx \
        admin/frontend/src/pages/TradingConfig.tsx \
        admin/frontend/src/pages/ConfigPanel.tsx \
        admin/frontend/src/pages/TradingAccount.tsx \
        admin/frontend/src/pages/AccountPanel.tsx \
        admin/frontend/src/App.tsx
git rm admin/frontend/src/pages/TradingSim.tsx admin/frontend/src/pages/TradingReal.tsx
git commit -m "feat(trading): 交易中心 4 Tab 大改版"
```

---

### Task 11: Futu 账户 API 端点

**Files:**
- Modify: `admin/server.py`

- [ ] **Step 1: 账户信息 + 持仓 + 订单 + 下单 API**

```python
# admin/server.py — 交易账户 API
from oms.broker.futu_stock_broker import FutuStockBroker
from futu import TrdEnv

@app.get("/api/admin/trading/account/{env}")
def admin_trading_account(env: str):
    """获取模拟/真实账户概览"""
    trd_env = TrdEnv.SIMULATE if env == "sim" else TrdEnv.REAL
    broker = FutuStockBroker(trd_env=trd_env)
    async def _get():
        acct = await broker.get_account()
        positions = await broker.get_positions()
        broker._get_ctx().close()
        mkt_val = sum(p.market_value for p in positions)
        pnl = sum(p.unrealized_pnl for p in positions)
        init = max(acct.equity - pnl, 1)
        return {
            "cash": acct.cash, "equity": acct.equity,
            "market_value": mkt_val, "total_pnl": pnl,
            "pnl_pct": pnl / init * 100,
            "positions": [{"symbol": p.symbol, "qty": p.qty, "avg_entry_price": p.avg_entry_price, "market_value": p.market_value, "unrealized_pnl": p.unrealized_pnl} for p in positions],
        }
    import asyncio; return asyncio.run(_get())

@app.get("/api/admin/trading/orders/{env}")
def admin_trading_orders(env: str):
    """获取订单列表"""
    trd_env = TrdEnv.SIMULATE if env == "sim" else TrdEnv.REAL
    broker = FutuStockBroker(trd_env=trd_env)
    async def _get():
        pending = await broker.get_open_orders()
        broker._get_ctx().close()
        return [{"broker_id": o.broker_id, "symbol": o.symbol, "side": o.side, "qty": o.qty, "filled_qty": o.filled_qty, "order_type": o.order_type, "status": o.status, "limit_price": o.limit_price, "created_at": o.created_at.isoformat() if o.created_at else None} for o in pending]
    import asyncio; return asyncio.run(_get())

@app.post("/api/admin/trading/order/{env}")
def admin_trading_place_order(env: str, body: dict = Body(...)):
    """手动下单"""
    trd_env = TrdEnv.SIMULATE if env == "sim" else TrdEnv.REAL
    broker = FutuStockBroker(trd_env=trd_env)
    async def _place():
        order = await broker.submit_order(symbol=body["symbol"], side=body["side"], qty=int(body["qty"]), order_type=body.get("order_type", "market"), limit_price=body.get("limit_price"))
        broker._get_ctx().close()
        return {"order_id": order.broker_id, "status": order.status}
    import asyncio; return asyncio.run(_place())
```

---

### Task 12: 策略对手动交易鲁棒 — Reconciliation

**Files:**
- Modify: `trading/capital.py`

- [ ] **Step 1: reconcile 方法**

```python
# trading/capital.py — 新增方法
import asyncio, logging
logger = logging.getLogger(__name__)

def reconcile(self, broker_positions: dict[str, int]):
    """Reconcile 虚拟持仓与 Futu 实际持仓。偏差时同步虚拟到实际。"""
    virtual = {p.symbol: p.qty for p in self.session.query(VirtualPosition).all()}
    for symbol, actual_qty in broker_positions.items():
        virt_qty = virtual.get(symbol, 0)
        if virt_qty != actual_qty:
            delta = actual_qty - virt_qty
            logger.warning("Position drift: %s virtual=%d actual=%d (delta=%+d)", symbol, virt_qty, actual_qty, delta)
            if actual_qty == 0:
                self.session.query(VirtualPosition).filter_by(symbol=symbol).delete()
            else:
                pos = self.session.query(VirtualPosition).filter_by(symbol=symbol).first()
                if pos: pos.qty = actual_qty
    self.session.commit()
    logger.info("Reconciliation complete")

def reconcile_and_continue(self, strategy_id: int):
    """策略恢复前检查偏差 — 不一致时同步到实际，记录 checkpoint。"""
    virtual = {p.symbol: p.qty for p in self.get_positions(strategy_id)}
    from oms.broker.futu_stock_broker import FutuStockBroker
    async def _get(): b = FutuStockBroker(); p = await b.get_positions(); b._get_ctx().close(); return {x.symbol: int(x.qty) for x in p}
    actual = asyncio.run(_get())
    drift = any(virtual.get(s, 0) != actual.get(s, 0) for s in set(virtual) | set(actual))
    if drift:
        self.reconcile(actual)
        from trading.state import TradingStateManager
        TradingStateManager(self.session).save_checkpoint(strategy_id, {"status": "dirty", "reason": "manual trade?", "virtual": virtual, "actual": actual})
    return drift
```

- [ ] **Step 2: 集成到 TradingRunner**

在 `_run_strategy_loop` 中每 N 个 bar 调用 `reconcile_and_continue`，默认 N=10。

---

## 实施路线图

```
Phase 1 (本次): Task 1-5 + Task 7-9 + Task 11-12 → 基础设施 + Admin UI + 账户功能
Phase 2 (下周): Task 6 + Task 10          → 交易运行器 + 端到端验证
Phase 3 (后续): 真实交易接入              → TrdEnv.REAL + 解锁交易
```

## 虚拟子账户架构

```
         ┌─────────────────────────────────┐
         │        Futu 模拟账户             │
         │   ┌─────────┐ ┌─────────┐       │
         │   │策略A虚拟 │ │策略B虚拟 │       │
         │   │$100K    │ │$50K     │ ...   │
         │   └─────────┘ └─────────┘       │
         │         净仓位聚合层              │
         │         FutuStockBroker          │
         │   → 实际下单                     │
         └─────────────────────────────────┘
```

- 每个策略分配固定资金，独立 P&L；暂停时清净仓位即可
- 定期 reconcile 虚拟 vs 实际，用户手动调仓自动对齐

