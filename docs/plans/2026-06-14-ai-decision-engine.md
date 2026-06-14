# AI 决策引擎 — 系统设计

> 版本: v2  
> 日期: 2026-06-14  
> 状态: 设计审阅中  
> 市场: **初始仅 US 市场** (HK/Crypto 暂不支持)

---

## 1. 概述

### 1.1 定位

在现有 **16 种量化策略** 之上，增加一层 **AI 驱动的元决策引擎**。

- **策略层**: 负责生成交易信号（买入/卖出/权重）
- **AI 决策层**: 负责判断"听谁的、什么时候听、怎么执行"

这不是发明新信号，而是用 AI 做信号质量的二次判断 + 组合层面的全局调配。

> **🔴 市场限制: 初始仅支持 US 市场。** HK 和 Crypto 数据链路不同，暂不纳入第一期。

### 1.2 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    现有策略层 (16 种)                         │
│  SimpleMomentum  MeanReversion  MLPrediction  MACD  RSI2    │
│  BollingerBands  MultiFactorRank  TurtleTrading  QARP  ...  │
│                                                              │
│  ↓ 每条策略独立产出 Signal(symbol, side, score, weight)       │
├─────────────────────────────────────────────────────────────┤
│                  🧠 AI 决策引擎 (新)                          │
│                                                              │
│  ① 召回层 ──→ ② 候选池 ──→ ③ 分析层 ──→ ④ 融合层 ──→ ⑤ 执行层  │
│  聚合信号    阈值过滤    Top-K深度   融合排序    分层决策       │
│                                                              │
│  ↓ 最终输出: 换仓方案 [(buy, sym, qty), (sell, sym, qty)]     │
├─────────────────────────────────────────────────────────────┤
│                    现有执行层                                  │
│  SignalBridge → OMS (FutuStockBroker) → 实际下单               │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 决策回顾

| # | 问题 | 决策 |
|---|------|------|
| 1 | 候选池大小 | 动态大小，按策略信号强度阈值过滤，信号弱的丢弃 |
| 2 | 分析深度 | 仅对 Top-K 标的做全深度 AI 分析（默认 K=10）。数据源优先级: 自有 BQ → llmquant 回退 |
| 3 | 信号融合 | 可配置 A/B。A: 投票制；B: 归一化加权（z-score/百分位）。默认 B |
| 4 | 执行粒度 | **C) 分层** — 先逐标评分（标的层），再汇总给组合层做最终调配（组合层） |

---

## 3. 五层流水线

### 3.0 总览

| 层 | 职责 | 输入 | 输出 | 频率 | 可配置项 |
|---|------|------|------|------|---------|
| ① 召回层 | 收拢所有策略信号 | 16 种策略 → 信号流 | `[(symbol, strategy, signal, score)]` | 可设 | 启用哪些策略 |
| ② 候选池 | 按信号强度过滤 | 召回全量信号 | 过滤后候选集（动态大小） | 同① | `min_signal_threshold` |
| ③ 分析层 | Top-K 深度 AI 分析 | 候选池取 Top-K | 每标的深度报告 + 评分 | 可设 | `top_k`(默认10), 数据源优先级 |
| ④ 融合层 | 多策略信号融合排序 | 策略信号 + AI 分析结果 | 最终排序榜单 | 同③ | 模式 A/B, 权重配置 |
| ⑤ 执行层 | 分层出最终决策 | 排序榜 + 账户 + 约束 | 换仓方案 → OMS | 可设 | 仓位上限/行业集中度/风险预算 |

> **频率独立**: ①-② 为一组，③-④ 为一组，⑤ 为一组，各组可设不同 cron。初始默认全部每天开盘后各执行一次。

### 3.1 ① 召回层 (`recall.py`)

**目的**: 统一收口，把 16 种策略的信号汇总到一个标准结构里。

```
对每只启用的策略:
  strategy.generate_signals(ctx) → [Signal, ...]
  
汇总:
  {symbol: [(strategy_name, signal_direction, score, confidence), ...]}
```

**输出示例**:
```python
{
  "AAPL": [
    ("SimpleMomentum", "buy", 0.82, 0.75),
    ("MeanReversion", "buy", 0.61, 0.60),
    ("RSI2", "sell", -0.45, 0.55),
  ],
  "GOOGL": [
    ("MLPrediction", "buy", 0.91, 0.88),
  ],
  ...
}
```

**关键逻辑**:
- 调用现有 `strategies/*.py` 各策略的 `on_bar()` / 信号生成入口
- 统一信号结构: `(symbol, strategy_name, direction, score, confidence)`
- `direction ∈ {buy, sell, hold}`, `score ∈ [-1, 1]`, `confidence ∈ [0, 1]`
- 记录日志: 本轮召回总数、各策略产出量

**配置**:
```yaml
recall:
  enabled_strategies:
    - SimpleMomentum
    - MeanReversion
    - MLPrediction
    - MACD
    - RSI2
    - BollingerBands
    - MultiFactorRank
    - TurtleTrading
    - QARP
    - MACrossover
    - ATRTrailingStop
    - ShortSqueeze
    - SectorRotation
    - PairsTrading
    - BuyHold
    - FundingRateArbitrage
```

### 3.2 ② 候选池 (`candidate_pool.py`)

**目的**: 过滤弱信号，保留值得分析的候选标的。

```
输入: 召回全量信号 [{symbol, strategy, score, confidence}]
处理:
  1. 合并同 symbol 的多个策略信号（取 score 绝对值最大者，或多信号取均值）
  2. 按 |score| * confidence 算综合强度
  3. 过滤: 综合强度 < threshold → 丢弃
输出: 候选标的列表 [(symbol, aggregate_score, contributing_strategies)]
```

**决策**: 动态大小 — 不设固定 N，只按阈值切。信号少的日子可能只有 3 只，信号强的交易日可能有 50 只。

**配置**:
```yaml
candidate_pool:
  min_signal_threshold: 0.20       # |score| * confidence < 0.20 → 丢弃
  aggregation: "max_abs"           # "max_abs" | "mean" | "weighted_mean"
```

### 3.3 ③ 分析层 (`analyst.py`)

**目的**: 对 Top-K 候选做 AI 深度分析，产出结构化报告。

```
输入: 候选池 [(symbol, score, strategy_list)]
处理:
  1. 按综合强度排序，取 Top-K (默认 10)
  2. 对每只标的并行:
     a. 采集数据 (自有 BQ 优先 → llmquant 回退)
     b. 组装 Prompt
     c. 调 LLM → 结构化 JSON 输出
输出: [(symbol, analysis_report)]
```

#### 3.3.1 数据采集 (`data_provider.py`)

按优先级从两个数据源获取:

| 数据域 | 自有 BQ (优先) | llmquant (回退) |
|--------|---------------|-----------------|
| **日线价格/成交量** | `us_bars_1d`, `hk_bars_1d` | ✅ |
| **5分钟 K 线** | `us_bars_5m`, `hk_bars_5m` | — |
| **技术指标** | factor tables (均线/RSI/MACD/布林/ATR) | ✅ |
| **基本面** | — | ✅ (估值/盈利/成长) |
| **情绪/新闻** | — | ✅ |
| **SEC 文件** | — | ✅ (10-K/10-Q via llmquant-data) |
| **机构持仓** | — | ✅ (13F via llmquant-data) |

**回退逻辑**:
```python
async def get_data(symbol: str, fields: list[str]) -> dict:
    result = {}
    for field in fields:
        data = await try_bigquery(symbol, field)
        if data is None:
            data = await try_llmquant(symbol, field)
        if data is None:
            data = {"status": "unavailable", "reason": "no source"}
        result[field] = data
    return result
```

#### 3.3.2 Prompt 模板 (`prompt_templates/analyst.j2`)

```
You are a quantitative analyst evaluating a stock for a multi-strategy trading system.

## Stock
- Symbol: {{ symbol }}
- Market: {{ market }}

## Strategy Signals (contributing strategies)
{% for s in signals %}
- {{ s.strategy }}: {{ s.direction }}, score={{ s.score }}, confidence={{ s.confidence }}
{% endfor %}

## Technical Data (from own database)
{% if technical.price %}
- Current Price: {{ technical.price }}
- MA20: {{ technical.ma20 }}, MA50: {{ technical.ma50 }}
- RSI(14): {{ technical.rsi }}
- MACD: {{ technical.macd }} (signal: {{ technical.macd_signal }})
- Bollinger: upper={{ technical.bb_upper }}, lower={{ technical.bb_lower }}
- ATR(14): {{ technical.atr }}
{% else %}
- Technical data: unavailable
{% endif %}

## Fundamental Data (from external source)
{% if fundamental %}
- P/E: {{ fundamental.pe }}, Forward P/E: {{ fundamental.forward_pe }}
- Revenue Growth (YoY): {{ fundamental.revenue_growth }}
- Net Margin: {{ fundamental.net_margin }}
- Debt/Equity: {{ fundamental.debt_equity }}
{% else %}
- Fundamental data: unavailable
{% endif %}

## Recent News Sentiment
{% if news %}
{% for item in news %}
- [{{ item.date }}] {{ item.headline }} (sentiment: {{ item.sentiment }})
{% endfor %}
{% else %}
- News: unavailable
{% endif %}

## Task

Output a JSON object with:
1. `direction`: "bullish" | "neutral" | "bearish"
2. `confidence`: 0.0 - 1.0
3. `key_arguments`: list of 3-5 key reasons
4. `risk_factors`: list of 2-3 risk factors
5. `suggested_weight_modifier`: -0.5 to +0.5 (how much to adjust the strategy signal weight)

Respond with ONLY the JSON, no markdown, no explanation.
```

#### 3.3.3 LLM 配置

```yaml
analysis:
  top_k: 10
  llm:
    model: "deepseek/deepseek-v4-pro"
    temperature: 0.3
    max_tokens: 2000
    concurrent: 5                    # 并行分析 5 只标的
  data:
    sources: ["bigquery", "llmquant"]
    timeout_seconds: 30
```

#### 3.3.4 结构化输出

```python
from pydantic import BaseModel

class AnalysisReport(BaseModel):
    symbol: str
    direction: str                     # "bullish" | "neutral" | "bearish"
    confidence: float                  # 0.0 - 1.0
    key_arguments: list[str]           # 3-5 条
    risk_factors: list[str]            # 2-3 条
    suggested_weight_modifier: float   # -0.5 to +0.5
    data_coverage: dict                # 哪些数据源有数据
    timestamp: datetime
```

### 3.4 ④ 融合层 (`fusion.py`)

**目的**: 将策略原始信号与 AI 分析结果融合，产出最终排序。

```
输入:
  - 策略信号: [(symbol, strategy, score, confidence)]
  - AI 分析: [(symbol, AnalysisReport)]
处理:
  按配置模式融合排序
输出: [(symbol, final_score, rank)]
```

#### 模式 A — 投票制

```
final_score = (被多少策略选为 buy/sell) × (AI confidence 加成)

例: AAPL 被 3 个策略看好 → 票数 3
    AI 分析 bullish, confidence 0.8 → 加成 ×1.8
    final_score = 3 × 1.8 = 5.4
```

#### 模式 B — 归一化加权 (默认)

```
Step 1: 策略内 z-score 归一化
  strategy_score_norm = (score - μ_strategy) / σ_strategy

Step 2: 加权求和
  raw = Σ(weight_strategy × strategy_score_norm) + weight_ai × ai_confidence

Step 3: 百分位
  final_score = percentile(raw)

权重:
  momentum:       1.0
  mean_rev:       0.8
  ml_predict:     1.2
  macd:           0.7
  rsi:            0.7
  bollinger:      0.6
  multi_factor:   1.0
  turtle:         0.8
  qarp:           0.9
  ma_crossover:   0.6
  atr_stop:       0.5
  short_squeeze:  0.5
  sector_rotation: 0.8
  pairs_trading:  0.7
  buy_hold:       0.3
  funding_rate:   0.6
  ai_analysis:    1.5               # AI 分析权重最高
```

**配置**:
```yaml
fusion:
  mode: "weighted"                  # "voting" | "weighted"
  weights:                          # 仅 weighted 模式
    momentum: 1.0
    mean_rev: 0.8
    ml_predict: 1.2
    # ... (见上方完整列表)
    ai_analysis: 1.5
```

### 3.5 ⑤ 执行层 (`executor.py`)

**目的**: 分层决策 — 先逐标判断，再组合调配。

```
┌─────────────────────────────────────────┐
│           Stage 1: 标的层                 │
│  per-symbol: 融合排名 + AI报告 + 实时行情  │
│  → action(买/卖/持有/观望) + 建议仓位       │
├─────────────────────────────────────────┤
│           Stage 2: 组合层                 │
│  汇总标的结论 + 账户状态 + 约束             │
│  → 换仓方案 [(buy/sell, sym, qty, reason)]│
└─────────────────────────────────────────┘
```

#### Stage 1 — 标的层 (`stock_evaluator.py`)

对每只标的独立判断:

```
输入 (per symbol):
  - 融合排名 & final_score
  - AI 分析报告 (direction, confidence, arguments, risks)
  - 实时技术指标快照 (当前价格, MA, RSI)
  - 是否已持仓

调 LLM (stock_eval.j2):
  输入以上信息
  输出 JSON:
    - action: "buy" | "sell" | "hold" | "watch"
    - suggested_weight: 0.0 - max_position_pct
    - reason: 一句话理由
    - urgency: "high" | "medium" | "low"
```

**标的层 prompt 关键要素**:
```
You are deciding whether to act on a single stock.

## Stock: {{ symbol }}
## Fusion Rank: #{{ rank }} / {{ total }} (score: {{ final_score }})
## AI Analysis: {{ direction }}, confidence {{ confidence }}
## Current Position: {{ holding.qty | default(0) }} shares ({{ holding.pct | default(0) }}% of portfolio)

## Market Snapshot
- Current Price: {{ price }}
- MA20: {{ ma20 }} (price is {{ 'above' if price > ma20 else 'below' }})
- RSI(14): {{ rsi }}
- Day Change: {{ day_change_pct }}%

## Constraints
- Max position per stock: {{ max_position_pct }}%
- You already hold {{ holding_pct }}% in this stock

Output JSON:
{
  "action": "buy" | "sell" | "hold" | "watch",
  "suggested_weight": 0.05,   // fractional, 0 = close position
  "reason": "string",
  "urgency": "high" | "medium" | "low"
}
```

#### Stage 2 — 组合层 (`portfolio_allocator.py`)

汇总所有标的层结论 + 账户约束 → 统一换仓方案:

```
输入:
  - N 条标的层结论 [(symbol, action, suggested_weight, urgency)]
  - 账户状态:
    - 现金: total_cash, available_cash
    - 现有持仓: [(symbol, qty, market_value, unrealized_pl, weight_pct)]
    - 购买力: buying_power
  - 约束:
    - max_position_pct: 0.15 (单只上限)
    - max_sector_pct: 0.40 (单行业上限)
    - min_cash_reserve: 0.10 (最低现金)
    - max_turnover: 0.30 (单次换手上限)

处理流程:
  1. 冲突检测与消解:
     - 资金不足 → 按 urgency 从低到高削减 buy
     - 单只超限 → 裁剪到 max_position_pct
     - 行业超限 → 同行业内按分数末位淘汰
  2. 买卖配对:
     - 优先 sell → free cash → buy (减少现金消耗)
     - 卖出顺序: urgency=high sell 优先
     - 买入顺序: urgency=high 优先, 同 urgency 看 final_score
  3. 换手率检查:
     - 若计算换手率 > max_turnover → 削减低优先级交易

输出: 最终换仓方案
  - 卖出: [(symbol, qty, price, estimated_value, reason)]
  - 买入: [(symbol, qty, price, estimated_cost, reason)]
  - 摘要:
    - 预计净资金变动
    - 预计剩余现金
    - 预计换手率
    - 行业分布变化
```

**组合层不调 LLM** — 这是纯算法/规则引擎，不需要 AI。

```yaml
execution:
  stock_eval:
    llm:
      model: "deepseek/deepseek-v4-pro"
      temperature: 0.2
    batch_size: 5                   # 并行分析数
  constraints:
    max_position_pct: 0.15          # 单标的上限 15%
    max_sector_pct: 0.40            # 单行业上限 40%
    min_cash_reserve: 0.10          # 最低现金保留 10%
    max_turnover: 0.30              # 单次换手率上限 30%
    min_trade_value: 500            # 最小交易金额 (USD/HKD)
```

---

## 4. 模块结构

```
ai_decision/                          # 新模块
├── __init__.py
├── engine.py                         # 主引擎, 编排五层流水线
├── recall.py                         # ① 召回层: 聚合策略信号
├── candidate_pool.py                 # ② 候选池: 阈值过滤
├── analyst.py                        # ③ 分析层: Top-K 深度 AI 分析
├── fusion.py                         # ④ 融合层: 投票/加权排序
├── executor.py                       # ⑤ 执行层入口: 分层决策编排
│   ├── stock_evaluator.py            #    Stage 1: 标的层 LLM 评估
│   └── portfolio_allocator.py        #    Stage 2: 组合层算法调配
├── data_provider.py                  # 数据获取 (BQ + llmquant fallback)
├── prompt_templates/                 # Jinja2 prompt 模板
│   ├── analyst.j2                    #   分析层: 深度分析
│   ├── stock_eval.j2                 #   执行层 Stage 1: 标的评估
│   └── portfolio.j2                  #   (预留) 组合层备用
├── schemas.py                        # Pydantic 数据模型
├── config.py                         # 配置加载 (从 YAML)
└── default_config.yaml               # 默认配置
```

### 现有模块改动

**后端:**

| 文件 | 改动 | 说明 |
|------|------|------|
| `trading/runner.py` | 新增 AI 决策集成路径 | Runner 可选 "直接执行策略信号" 或 "走 AI 决策引擎" |
| `config/trading/` | 新增 ai 配置段 | 交易模板可引用 AI 决策配置 |
| 无 | cron 任务 | 新 `ai-decision-engine` cron 任务 |

**前端 (Admin UI):**

| 改动 | 说明 |
|------|------|
| 侧边栏新增「🤖 AI 决策」模块 | 独立模块, 标注 "仅 US" |
| API 新增 `/api/admin/ai-decision/*` | 策略 CRUD + 配置管理 + 各层输出查询 |
| Cron 新增 `ai-decision-engine` | 调度 AI 决策流水线 |

**看板集成 (无需新建看板):**

| 改动 | 说明 |
|------|------|
| 实验看板下拉框 | 运行环境=实验 → 实验中可选此 AI 策略 |
| 模拟交易看板下拉框 | 运行环境=模拟交易 → 模拟交易中可选此 AI 策略 |
| 真实交易看板下拉框 | 运行环境=真实交易 → 真实交易中可选此 AI 策略 |

> 除 Runner 的可选集成入口 + Admin 新增页面外，AI 决策引擎是**独立模块**，不改动现有策略代码、OMS、SignalBridge。看板直接复用，不新建。

---

## 5. 分阶段实施

### Phase 1: 数据层 ⭐ 当前阶段

**目标**: 打通数据获取链路，验证 BQ + llmquant 双源回退

**产出**:
- `ai_decision/data_provider.py` — 数据获取抽象层
- `ai_decision/schemas.py` — 核心数据模型
- `ai_decision/config.py` — 配置加载
- `ai_decision/default_config.yaml` — 默认配置
- 测试: 对 3 只美股验证 BQ 读取 + llmquant 回退

**不依赖其他层，可独立验证**

### Phase 2: 召回 + 候选池

**目标**: 信号聚合和过滤链路跑通

**产出**:
- `ai_decision/recall.py` — 调用 16 种策略生成信号
- `ai_decision/candidate_pool.py` — 阈值过滤
- 测试: 在某交易日数据上跑全量 → 输出候选集

### Phase 3: 分析层

**目标**: AI 深度分析链路

**产出**:
- `ai_decision/analyst.py` — Top-K 调度 + LLM 调用
- `ai_decision/prompt_templates/analyst.j2` — 分析 prompt
- 集成 data_provider 数据采集
- 测试: Top-3 标的跑分析 → 验证报告质量

### Phase 4: 融合层

**目标**: 多策略信号融合排序

**产出**:
- `ai_decision/fusion.py` — 投票/加权模式
- 配合分析层结果做端到端排序

### Phase 5: 执行层 + 集成

**目标**: 分层决策 → 换仓方案 → OMS

**产出**:
- `ai_decision/executor/stock_evaluator.py` — 标的层 LLM
- `ai_decision/executor/portfolio_allocator.py` — 组合层算法
- `ai_decision/engine.py` — 主引擎编排
- `trading/runner.py` — AI 决策集成入口
- cron 任务: `ai-decision-engine`

### Phase 6: Admin UI + 监控

**目标**: Admin 管理台新侧边栏模块 + 全链路可视化 + 策略生命周期管理 + 看板集成

---

#### 6.1 侧边栏

Admin 左侧导航新增独立模块:

```
📊 Dashboard
🧪 实验管理
  ├── 配置模板
  ├── 实验室 (Live / Paper / Prod)
  └── ...
📈 数据采集
📋 日志浏览
⏰ Cron
📦 模型 & 策略
🧬 因子管理
🤖 AI 决策 (仅 US)          ← 新增
  ├── 策略概览
  ├── 配置管理
  ├── 召回层
  ├── 分析层
  └── 决策层
```

> 标题带 `(仅 US)` 后缀，提示当前不支持 HK/Crypto。

---

#### 6.2 策略概览 (主页面, 类似实验室)

**功能**: AI 策略的全生命周期管理 — 创建、查看、启动/停止、跳转看板。

##### 6.2.1 页面布局

```
┌─────────────────────────────────────────────────┐
│  🤖 AI 决策 — 策略概览                  [从模板创建] │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌───────────────────────────────────────────┐  │
│  │ 名称        │ 环境   │ 状态  │ 最后运行    │ 操作 │  │
│  ├───────────────────────────────────────────┤  │
│  │ us_ai_v1    │ 模拟交易│ ▶ 运行中│ 06-14 09:35│ ··· │  │
│  │ us_ai_exp   │ 实验   │ ⏸ 已停止│ 06-13 09:40│ ··· │  │
│  │ us_ai_prod  │ 真实交易│ ⏸ 已停止│ --        │ ··· │  │
│  └───────────────────────────────────────────┘  │
│                                                 │
└─────────────────────────────────────────────────┘
```

##### 6.2.2 从模板创建 AI 策略 (创建弹窗)

参照交易策略模板创建模式:

```
┌─────────────────────────────────────────────┐
│  创建 AI 决策策略                            │
│                                             │
│  模板选择:  [ai_decision_default.yaml  ▾]    │
│  策略名称:  [us_ai_v2_______________]        │
│  运行环境:  ○ 实验  ● 模拟交易  ○ 真实交易      │
│  市场:      US (固定)                        │
│                                             │
│  ── 高级选项 (可覆盖模板) ──                  │
│  Top-K:     [10________]                    │
│  融合模式:  [加权 (B)____▾]                  │
│  调度频率:  [30 9 * * 1-5_]                 │
│                                             │
│         [取消]           [创建]              │
└─────────────────────────────────────────────┘
```

- **模板选择**: 下拉列出 `/opt/quant-dev/ai_decision/configs/` 下所有 `*.yaml`
- **运行环境** 决定结果输出到哪里:
  - 实验 → 实验看板 (`/api/admin/dashboard/experiment/*`)
  - 模拟交易 → 模拟交易看板 (`/api/admin/dashboard/trading/sim/*`)
  - 真实交易 → 真实交易看板 (`/api/admin/dashboard/trading/prod/*`)
- 创建后生成独立配置文件，不污染模板

##### 6.2.3 策略操作

每条策略的「操作」菜单:

| 操作 | 说明 | 适用状态 |
|------|------|---------|
| **启动** | 按配置 cron 调度运行 | 已停止 |
| **停止** | 停止调度, 不中断正在运行的决策 | 运行中 |
| **立即运行** | 无视调度, 立即触发一次完整流水线 | 任意 |
| **编辑配置** | 跳转配置管理编辑此策略配置 | 任意 |
| **查看看板** | 根据运行环境跳转对应看板 | 任意 |
| **删除** | 删除策略 + 配置 (二次确认) | 已停止 |

##### 6.2.4 看板集成 (复用现有)

AI 策略 **不建新看板**，直接复用现有看板，通过运行环境自动路由:

```
运行环境=实验      → 实验看板 (Experiments)
                     实验下拉列表中出现此 AI 策略名称
                     
运行环境=模拟交易  → 模拟交易看板 (Trading Dashboard → Sim)
                     模拟交易下拉列表中出现此 AI 策略名称
                     
运行环境=真实交易  → 真实交易看板 (Trading Dashboard → Prod)
                     真实交易下拉列表中出现此 AI 策略名称
```

看板内容 (复用实验/交易看板现有指标):
- 权益曲线 (Cash + Positions = Equity)
- 累积收益 + 回撤图
- PnL 分解 + Sharpe / Calmar / MaxDD / WinRate
- 持仓明细 + 交易记录

> "查看看板" 按钮根据策略的运行环境，直接跳转到对应看板并自动选中当前策略。

---

#### 6.3 配置管理 (子页面)

**功能**: AI 决策配置文件的 CRUD。

##### 6.3.1 页面布局

```
┌─────────────────────────────────────────────────┐
│  🤖 AI 决策 — 配置管理                  [新建配置] │
├─────────────────────────────────────────────────┤
│                                                 │
│  配置列表:                                      │
│  ┌───────────────────────────────────────────┐  │
│  │ 配置名称                    │ 更新日期   │ 操作│  │
│  ├───────────────────────────────────────────┤  │
│  │ ai_decision_default.yaml    │ 06-14     │ ✏️ 🗑│  │
│  │ ai_decision_aggressive.yaml │ 06-14     │ ✏️ 🗑│  │
│  │ us_ai_v1.yaml (策略实例)      │ 06-14     │ ✏️ 🗑│  │
│  └───────────────────────────────────────────┘  │
│                                                 │
└─────────────────────────────────────────────────┘
```

##### 6.3.2 新建/编辑配置

- **YAML 编辑器** (Monaco Editor 或带语法高亮的 TextArea)
- 保存时后端校验 YAML 语法 + schema
- 区分「模板配置」(`/ai_decision/configs/*.yaml`) 和「策略实例配置」(由创建策略时生成)
- 编辑模板配置不影响已有策略实例

##### 6.3.3 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/admin/ai-decision/configs` | GET | 列出所有配置文件 |
| `/api/admin/ai-decision/configs/{name}` | GET | 获取单个配置内容 |
| `/api/admin/ai-decision/configs/{name}` | PUT | 更新配置 |
| `/api/admin/ai-decision/configs/{name}` | DELETE | 删除配置 (二次确认) |

---

#### 6.4 召回层 (子页面)

**功能**: 展示本轮召回 + 候选池过滤结果。

##### 6.4.1 页面布局

```
┌───────────────────────────────────────────────────────┐
│  🤖 AI 决策 — 召回层                    2026-06-14 09:30│
├───────────────────────────────────────────────────────┤
│                                                       │
│  摘要卡片:                                             │
│  ┌──────────┬──────────┬──────────┬──────────┐        │
│  │ 启用策略  │ 原始信号  │ 候选标的  │ 过滤阈值  │        │
│  │   16      │   487    │   23     │   0.20   │        │
│  └──────────┴──────────┴──────────┴──────────┘        │
│                                                       │
│  候选池 (23 只标的, 按综合强度排序):                    │
│  ┌─────────────────────────────────────────────────┐  │
│  │ #  │ 标的   │ 综合得分 │ 命中策略               │  │
│  ├─────────────────────────────────────────────────┤  │
│  │ 1  │ AAPL  │ 0.87    │ SimpleMomentum(+0.82)    │  │
│  │    │       │         │ MLPrediction(+0.91)       │  │
│  │    │       │         │ QARP(+0.73)              │  │
│  │ 2  │ NVDA  │ 0.84    │ SimpleMomentum(+0.79)    │  │
│  │    │       │         │ MACD(+0.67)              │  │
│  │    │       │         │ BollingerBands(+0.71)    │  │
│  │ 3  │ MSFT  │ 0.71    │ MeanReversion(+0.65)     │  │
│  │    │       │         │ MLPrediction(+0.78)      │  │
│  │ ...│ ...   │ ...     │ ...                      │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  每个标的展示:                                         │
│  - 标的名称 (symbol)                                   │
│  - 综合得分 (归一化后,  0-1)                            │
│  - 被哪些策略命中 → 展开显示策略名 + 原始 score          │
│  - 策略得分方向 (buy/sell) → 绿色/红色标识              │
│                                                       │
└───────────────────────────────────────────────────────┘
```

##### 6.4.2 数据来源

- 持久化存储: SQLite 表 `ai_recall_results` (每次运行写入)
- API: `GET /api/admin/ai-decision/recall?strategy_id=xxx&date=2026-06-14`

##### 6.4.3 交互

- 默认显示最新一次运行结果
- 日期选择器可回溯历史
- 点击标的跳转到分析层查看该标的详细分析

---

#### 6.5 分析层 (子页面)

**功能**: 展示 Top-K 标的的 AI 深度分析结果。

##### 6.5.1 页面布局

```
┌───────────────────────────────────────────────────────┐
│  🤖 AI 决策 — 分析层                    2026-06-14 09:35│
├───────────────────────────────────────────────────────┤
│                                                       │
│  摘要: 已分析 10/10 标的 (Top-K=10), 耗时 8.2s         │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │ 标的   │ 方向     │ 置信度 │ 评分 │ 分析摘要       │  │
│  ├─────────────────────────────────────────────────┤  │
│  │ AAPL  │ 🟢 Bullish│ 0.82  │ A-   │ 技术面强势突破  │  │
│  │       │          │       │      │ + 基本面盈利超   │  │
│  │       │          │       │      │ 预期, RSI未超买  │  │
│  │ NVDA  │ 🟢 Bullish│ 0.78  │ B+   │ AI 需求持续旺盛  │  │
│  │       │          │       │      │ + 机构增持, 但   │  │
│  │       │          │       │      │ 估值偏高需注意   │  │
│  │ MSFT  │ ⚪ Neutral│ 0.55  │ B    │ 横盘整理中, 均   │  │
│  │       │          │       │      │ 线粘合, 方向不明 │  │
│  │ ...   │ ...      │ ...   │ ...  │ ...              │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  点击展开查看完整报告 (JSON / 格式化文本)               │
└───────────────────────────────────────────────────────┘
```

##### 6.5.2 展开详情

点击某只标的展开完整 AI 分析报告:

```
AAPL — 完整分析报告
──────────────────────────────
方向: Bullish
置信度: 0.82
评分: A- (综合技术面+基本面+情绪面)

关键论据:
1. 20日均线上穿50日均线形成金叉，技术面强势
2. Q2 财报盈利超预期, 服务收入同比增长 14%
3. 5 个策略同时给出 buy 信号，共识度高
4. RSI(14)=62，未进入超买区间，仍有上行空间
5. 机构 13F 显示最近一季度增持

风险因素:
1. 估值偏高 (P/E 32x vs 行业均值 25x)
2. 中国区销售面临地缘政治不确定性
3. 大盘短期回调风险

数据覆盖:
- 技术面: ✅ (自有 BQ)  - 基本面: ✅ (llmquant)
- 情绪面: ✅ (llmquant)  - 机构持仓: ✅ (llmquant)
- SEC文件: ✅ (llmquant)

建议权重调整: +0.15 (上调)
──────────────────────────────
```

##### 6.5.3 数据来源

- 持久化: SQLite 表 `ai_analysis_results`
- API: `GET /api/admin/ai-decision/analysis?strategy_id=xxx&date=2026-06-14`
- API: `GET /api/admin/ai-decision/analysis/{symbol}?strategy_id=xxx` (完整报告)

---

#### 6.6 决策层 (子页面)

**功能**: 展示每次 AI 策略的最终决策输出。

##### 6.6.1 页面布局

```
┌───────────────────────────────────────────────────────┐
│  🤖 AI 决策 — 决策层                    2026-06-14 09:40│
├───────────────────────────────────────────────────────┤
│                                                       │
│  ┌───────────────────────────────────────────────┐    │
│  │ 决策时间: 2026-06-14 09:40:15 CST              │    │
│  │ 策略: us_ai_v1  │  运行环境: 模拟交易           │    │
│  │ 标的总数: 6 只  │  预计换手率: 18%              │    │
│  ├───────────────────────────────────────────────┤    │
│  │                                               │    │
│  │  📤 卖出 (2 笔)                               │    │
│  │  ┌───────────────────────────────────────┐    │    │
│  │  │ GOOGL │ -50 股 │ ~$7,500 │ 信号转弱    │    │    │
│  │  │ META  │ -30 股 │ ~$14,100│ 行业超限调整 │    │    │
│  │  └───────────────────────────────────────┘    │    │
│  │                                               │    │
│  │  📥 买入 (4 笔)                               │    │
│  │  ┌───────────────────────────────────────┐    │    │
│  │  │ AAPL │ +80 股  │ ~$16,000│ Bullish/排名#1 │  │    │
│  │  │ NVDA │ +15 股  │ ~$18,000│ Bullish/排名#2 │  │    │
│  │  │ MSFT │ +20 股  │ ~$8,800 │ Neutral但低估  │  │    │
│  │  │ AMZN │ +25 股  │ ~$4,700 │ Bullish/排名#4 │  │    │
│  │  └───────────────────────────────────────┘    │    │
│  │                                               │    │
│  │  📊 摘要                                      │    │
│  │  卖出总额: ~$21,600                            │    │
│  │  买入总额: ~$47,500                            │    │
│  │  净资金变动: -$25,900                          │    │
│  │  剩余现金: $32,400                             │    │
│  │  换手率: 18% / 30% (上限)                      │    │
│  │  行业分布: 科技 35% / 金融 12% / 医疗 8%       │    │
│  └───────────────────────────────────────────────┘    │
│                                                       │
│  历史决策记录:                                         │
│  ┌─────────────────────────────────────────────────┐  │
│  │ 日期       │ 卖出 │ 买入 │ 换手率 │ 净变动     │  │
│  ├─────────────────────────────────────────────────┤  │
│  │ 06-14 09:40│ 2笔  │ 4笔  │ 18%   │ -$25,900   │  │
│  │ 06-13 09:40│ 1笔  │ 3笔  │ 12%   │ +$8,300    │  │
│  │ 06-12 09:40│ 0笔  │ 0笔  │ 0%    │ $0 (未调仓) │  │
│  └─────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘
```

##### 6.6.2 决策详情 (展开单条记录)

点击历史记录中的某一天 → 展示完整决策明细:
- 标的层: 每只标的的 LLM 判断 (action/reason/urgency)
- 组合层: 买卖配对逻辑、冲突消解过程、约束检查结果
- 执行状态: 是否已发送到 OMS、订单 ID、成交状态

##### 6.6.3 数据来源

- 持久化: SQLite 表 `ai_decision_results`
- API: `GET /api/admin/ai-decision/decisions?strategy_id=xxx` (历史列表)
- API: `GET /api/admin/ai-decision/decisions/{id}` (单条详情)

---

#### 6.7 数据模型 (SQLite 新增表)

```sql
-- 召回层结果
CREATE TABLE ai_recall_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    run_time TIMESTAMP NOT NULL,
    symbol TEXT NOT NULL,
    aggregate_score REAL,
    hitting_strategies TEXT,        -- JSON: [{"strategy":"...","score":0.82,"direction":"buy"}]
    UNIQUE(strategy_id, run_time, symbol)
);

-- 分析层结果
CREATE TABLE ai_analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    run_time TIMESTAMP NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT,                 -- bullish/neutral/bearish
    confidence REAL,
    rating TEXT,                    -- A-/B+/B etc.
    summary TEXT,                   -- 一句话摘要
    full_report TEXT,               -- 完整 JSON 报告
    data_coverage TEXT,             -- JSON: {"technical":true,"fundamental":true,...}
    UNIQUE(strategy_id, run_time, symbol)
);

-- 决策层结果
CREATE TABLE ai_decision_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    run_time TIMESTAMP NOT NULL,
    environment TEXT NOT NULL,       -- experiment/sim/real
    sell_orders TEXT,               -- JSON: [{"symbol":"...","qty":...,"reason":"..."}]
    buy_orders TEXT,                -- JSON
    summary TEXT,                   -- JSON: {net_change, remaining_cash, turnover, sectors}
    execution_status TEXT,          -- pending/sent/filled/partial/error
    UNIQUE(strategy_id, run_time)
);
```

---

#### 6.8 日志 & 告警

- **运行日志**: `/var/log/quant/ai_decision_{strategy_id}_{date}.log`
- **Admin 日志中心**: 复用现有日志浏览, 可按 `ai_decision` 过滤
- **告警规则**:
  - LLM 调用连续失败 3 次 → Admin 告警
  - 数据源全部缺失 (BQ + llmquant 都无数据) → Admin 告警
  - 决策执行异常 (OMS 下单失败) → Admin 告警
  - 融合层候选池为空 (无信号) → 非告警, 记录日志

#### 6.9 实现要点

| 要点 | 说明 |
|------|------|
| 侧边栏导航 | 参照现有「模型 & 策略」模块模式, 5 个子页 |
| 策略创建 | 参照交易策略「从模板创建」模式 (下拉模板 + 覆盖字段) |
| 看板集成 | AI 策略出现在对应看板的下拉列表中; 无需新建看板代码 |
| 策略状态 | 通过 cron 调度; 启动=启用 cron, 停止=禁用 cron |
| 立即运行 | 调用 `engine.py` 同步跑一次, 或排入 task 队列异步执行 |
| 市场限制 | 前端硬限制 US; 后端 schema 加 market 字段为未来扩展预留 |

---

## 6. 配置完整示例

```yaml
# ai_decision/default_config.yaml

ai_decision:
  pipeline:
    # ①-② 召回 + 候选池
    recall:
      enabled_strategies:
        - SimpleMomentum
        - MeanReversion
        - MLPrediction
        - MACD
        - RSI2
        - BollingerBands
        - MultiFactorRank
        - TurtleTrading
        - QARP
        - MACrossover
        - ATRTrailingStop
        - ShortSqueeze
        - SectorRotation
        - PairsTrading
        - BuyHold
        - FundingRateArbitrage

    candidate_pool:
      min_signal_threshold: 0.20
      aggregation: "max_abs"

    # ③ 分析层
    analysis:
      top_k: 10
      llm:
        model: "deepseek/deepseek-v4-pro"
        temperature: 0.3
        max_tokens: 2000
        concurrent: 5
      data:
        sources: ["bigquery", "llmquant"]
        timeout_seconds: 30

    # ④ 融合层
    fusion:
      mode: "weighted"
      weights:
        momentum: 1.0
        mean_rev: 0.8
        ml_predict: 1.2
        macd: 0.7
        rsi: 0.7
        bollinger: 0.6
        multi_factor: 1.0
        turtle: 0.8
        qarp: 0.9
        ma_crossover: 0.6
        atr_stop: 0.5
        short_squeeze: 0.5
        sector_rotation: 0.8
        pairs_trading: 0.7
        buy_hold: 0.3
        funding_rate: 0.6
        ai_analysis: 1.5

    # ⑤ 执行层
    execution:
      stock_eval:
        llm:
          model: "deepseek/deepseek-v4-pro"
          temperature: 0.2
        batch_size: 5
      constraints:
        max_position_pct: 0.15
        max_sector_pct: 0.40
        min_cash_reserve: 0.10
        max_turnover: 0.30
        min_trade_value: 500

  # 调度 (各组独立频率)
  schedule:
    recall_candidate:
      cron: "30 9 * * 1-5"
      timezone: "Asia/Shanghai"
    analysis_fusion:
      cron: "35 9 * * 1-5"
      timezone: "Asia/Shanghai"
    execution:
      cron: "40 9 * * 1-5"
      timezone: "Asia/Shanghai"
```

---

## 7. 风险 & 待讨论

### 7.1 已识别风险

| 风险 | 缓解 |
|------|------|
| LLM 延迟（分析层并发 10 只 + 标的层并发 N 只） | 并行调用；超时 fallback；可降级为纯规则模式 |
| LLM 幻觉/输出格式不稳定 | Pydantic 校验 + JSON retry；结构化输出约束 |
| BQ 数据缺失（某标的日线有缺口） | llmquant 回退；标注 "partial_data" |
| 策略信号空窗期（某天大部分策略没出信号） | 候选池自然为空 → 当日跳过不调仓 |
| AI 决策与现有 Runner 冲突 | Runner 加开关: `use_ai_decision: true/false` |

### 7.2 已确认 / 待确认

| # | 问题 | 状态 |
|---|------|------|
| 1 | Top-K = 10 | ✅ |
| 2 | LLM 模型 deepseek-v4-pro | ✅ |
| 3 | 各层频率独立可配, 默认每天开盘后各一次 | ✅ |
| 4 | US 市场 only (初始) | ✅ |
| 5 | Admin 侧边栏独立模块「🤖 AI 决策 (仅 US)」 | ✅ |
| 6 | 策略概览 — 模板创建 + 环境选择 + 启动/停止 | ✅ |
| 7 | 看板复用 — 根据运行环境自动路由 | ✅ |
| 8 | 配置管理 — CRUD | ✅ |
| 9 | 各层输出 — 召回/分析/决策子页面 | ✅ |
| 10 | 行业分类数据来源？ | 待确认 |
| 11 | 组合层是否支持"不调仓"输出？ | 待确认 |
| 12 | Phase 1 数据层开始？ | 待确认 |

---

## 8. 附录: 与现有系统对比

| 维度 | 现有系统 | AI 决策引擎 |
|------|---------|-----------|
| **信号来源** | 单策略独立产出 | 多策略聚合 + AI 二次判断 |
| **分析深度** | 纯量化公式 | 量化 + LLM 综合（技术面+基本面+情绪面） |
| **决策方式** | 策略内规则 (RSI>70→sell) | AI 判断时机 + 组合调配 |
| **组合视角** | 无 (各策略各管各) | 全局约束 (仓位/行业/现金) |
| **适应性** | 固定参数 | 可配置模式 (投票/加权, 频率独立) |
| **可解释性** | 规则透明但单一 | AI 产出结构化理由 + 风险提示 |
