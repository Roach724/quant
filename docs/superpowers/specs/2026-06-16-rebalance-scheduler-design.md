# RebalanceScheduler — 策略调仓调度器设计

> 状态: Draft | 日期: 2026-06-16 | 作者: Jarvis + 老大

## 背景

当前所有策略的 `on_bar(ctx, bar)` 中，`bar` 被当做"数据点序号"，不知道自己在什么 K 线频率上跑。`rebalance_every` 在回测（日线）和模拟/实盘（5m）语境下含义完全不同：

| 语境 | rebalance_every=5 的真实含义 |
|------|----------------------------|
| 回测（日线） | 每周调一次仓 |
| 模拟（5m） | 每 25 分钟调一次仓 — 不可接受 |
| 实盘（5m） | 同上，还要算佣金、限制、滑点 |

四大场景需要统一的调仓决策逻辑：

| 场景 | 数据频率 | 交易方式 | 是否需要 RebalanceScheduler |
|------|---------|---------|---------------------------|
| 回测 | 日线 | 虚拟交易 | ✅ |
| 模拟实验 | 5m 实时 | 虚拟交易 | ✅ |
| 模拟交易 | 5m 实时 | 券商模拟账号 | ✅ |
| 实盘交易 | 5m 实时 | 真实交易 | ✅ |

## 核心设计

### 模块位置

`trading/scheduler.py` — 独立模块，所有场景共用。

### 接口

```python
class RebalanceScheduler:
    def __init__(
        self,
        freq_minutes: int,       # K 线周期（分钟）：1, 5, 60, 1440
        lookback_bars: int,      # 首次启动回放条数
        rebalance_every: int,    # 调仓间隔（单位：bar）
        state_path: str,         # 持久化文件路径
    ): ...

    def on_bar(self) -> Decision:
        """每根 bar 调用，返回 {{TRADE, SKIP, WAITING}}"""
        ...

    def save(self) -> dict:
        """序列化当前状态"""
        ...

    def load_state(self, bar_count: int, last_rebalance_bar: int) -> None:
        """恢复持久化状态"""
        ...

    def write(self) -> None:
        """写持久化文件"""
        ...
```

### Decision 枚举

```python
class Decision(Enum):
    TRADE   = "trade"    # 可以调仓
    SKIP    = "skip"     # 距离上次调仓不足一个窗口，跳过
    WAITING = "waiting"  # 回放阶段，等待足够数据
```

### 持久化

- 路径：`/var/data/trading/state/strategy_{id}_scheduler.json`
- 格式：`{"bar_count": N, "last_rebalance_bar": M}`
- 独立于现有 checkpoint，互不干扰

### 策略 YAML 配置

```yaml
live:
  market: us
  freq: 5m              # 新增：K 线频率
  lookback_bars: 100     # 新增：首次启动回放条数
```

`rebalance_every` 已有，无需新增。

## 行为模型

### 三种状态

```
               bar < lookback    bar >= lookback       restart (持久化恢复)
               ──────────────    ─────────────────     ────────────────────
on_bar()  →    WAITING           间隔判断 TRADE/SKIP    load_state → 间隔判断
```

### 首次启动

```
lookback_bars = 100, rebalance_every = 5

bar 0...99   → WAITING
bar 100      → 用 0..100 全部数据计算一次 → 做初始调仓 → last_rebalance_bar = 100
bar 101..104 → SKIP（距离上次调仓 < 5 bar）
bar 105      → TRADE → 调仓 → last_rebalance_bar = 105
bar 106..109 → SKIP
bar 110      → TRADE ...
```

关键：回放过程中**不调仓**，只在 `bar == lookback` 时用全部回放数据计算一次。

### 暂停重启

**场景 A：恢复时距离上次调仓不足一个窗口**

```
保存: bar_count=107, last_rebalance=105
重启: load(107, 105)
      107 - 105 = 2 < 5 → SKIP
      继续跑，等到 bar_count 达到 110 时 TRADE
```

**场景 B：恢复时距离上次调仓 >= 一个窗口**

```
保存: bar_count=112, last_rebalance=105
重启: load(112, 105)
      112 - 105 = 7 >= 5 → TRADE（一次）
      用全部新 bar（106..112）做一次计算 → 调仓 → last=112
      之后按正常间隔跑
```

**场景 C：restart 恰好落在一个窗口倍数上**

```
保存: bar_count=110, last_rebalance=105
重启: load(110, 105)
      110 - 105 = 5 >= 5 → TRADE（一次）
      调仓 → last=110
      之后照常
```

### 关键约束

- **从来不逐 bar 追溯调仓**。即使断连超过一个窗口（如 scenario B），也只补算一次，不会"追上"错过的调仓。
- `bar_count` 不包括回放阶段的 bar 数（只计数 live bar）。

## 依赖

### 被谁调用

- `trading/runner.py` — 模拟交易 / 实盘交易
- 回测引擎（待建）
- 模拟实验引擎（`live/run.py` 系列，如接入）

### 调用方式

```python
scheduler = RebalanceScheduler(
    freq_minutes=5,
    lookback_bars=cfg["live"]["lookback_bars"],
    rebalance_every=self.strategy.rebalance_every,
    state_path=f"/var/data/trading/state/strategy_{strategy_id}_scheduler.json",
)

def _on_bar(bar_data):
    dec = scheduler.on_bar()
    if dec == Decision.WAITING:
        return
    if dec == Decision.SKIP:
        # 只积累 bar，不调仓
        return
    if dec == Decision.TRADE:
        signals = adapter.generate_signals(ctx, bar_count - 1, strategy_id)
        self._execute_signals(signals, bar_data)
        scheduler.write()
```

### 不依赖什么

- 不依赖 `StrategyAdapter`、`SignalBridge`、`CapitalManager`
- 不依赖任何 Futu API
- 不依赖 BQ / 数据库
- **纯状态机**，只回答"该不该调仓"

## 潜在风险

1. **K 线频率和 `rebalance_every` 可能产生不合理的间隔**。比如 freq=5m, rebalance_every=5 → 25 分钟。如果用户想要"每天调一次"，应该是 freq=1440, rebalance_every=1。建议在 scheduler 初始化时做合理性校验（间隔 < 1 小时 → 警告日志）。

2. **多个路径共用一个实例**。虽然每个 runner 实例创建独立的 scheduler，但如果回测也引用，注意文件路径冲突。
