# Quant-Dev 风险引擎与回测系统全面审计报告

**审计日期:** 2026-06-12  
**审计范围:** 风控引擎、回测/模拟引擎、订单管理 (OMS)、策略基类  
**审计员:** quant-analyst (子代理)

---

## 执行摘要

本次审计发现 **5 个致命缺陷**、**7 个重要问题**、**5 个建议改进**。最严重的问题集中在：

1. **`Portfolio.total_equity` 属性只返回现金余额**，导致所有风险检查（最大回撤、敞口限制、杠杆限制）完全失效
2. **`Position.add()` 在卖出时错误地重新计算平均入场价**，导致未实现盈亏追踪失真
3. **PaperBroker 的模拟成交使用随机价格**，使回测不可复现
4. **PaperBroker._execute_fill 不区分买卖方向、不计佣金**，导致纸交易账户状态错误
5. **WalkForward 不传递投资组合状态**，跨 fold 失去连续性

---

## 🔴 致命缺陷 (Fatal)

### F1. `Portfolio.total_equity` 仅返回现金余额

- **文件:** `engine/portfolio.py:73-74`
- **代码:**
  ```python
  @property
  def total_equity(self) -> float:
      return self.cash
  ```
- **问题:** 该属性命名为 `total_equity`，但只返回 `self.cash`，完全忽略所有持仓的市值。这是一个语义与实现严重背离的 Bug。
- **影响范围:** 极其广泛，影响所有依赖此属性的模块：

  | 调用方 | 文件:行号 | 后果 |
  |--------|----------|------|
  | `MaxDrawdown.apply()` | `engine/risk/drawdown.py:8` | 满仓时 `total_equity ≈ 0`，触发 100% 回撤误报，**拒绝所有后续订单** |
  | `ExposureLimit.apply()` | `engine/risk/exposure.py:16` | 满仓时分母 ≈ 0，`new_pct` 走 `else: 0` 分支，**敞口限制完全绕过** |
  | `MaxLeverage.apply()` | `engine/risk/exposure.py:33` | 分母使用纯现金，杠杆率被严重高估，**误触发杠杆限制** |
  | `convert_signal()` | `oms/bridge.py:30` | 仓位规模计算只用现金，**低估可用资金** |
  | `_signals_to_orders()` | `engine/engine.py:31` | 同上 |
  | `run_paper.py:_signals_to_orders()` | `run_paper.py:464` | 同上 |

- **严重级别:** 🔴 **致命**
- **修复建议:**
  ```python
  @property
  def total_equity(self) -> float:
      return self._mark_to_market(self._last_prices or {})
  ```
  即：调用已有的 `_mark_to_market` 方法，传入最近已知价格。同时需要确保 `_last_prices` 在每次 bar 更新前被正确填充（当前 `mark_and_record` 已做此事）。

---

### F2. `Position.add()` 卖出时错误重算平均入场价

- **文件:** `engine/portfolio.py:18-24`
- **代码:**
  ```python
  def add(self, size: int, price: float):
      new_total = self.size + size
      if new_total == 0:
          self.avg_entry = 0.0
          self._total_cost = 0.0
      else:
          self._total_cost += size * price    # 卖出时 size<0，用成交价抵扣成本
          self.avg_entry = self._total_cost / new_total
      self.size = new_total
  ```
- **问题:** 卖出时 (`size < 0`)，`_total_cost += (-qty) * sell_price` 将卖出价当作"负成本"抵扣，导致 `avg_entry` 被拉向卖出价方向。

  **示例:** 持有 100 股 AAPL @ $150 → `_total_cost=15000`。卖出 50 股 @ $200 → `_total_cost = 15000 + (-50*200) = 5000`，`avg_entry = 5000/50 = $100`。**正确值应保持 $150**（成本基础不因卖出而改变）。

- **影响:** 卖出后持仓的未实现盈亏计算失真。若市价 $210，错误报告 unrealized PnL = 50 × ($210 - $100) = $5500（实际应为 50 × ($210 - $150) = $3000），**虚增 83% 的浮盈**。这会污染权益曲线和所有依赖 Mark-to-Market 的指标。

- **严重级别:** 🔴 **致命**
- **修复建议:** 卖出时应仅减少 `size` 和 `_total_cost`（按 `avg_entry` 比例扣除），保持 `avg_entry` 不变：
  ```python
  if size < 0:
      # 卖出: 按成本比例减少 _total_cost，avg_entry 保持不变
      if self.size > 0:
          self._total_cost += size * self.avg_entry  # size 为负
      self.size = new_total
      if self.size == 0:
          self.avg_entry = 0.0
          self._total_cost = 0.0
  else:
      # 买入: 正常累加
      self._total_cost += size * price
      self.avg_entry = self._total_cost / new_total
      self.size = new_total
  ```

---

### F3. PaperBroker 模拟成交价格包含随机噪声

- **文件:** `oms/broker/__init__.py:111`
- **代码:**
  ```python
  # Market order: fill immediately
  fill_price = current_price + random.uniform(-0.5, 0.5)
  ```
- **问题:** 每次市价单执行在 `current_price` 上叠加 ±$0.50 的随机抖动。这导致：
  - ✗ 回测结果**不可复现**（每次运行结果不同）
  - ✗ 引入的方差**不来自任何市场微观结构模型**，是纯噪声
  - ✗ 滑点应该用 `slippage_bps` 模型，已有专门的滑点计算逻辑在 runner 层，这里不应额外加噪
- **严重级别:** 🔴 **致命**
- **修复建议:** 移除 `random.uniform`，直接使用传入的 `current_price`：
  ```python
  fill_price = current_price  # 滑点和佣金由 runner 层统一处理
  ```

---

### F4. PaperBroker._execute_fill 不区分买卖方向且不扣除佣金

- **文件:** `oms/broker/__init__.py:84-89`
- **代码:**
  ```python
  def _execute_fill(self, order: BrokerOrder, fill_price: float):
      cost = fill_price * order.qty
      self.cash -= cost    # 卖单也应该加现金！
      ...
      order.filled_qty = order.qty
      order.avg_price = fill_price
      ...
  ```
- **问题:**
  1. **未区分买卖方向:** 卖单执行后 `self.cash` 被减少而非增加（`self.cash -= fill_price * qty` 对卖单是错误的，应 `+=`）。
  2. **未扣除佣金:** 纸交易账户的现金余额不反映佣金成本，导致 Broker 层与 Portfolio 层现金不一致。
  3. **未应用滑点:** `fill_price` 来自 `current_price + random.uniform(...)`，但 Runner 层另行计算了带滑点的 `exec_price`，两个价格不一致。

- **影响:** 由于 Runner 层（`run_paper.py` / `live/runner.py`）手动管理 `portfolio.cash` 而非依赖 Broker 的现金状态，当前代码路径下这个 Bug 未直接导致计算错误。但如果任何模块**直接查询 Broker 的现金余额**（如 `RiskMonitor` 调用 `broker.get_account()`），将获得完全错误的账户状态。

- **严重级别:** 🔴 **致命**（潜在资金损失风险：若未来任何模块依赖 Broker 账户状态做决策）
- **修复建议:** 
  ```python
  def _execute_fill(self, order: BrokerOrder, fill_price: float):
      if order.side == "buy":
          self.cash -= fill_price * order.qty
      else:
          self.cash += fill_price * order.qty
      # 滑点和佣金在 Runner 层统一处理，Broker 层只记录成交
  ```

---

### F5. RiskGateway.check() 使用哑元投资组合，所有风险规则被绕过

- **文件:** `oms/risk_gateway.py:51-57`
- **代码:**
  ```python
  def _dummy_portfolio(self):
      class DummyPF:
          initial_capital = 100_000
          total_equity = 100_000
          positions = {}
      return DummyPF()
  ```
- **问题:** `RiskGateway.check()` 将 `self._dummy_portfolio()` 传入 `RiskEngine.check()`，而非真实的投资组合。哑元组合永远持有 $100,000 现金、零持仓，因此：
  - 最大回撤检查：`total_equity - initial_capital = 0`，永远不触发
  - 敞口限制：当前敞口为 0，任何订单都通过
  - 止损检查：无持仓，永远不触发
  - 杠杆检查：`gross / 100000`，永远不触发

- **影响:** 虽然当前 `run_paper.py` 绕过 `RiskGateway.check()` 直接调用 `RiskEngine.check()` 并传入真实 Portfolio，但 `RiskGateway` 的接口存在，若未来任何代码路径调用 `risk_gateway.check()`，**所有风控规则将静默失效**。

- **严重级别:** 🔴 **致命**
- **修复建议:** 移除 `_dummy_portfolio()`，要求调用方传入真实的 Portfolio 对象（接口改为 `async def check(self, orders, portfolio, bar_data)`）。

---

## 🟡 重要问题 (Important)

### I1. WalkForward 不传递投资组合状态到测试集

- **文件:** `engine/walkforward.py:47-48`
- **问题:** 每个 fold 的 train 和 test 分别通过独立的 `Engine.run()` 执行，各创建全新的 `Portfolio`。对于状态依赖策略（使用 `ctx.portfolio` 做决策），test 阶段从空仓开始，与实际"训练后继续交易"的语义不符。
- **严重级别:** 🟡 **重要**
- **建议:** 在 train 结束后将 Portfolio 状态序列化，test Engine 以该状态为起点。

---

### I2. WalkForward._slice 丢弃 OHLCV 数据

- **文件:** `engine/walkforward.py:82-87`
- **代码:**
  ```python
  def _slice(self, start, end):
      close = self.data.close.iloc[start:end].copy()
      pred = ...
      return DataFrameSource(close=close, pred=pred)
  ```
- **问题:** `_slice` 只传递 `close` 和 `pred`，丢失了 `open`、`high`、`low`、`volume` 数据。任何需要这些字段的策略或风险规则（如波动率目标、止损策略依赖日内高低点）将在 WalkForward 中静默失败。
- **严重级别:** 🟡 **重要**
- **建议:** 补全 OHLCV 字段的切片：
  ```python
  DataFrameSource(
      close=close, open=self.data.open.iloc[start:end],
      high=self.data.high.iloc[start:end],
      low=self.data.low.iloc[start:end],
      volume=self.data.volume.iloc[start:end],
      pred=pred
  )
  ```

---

### I3. Live Runner 的 `on_live_bar` 用原始价格而非滑点价格扣款

- **文件:** `live/runner.py` — `on_live_bar` 回调中的成交处理
- **代码 (简化):**
  ```python
  if tracked.side == "buy":
      portfolio.cash -= price * tracked.filled_qty + commission
  ```
- **对比** `_process_signal`（paper 模式）：
  ```python
  self.broker.update_price(sym, price)
  # ... fill happens through broker
  portfolio.cash -= fill_qty * fill_price + commission  # fill_price 已含滑点
  ```
- **问题:** Live 模式使用 `price`（bar 的收盘价）直接扣款，而 commission 却基于 `exec_price`（含滑点）计算。**佣金被高估**（基于更高的名义本金），但实际扣款又用了较低的价格。净效果因滑点方向和买卖方向而异，不是简单的偏移。
- **严重级别:** 🟡 **重要**
- **建议:** 统一使用 `exec_price` 进行现金扣款，与 `_process_signal` 保持一致。

---

### I4. `engine.py` 的 `_signals_to_orders` 未归一化买入权重

- **文件:** `engine/engine.py:28-33`
- **代码:**
  ```python
  elif sig.side == "buy" or sig.side == "target":
      weight = sig.weight or 1.0
      cash_per_symbol = portfolio.total_equity * weight  # 每个信号都拿总权益*权重
      ...
  ```
- **问题:** 若策略同时发出 5 个买入信号，每个 `weight=1.0`，则每笔分配 `total_equity × 1.0` 的资金，合计试图买入 **5 倍于总权益的仓位**。虽然在风险引擎的 Exposure/MaxLeverage 层可能被拦截，但这说明了 **策略层默认行为不符合直觉**。
- **严重级别:** 🟡 **重要**
- **建议:** 自动归一化或要求策略明确传入已归一化的权重。参考 `run_paper.py:307-310` 的做法（`buy_weight = 1.0 / n_buy`）。

---

### I5. PaperBroker 限价单可能永远不会成交

- **文件:** `oms/broker/__init__.py:51-54` (`update_price` 中触发 limit fill)
- **问题:** 限价单仅在 `update_price()` 被调用时检查成交条件。如果在两个 `update_price()` 调用之间价格穿过了限价，限价单不会执行。对于低频（日线）回测这不是问题，但对于 5 分钟线回测，两个 bar 之间的价格变动可能导致限价单的成交时点不准确。
- **严重级别:** 🟡 **重要**
- **建议:** 对于分钟级回测，考虑在每次 `update_price` 时用 `open`/`high`/`low` 判断是否日内触发。当前用收盘价检查可能漏过日内触发。

---

### I6. `engine/metrics.py` 的 Sharpe 比率使用简单乘法复合而非对数收益

- **文件:** `engine/metrics.py:42-50`、`paper_run/metrics.py:107-115`
- **问题:** 两套 metrics 都使用：
  ```python
  total_return = 1.0
  for r in returns:
      total_return *= (1 + r)
  annual_r = total_return ** (periods_per_year / n) - 1
  ```
  对一个收益序列使用直接复合再反算年化，在收益接近 0、波动正常时近似正确。但当回测周期较长、收益率较大时，与对数收益的年化偏差会累积。标准做法是用对数收益或直接用 CAGR：`(end / start) ^ (252/n) - 1`。
- **严重级别:** 🟡 **重要**（对长时间序列的指标精度有影响）
- **建议:** 至少确保两种方式给出相同结果，优选更稳定的对数收益法。

---

### I7. `MaxLeverage.apply()` 将买单和卖单都计入总敞口

- **文件:** `engine/risk/exposure.py:28-36`
- **代码:**
  ```python
  for o in orders:
      gross += abs(o.size * bar_data.get("close", {}).get(o.symbol, 0))
  ```
- **问题:** 计算总敞口时，`abs()` 将卖单也计入，意味着：如果同时有 50 个买单和 50 个卖单，总敞口 = 买单金额 + 卖单金额。实际风险敞口取决于净仓位（买 - 卖），双边加总高估了风险。
- **严重级别:** 🟡 **重要**
- **建议:** 区分净敞口（net exposure）和总敞口（gross exposure）两个独立指标。`MaxLeverage` 应使用净敞口。可新增 `GrossExposureLimit` 规则使用总敞口。

---

## ⚪ 建议改进 (Suggestions)

### S1. 无统一的事务成本模型

- **文件:** `live/config.py:8-10`, `live/runner.py:87-90`, `run_paper.py:136-138`
- **问题:** 佣金和滑点参数在至少三个地方独立定义（`DEFAULT_VALUES`、`LiveRunner.__init__`、`PaperRunner.__init__`），默认值分散，可能导致不同运行模式使用不一致的成本假设。加密货币的费率结构与股票不同，但代码未作区分。
- **严重级别:** ⚪ **建议**
- **建议:** 将交易成本模型抽离为独立配置对象，按市场类型和资产类别提供合理的默认值。

---

### S2. `RiskMonitor.drawdown` 告警消息语义混淆

- **文件:** `oms/risk_monitor.py:56-60`
- **代码:**
  ```python
  dd = (account.equity - self._peak_equity) / self._peak_equity
  if dd < -max_dd:
      self.alerts.fire("critical",
          f"Max drawdown breached: {dd:.2%} (limit: {max_dd:.0%})",
          ...)
  ```
- **问题:** `dd` 是负值（如 -0.25 表示 25% 回撤），但告警消息中显示 `-25%` 回撤、限制为 `20%`，"超过了 20% 限制"的语义不够直观。检查逻辑 `dd < -max_dd` 本身是正确的。
- **严重级别:** ⚪ **建议**
- **建议:** 告警消息使用 `abs(dd)` 显示正的回撤百分比，或将比较逻辑改为人更易读的形式。

---

### S3. 无部分成交模拟

- **文件:** `oms/broker/__init__.py:103-114`
- **问题:** PaperBroker 的市价单总是 100% 成交（`order.filled_qty = order.qty`）。真实市场经常发生部分成交。对于流动性较差的标的或大额订单，这会高估策略执行能力。
- **严重级别:** ⚪ **建议**
- **建议:** 基于当前 bar 的 volume 引入简单的流动性模型：最大可成交量 = min(order.qty, volume × liquidity_factor)，其中 liquidity_factor 可设为 0.01~0.05。

---

### S4. `InvestmentRecord._compute_performance` 未处理日期间隙

- **文件:** `experiment/investment_record.py:136-177`
- **问题:** 每日收益序列由连续日期的 equity 值差分计算。如果权益记录有缺失日期（节假日、停牌日），差分会跨越多日，导致该日收益率异常高/低，进而影响波动率、Sharpe、最大回撤等指标。
- **严重级别:** ⚪ **建议**
- **建议:** 在计算日收益率前对日期序列做 resample（如 `resample('D').ffill()`），确保覆盖率连续。

---

### S5. `VolatilityTarget` 和 `SectorCap` 规则未实现

- **文件:** `engine/risk/volatility_target.py`, `engine/risk/exposure.py:39-45` (SectorCap)
- **问题:** 两个风控规则的 `apply()` 方法直接 `return orders`（无操作）。`SectorCap` 的 `sectors` 参数未被使用。如果有人在策略中添加这些规则期望获得保护，将获得**零保护**。
- **严重级别:** ⚪ **建议**（未实现但已暴露接口，属于文档/接口设计问题）
- **建议:** 实现规则逻辑或显式抛出 `NotImplementedError`。

---

## 审计统计

| 级别 | 数量 | 涉及模块 |
|------|------|----------|
| 🔴 致命 | 5 | portfolio, broker, risk_gateway, risk/drawdown, risk/exposure |
| 🟡 重要 | 7 | walkforward, live/runner, engine, risk/exposure, broker, metrics |
| ⚪ 建议 | 5 | risk_monitor, broker, investment_record, risk/* |

### 建议修复优先级

1. **P0 — 立即修复:** F1 (`total_equity`)、F2 (`Position.add`)、F3 (随机价格)
2. **P1 — 本迭代修复:** F4 (Broker 方向)、F5 (哑元组合)、I1 (WalkForward 状态)
3. **P2 — 下迭代修复:** I2-I7（WalkForward 切片、滑点一致性、权重归一化等）
4. **P3 — 技术债:** S1-S5

---

## 审计方法说明

本次审计采用静态代码审查方式，覆盖以下模块的完整源码：

- `engine/` — 策略基类、投资组合、风控规则、回测引擎、WalkForward、指标
- `oms/` — 订单管理、桥接层、纸交易经纪商、风控网关、风控监控
- `live/` — 实盘/纸交易运行器、配置、状态管理
- `paper_run/` — 纸交易运行器和指标
- `experiment/` — 投资记录
- `run_paper.py` — 纸交易入口

审查从数据流角度追踪资金和信号在系统各层之间的传递，重点检查数值计算正确性、边界条件处理和组件间契约一致性。

*本报告未包含性能分析或生产环境部署审计。*
