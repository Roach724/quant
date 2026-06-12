# 风控引擎 & 回测系统修复方案

> 基于 `docs/audit_risk_engine.md` 审计报告  
> 日期: 2026-06-12

---

## 分阶段实施

| 阶段 | 问题数 | 预估 PR | 影响范围 |
|------|--------|---------|----------|
| **P0** | 3 | 1 PR | 核心计算正确性，必须立即修 |
| **P1** | 3 | 1 PR | 潜在致命 + 回测正确性 |
| **P2** | 5 | 2 PR | 精度/一致性 |
| **P3** | 5 | 按需 | 技术债，不紧急 |

---

## P0 — 立即修复 (3 项)

### F1. `Portfolio.total_equity` 改为返回含持仓市值

- **文件:** `engine/portfolio.py:73-74`
- **改前:** `return self.cash`
- **改后:**
  ```python
  @property
  def total_equity(self) -> float:
      return self._mark_to_market({})
  ```
- **验证点:**
  - `_mark_to_market({})` 会用 `_last_prices` 兜底，初始状态下 `_last_prices` 为空、无持仓 → 退化为 `self.cash`
  - `mark_and_record()` 在每次 bar 更新前填充 `_last_prices` → 有持仓后 `total_equity` 自动包含市值
  - 运行现有测试确认不破坏

### F2. `Position.add()` 卖出时不再重算 avg_entry

- **文件:** `engine/portfolio.py:18-24`
- **改后:**
  ```python
  def add(self, size: int, price: float):
      new_total = self.size + size
      if new_total == 0:
          self.avg_entry = 0.0
          self._total_cost = 0.0
      elif size > 0:
          # 买入: 累加成本, 重算均价
          self._total_cost += size * price
          self.avg_entry = self._total_cost / new_total
      else:
          # 卖出: 按成本比例减少 _total_cost, avg_entry 不变
          if self.size > 0:
              self._total_cost += size * self.avg_entry  # size < 0
      self.size = new_total
  ```
- **验证点:**
  - 买 100@$150 → avg=$150, cost=$15,000
  - 卖 50@$200 → avg 仍为 $150, cost=$7,500
  - unrealized PnL = 50×(210-150) = $3,000 ✅ (之前错误报告 $5,500)

### F3. PaperBroker 移除随机价格噪声

- **文件:** `oms/broker/__init__.py:111`
- **改前:** `fill_price = current_price + random.uniform(-0.5, 0.5)`
- **改后:** `fill_price = current_price`
- **影响:** 回测结果可复现；滑点统一由 runner 层的 `slippage_bps` 处理

---

## P1 — 本迭代 (3 项)

### F4. `_execute_fill` 区分买卖方向

- **文件:** `oms/broker/__init__.py:84-89`
- **改后:**
  ```python
  def _execute_fill(self, order: BrokerOrder, fill_price: float):
      if order.side == "buy":
          self.cash -= fill_price * order.qty
      else:
          self.cash += fill_price * order.qty
      order.filled_qty = order.qty
      order.avg_price = fill_price
  ```
- **说明:** 当前 Runner 层自己管理 Portfolio.cash 绕过 Broker，此修复是防御性的

### F5. RiskGateway 去掉哑元组合

- **文件:** `oms/risk_gateway.py:51-57`
- **方案:**
  1. 删除 `_dummy_portfolio()` 方法
  2. `check()` 方法签名改为 `async def check(self, orders, portfolio, bar_data)`
  3. 更新 `oms/__init__.py` 中所有调用点传入真实 Portfolio
- **说明:** 确保未来任何调用此接口的代码不会静默绕过风控

### I1. WalkForward 传递持仓状态

- **文件:** `engine/walkforward.py:47-48`
- **方案:**
  1. Train 结束后 `result.portfolio` 已经包含最终状态
  2. Test Engine 初始化时接受可选的 `initial_portfolio_state` 参数
  3. 将 train 的 `positions`、`cash`、`_peak_equity` 复制到 test 的 Portfolio
- **验证:** `walkforward.py` 测试覆盖

---

## P2 — 下迭代 (5 项)

### I2. `_slice` 补全 OHLCV

- **文件:** `engine/walkforward.py:82-87`
- **改后:**
  ```python
  def _slice(self, start, end):
      common = dict(
          open=self.data.open.iloc[start:end],
          high=self.data.high.iloc[start:end],
          low=self.data.low.iloc[start:end],
          volume=self.data.volume.iloc[start:end],
      ) if all(hasattr(self.data, f) for f in ['open','high','low','volume']) and \
           all(getattr(self.data, f) is not None for f in ['open','high','low','volume']) \
           else {}
      return DataFrameSource(
          close=self.data.close.iloc[start:end].copy(),
          pred=..., **common)
  ```

### I3. Live runner 扣款统一用 exec_price

- **文件:** `live/runner.py`
- **分析:** 需要对比 `on_live_bar` 和 `_process_signal` 中扣款逻辑，确认差异根因后统一
- **要点:** 确保 `fill_price`（传到 commission 计算）和 `price`（传到扣款）是同一个值

### I4. 买入权重归一化 (engine.py)

- **文件:** `engine/engine.py:28-33`
- **方案:** 在 `_signals_to_orders` 中对所有 buy/target 信号做归一化：
  ```python
  total_weight = sum(s.weight or 1.0 for s in buy_signals)
  if total_weight > 0:
      for s in buy_signals:
          weight = (s.weight or 1.0) / total_weight
  ```

### I5. 限价单日内触发检测

- **文件:** `oms/broker/__init__.py`
- **方案:** `update_price()` 接收 `bar_data`（含 open/high/low），检查当日高低点是否穿越限价

### I6. Metrics 年化统一为 CAGR

- **文件:** `engine/metrics.py`, `paper_run/metrics.py`
- **方案:** `annual_return` 统一用 `(end_equity / start_equity) ** (252 / n_days) - 1`

### I7. MaxLeverage 区分净/总敞口

- **文件:** `engine/risk/exposure.py`
- **方案:** `MaxLeverage` 改算净敞口（不取 abs），新增 `GrossExposureLimit` 算总敞口

---

## P3 — 技术债 (5 项，按需)

| # | 内容 | 思路 |
|---|------|------|
| S1 | 交易成本模型分散 | 抽离 `TransactionCost` 配置对象，按 market/asset_class 提供默认值 |
| S2 | RiskMonitor 告警语义 | `abs(dd)` 显示正数 |
| S3 | 部分成交模拟 | 基于 bar volume × liquidity_factor 限制 `filled_qty` |
| S4 | 日期间隙 resample | `equity_curve.resample('D').ffill()` |
| S5 | VolatilityTarget/SectorCap 空壳 | 抛 `NotImplementedError` 或实现 |

---

## 影响分析

### 会改变现有行为的修复

| 修复 | 影响 | 缓解 |
|------|------|------|
| **F1** total_equity | 所有风控规则的分母变大 → 杠杆/敞口限制放宽 → 可能有更多订单通过 | 运行 existing paper 实验对比权益曲线 |
| **F2** Position.add | 持仓的 unrealized PnL 降低 → 权益曲线可能小幅下移 | 对比修复前后的 live 实验数据 |
| **F3** 随机噪声 | 回测不再有随机方差 → 结果可复现 | 无负面影响 |

### 不改变现有行为的修复

| 修复 | 原因 |
|------|------|
| F4 Broker 方向 | Runner 层不依赖 Broker 现金 |
| F5 哑元组合 | 当前无代码调用 |
| I1-I7 | 精度/正确性改善 |

---

## 执行计划

```
PR#1 (P0): F1 + F2 + F3
  ↓ 合并 → CD 部署
  ↓ 验证实验数据 (对比权益曲线)
PR#2 (P1): F4 + F5 + I1
  ↓ 合并 → CD 部署
PR#3 (P2-前半): I2-I4
PR#4 (P2-后半): I5-I7
P3: 按需，不排期
```
