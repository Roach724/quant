# Factor Registry — 因子注册制设计

> 日期: 2026-05-30 · 状态: Draft · 作者: Jarvis + 老大  
> **前置于**: Paper Runner Week 2 (ML 双策略)

---

## 1. 目标

将因子从"代码资产"升级为"可管理的数据资产"。

```
当前: FactorBuilder.get_all_factors() → 43+ 个名字 → 读代码才知道是什么
目标: FactorRegistry.get_active("us") → 挑出 IC>0.05 的因子 → 直接用
```

**核心价值**：
- **解耦** — 研究员加因子不碰策略代码，策略员不读因子源码
- **透明** — 谁写的、怎么算的、IC 多少、何时失效，一目了然
- **可持续** — 因子库积累，换市场/换策略直接挑

---

## 2. 架构

```
因子生产                     因子库                        因子消费
─────────                   ──────                       ─────────
FactorBuilder               BQ 双表                      策略 / ML
    │                    ┌──────────────┐                    │
    ├─ 注册 ────────────→│ registry     │←── 查询有效因子 ───┤
    │                    │ (元数据)      │                    │
    ├─ 评估 ────→ factor_evaluations ───┤                    │
    │               │  (评估快照)  │     │                    │
    │               └── 准入门槛 ──┘     │                    │
    │                 IC>0.05, t-stat>3  │                    │
    │                 cov>90%, corr<0.7  │                    │
```

---

## 3. Schema

### 3.1 factor_registry（元数据表）

```sql
CREATE TABLE quant.factor_registry (
    factor_id      STRING    NOT NULL,  -- 唯一标识, e.g. "us_momentum_20d"
    name           STRING    NOT NULL,  -- 人类可读, e.g. "20日动量"
    market         STRING    NOT NULL,  -- us / hk / crypto
    category       STRING,              -- momentum / volatility / volume / price_pattern / higher_moment / hk_specific
    source         STRING,              -- 来源, e.g. "Alpha158", "自研", "论文:Jegadeesh&Titman1993"
    formula        STRING,              -- 计算公式 or 代码引用, e.g. "factors/momentum.py::momentum_20d"
    description    STRING,              -- 文字描述
    is_active      BOOL      DEFAULT TRUE,  -- 当前是否有效
    admitted_at    TIMESTAMP,           -- 入库日期
    last_evaluated TIMESTAMP,           -- 最近一次评估时间
    created_by     STRING,              -- 创建者
    
    -- 最新评估快照（冗余，方便快速查询）
    latest_ic_mean     FLOAT64,
    latest_ic_tstat    FLOAT64,
    latest_coverage    FLOAT64,
    latest_eval_id     STRING,          -- 对应 factor_evaluations.eval_id
    
    tags           ARRAY<STRING>,       -- ["trend", "short_term"]
    metadata       JSON                 -- 扩展字段
)
PARTITION BY DATE(admitted_at)
CLUSTER BY market, is_active;
```

### 3.2 factor_evaluations（评估历史表）

```sql
CREATE TABLE quant.factor_evaluations (
    eval_id        STRING    NOT NULL,  -- e.g. "us_momentum_20d_2026Q1"
    factor_id      STRING    NOT NULL,  -- 关联 registry
    evaluated_at   TIMESTAMP NOT NULL,  -- 评估时间
    
    -- IC 指标
    ic_mean        FLOAT64,             -- Rank IC 均值
    ic_std         FLOAT64,             -- IC 标准差
    ic_tstat       FLOAT64,             -- IC t-statistic
    ic_ir          FLOAT64,             -- IC Information Ratio (IC_mean / IC_std)
    
    -- IC 衰减
    ic_decay_1d    FLOAT64,
    ic_decay_5d    FLOAT64,
    ic_decay_20d   FLOAT64,
    
    -- 覆盖 & 分布
    coverage       FLOAT64,             -- 有效值覆盖率
    skewness       FLOAT64,
    kurtosis       FLOAT64,
    
    -- 相关性
    top_correlated   ARRAY<STRING>,     -- 最相关的其他因子 ID
    max_correlation  FLOAT64,           -- 最大相关系数
    
    -- 准入判定
    passes_admission  BOOL,             -- 是否通过所有准入标准
    admission_details STRING,           -- 哪些标准没通过（JSON）
    
    -- 评估参数
    eval_period_start  DATE,
    eval_period_end    DATE,
    eval_market        STRING,
    data_version       STRING,          -- 数据版本（用于复现）
    
    metadata  JSON
)
PARTITION BY DATE(evaluated_at)
CLUSTER BY factor_id;
```

---

## 4. 准入标准（硬门槛）

| 标准 | 阈值 | 不通过 => |
|------|------|-----------|
| Rank IC (abs) | > 0.05 | is_active = false, admission_details |= "ic_low" |
| IC t-statistic | > 3.0 | is_active = false, admission_details |= "ic_insignificant" |
| 覆盖率 | > 90% | is_active = false, admission_details |= "coverage_low" |
| IC 衰减 (20d) | 不反转 | 反转 => admission_details |= "ic_decay_reversal" |
| 最大相关性 | < 0.7 | 提示但不否决 |

**设计决定**: 相关性不否决（只 warn），因为你可能需要冗余因子做稳健性。IC 不达标才直接禁止。

---

## 5. FactorRegistry API

```python
class FactorRegistry:
    """因子注册库 — 读写 BQ 双表"""
    
    # ── 注册 ──
    def register(
        factor_id: str,
        name: str,
        market: str,
        source: str = None,
        formula: str = None,
        category: str = None,
        description: str = None,
        tags: list[str] = None,
    ) -> bool:
        """注册新因子元数据。不自动评估。"""
    
    # ── 评估 ──
    def evaluate(
        factor_id: str,
        start_date: str = "2020-01-01",
        end_date: str = None,
        force: bool = False,
    ) -> FactorEvaluation:
        """运行因子评估（IC/覆盖率/衰减/相关性），写入 factor_evaluations。
        如果通过准入则更新 registry.is_active=true。
        如果 force=false 且上次评估 < 30 天，跳过。
        """
    
    # ── 批量评估 ──
    def evaluate_all(
        market: str = "us",
        start_date: str = None,
        end_date: str = None,
    ) -> list[FactorEvaluation]:
        """评估该市场所有未评估因子"""
    
    # ── 查询 ──
    def get_active(market: str = "us") -> pd.DataFrame:
        """SELECT * FROM factor_registry 
           WHERE market = '{market}' AND is_active = TRUE
           ORDER BY latest_ic_mean DESC"""
    
    def get_history(factor_id: str) -> pd.DataFrame:
        """查询某个因子的完整评估历史"""
    
    def get_evaluation(factor_id: str, eval_id: str = None) -> FactorEvaluation:
        """最新评估 or 指定评估"""
    
    # ── 管理 ──
    def deactivate(factor_id: str, reason: str):
        """手动下架因子（is_active=false）"""
    
    def compare(factor_ids: list[str]) -> pd.DataFrame:
        """多因子横向对比"""
```

---

## 6. 数据流

### 6.1 新因子入库流程

```
1. 研究员写因子计算函数
2. FactorBuilder 注册新名字
3. FactorRegistry.register(factor_id, name, market, source, formula)
4. FactorRegistry.evaluate(factor_id, start="2020-01-01")
5. 结果写入 factor_evaluations
6. 如果 passes_admission → registry.is_active = TRUE
7. 策略可以直接 FactorRegistry.get_active("us") 选中新因子
```

### 6.2 定期复审流程（可选）

```
cron quarterly:
  FactorRegistry.evaluate_all(market="us")
  → 收集所有 active 因子的最新评估
  → 失效因子自动 deactivate
  → 日志记录变化
```

---

## 7. 与现有组件集成

### 7.1 FactorBuilder 集成

```python
# 旧: 策略直接调 FactorBuilder
factors = FactorBuilder.get_all_factors()  # 无过滤

# 新: 策略从注册库挑
active_factors = FactorRegistry.get_active("us")
factor_names = active_factors["factor_id"].tolist()
factors = FactorBuilder.compute(factor_names, data)
```

FactorBuilder 不需要大改——只新增 `compute(names, data)` 方法，按名字算指定因子。

### 7.2 ModelTrainer 集成

```python
# 旧: get_all_factors() → 硬编码 43+
# 新: FactorRegistry.get_active("us") → 挑出 IC>0.05 的因子
# → 更少的因子 → 更快的训练 → 更不容易过拟合
```

### 7.3 ExperimentTracker 集成

每次 run 记录使用了哪几个 `factor_ids`，确保实验可复现。

### 7.4 PaperRunner 集成 (Week 2)

```
W2 MLPredStrategy:
  factors = FactorRegistry.get_active("us").head(15)  # Top 15
  model = ModelTrainer.train(factors, ...)
```

---

## 8. 新增文件清单

| 文件 | 用途 |
|------|------|
| `factors/registry.py` | FactorRegistry 类 |
| `factors/evaluation.py` | 因子评估逻辑（IC/衰减/相关性） |
| `scripts/init_factor_registry.py` | 初始化 BQ 表 + 注册现有 43+ 因子 |
| `sql/factor_registry_schema.sql` | 建表 DDL |
| `docs/factors/registry-guide.md` | 使用文档 |

---

## 9. 实施步骤

| # | 步骤 | 预计工时 |
|---|------|----------|
| 1 | 创建 BQ 双表 | 15 min |
| 2 | 写 FactorRegistry.register() / get_active() | 30 min |
| 3 | 写因子评估逻辑 (IC/衰减/相关性) | 1h |
| 4 | 运行 init 脚本：注册现有 43+ 因子 + 评估 | 手动触发 |
| 5 | FactorBuilder 加 compute(factor_names) | 15 min |
| 6 | ModelTrainer 改为从 registry 取因子 | 15 min |
| 7 | 验证：get_active("us") 返回因子全部 IC>0.05 | 自动 |

**估算总工时**: 2-3h

---

## 10. 风险

| 风险 | 缓解 |
|------|------|
| 43+ 因子首次评估数据量大 | 批处理按 market 切片跑 |
| 准入标准太严导致 active 因子太少 | 阈值可配，保留调参入口 |
| 因子值 BQ 计算开销大 | 只在 evaluate() 时算，不实时 |
| registry 与 FactorBuilder 代码不同步 | register() 要求 formula 字段指向源码路径 |
