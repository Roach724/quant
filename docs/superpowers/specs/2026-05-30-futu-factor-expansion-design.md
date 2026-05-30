# Futu API 因子扩展一期 — 设计文档

日期：2026-05-30 | 状态：设计中 | 参考调研：[futu-api-1-api-2-api-ethereal-falcon](../research/futu-api-1-api-2-api-ethereal-falcon.md)

## 目标

一期交付 F10 数据采集管道 + 因子体系从 39 扩展到 80（OHLCV 39 + F10 41）+ QARP 策略回测验证。架构预留二期（ML 升级/更多策略）和三期（期权/事件驱动）扩展点。

## 架构总览

```
┌─ 采集层 ─────────────────────────────────────────────────────┐
│  6 个独立 Adapter，每类 F10 数据一个                            │
│  _futu_base.py（共享基类：OpenD 连接、限流、symbol pool）       │
│                                                               │
│  FutuFinancialsAdapter       → GCS → BQ: quant.{mkt}_financials│
│  FutuValuationAdapter        → GCS → BQ: quant.{mkt}_valuation│
│  FutuShortInterestAdapter    → GCS → BQ: quant.{mkt}_short_int│
│  FutuCapitalFlowAdapter      → GCS → BQ: quant.{mkt}_cap_flow │
│  FutuAnalystAdapter          → GCS → BQ: quant.{mkt}_analyst  │
│  FutuShareholderAdapter      → GCS → BQ: quant.{mkt}_shrhldr  │
│                                                               │
│  各自独立 cron，独立 BQ 表，互不依赖                             │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ 因子层 ─────────────────────────────────────────────────────┐
│  TechFactorBuilder (OHLCV 39)    → BQ: factor_values         │
│  FundamentalFactorBuilder (F10 41)→ BQ: factor_values        │
│                                                               │
│  批处理 cron，从 BQ 原始表读取 → 计算 → 写入 factor_values     │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ 注册/评估层 ────────────────────────────────────────────────┐
│  FactorRegistry 统一注册 80 因子（已有，扩展 category 枚举）    │
│  FactorEvaluation IC/decal/coverage/admission（已有，适配频率）│
│  ML Trainer 加载 80 特征 → predict → ctx.predictions          │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ 策略/回测层 ────────────────────────────────────────────────┐
│  UniverseBuilder 动态选股（screen / plate / bq_rank）          │
│  QARP 策略：高 ROE + 低 PE 分位 + 盈利增长 → top K 月度调仓    │
│  PaperRunner BQ 数据源 → 回测验证                              │
└──────────────────────────────────────────────────────────────┘
```

## 设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 范围 | 渐进式：一期因子+采集，预留扩展点 | 避免一次性改动过大 |
| 数据维度 | 广度优先，每维度 3-5 核心因子，总量 ~41 | 快速覆盖多类 alpha 来源 |
| 存储管道 | GCS Parquet → BQ LOAD | 与现有 K 线管道一致，保留数据血缘 |
| 因子汇聚 | BQ `factor_values` 表 | 两个 builder 独立输出，统一注册 |
| 命名调整 | FactorBuilder → TechFactorBuilder + FundamentalFactorBuilder | 职责更明确 |
| 因子计算时机 | 独立批处理 cron | 与采集解耦 |
| Adapter 粒度 | 一个 adapter 管一类数据 | 独立部署、独立频率、独立维护 |

## 文件变更清单

### 新建文件

| 文件 | 说明 |
|------|------|
| `collectors/adapters/_futu_base.py` | 共享基类：OpenD 连接管理、API 限流（60 req/30s）、symbol pool 加载、GCS 写入、BQ LOAD 模板方法 |
| `collectors/adapters/futu_financials_adapter.py` | 财报数据采集（利润表/资产负债表/现金流/关键指标），cron 每周 |
| `collectors/adapters/futu_valuation_adapter.py` | 估值数据采集（PE/PB/PS 当前值、历史分位），cron 每日 |
| `collectors/adapters/futu_short_interest_adapter.py` | 空头数据采集（空头持仓、卖空占比），cron 每日 |
| `collectors/adapters/futu_capital_flow_adapter.py` | 资金流数据采集（主力/大户/中户/散户流向），cron 每日 |
| `collectors/adapters/futu_analyst_adapter.py` | 分析师评级采集（综合评级、目标价、评级分布），cron 每周 |
| `collectors/adapters/futu_shareholder_adapter.py` | 股东/机构数据采集（持股变动、机构持股），cron 每周 |
| `collectors/fundamental_collector.py` | F10 采集入口脚本，接受 `--source` 参数选择 adapter |
| `factors/fundamental_builder.py` | FundamentalFactorBuilder，41 个 F10 因子 |
| `sql/f10_schemas.sql` | 6 张 F10 数据 BQ 表 schema |
| `sql/factor_values_schema.sql` | 因子值汇聚表 schema（factor_id, symbol, date, value, source_builder） |

### 重命名

| 旧路径 | 新路径 | 说明 |
|--------|--------|------|
| `factors/builder.py` | `factors/tech_builder.py` | `FactorBuilder` → `TechFactorBuilder` |

原 `FactorBuilder` 类名保留为别名，打印 deprecation warning。后续版本移除。

### 修改文件

| 文件 | 改动 |
|------|------|
| `factors/registry.py` | `category` 字段新增 9 个枚举值：quality / growth / earnings_quality / valuation / short_sentiment / capital_flow / analyst / smart_money / earnings_event |
| `factors/evaluation.py` | `evaluate()` 新增 `min_periods` 参数适配低频因子 |
| `scripts/init_factor_registry.py` | 同时注册 TechFactorBuilder + FundamentalFactorBuilder 的因子 |
| `ml/trainer.py` | BQ 数据加载替代 parquet；新增 `--factor-source`（tech / fundamental / all）参数 |
| `paper/market.py` | 新增 `UniverseBuilder` 类：from_screen / from_plate / from_bq / from_static |
| `paper/strategies.py` | 新增 `QARP` 策略类 |
| `run_paper.py` | 新增 `--universe` 参数，支持 screen:xxx / plate:xxx / bq:xxx / static |
| `engine/__init__.py` 及相关引用 | 更新 `FactorBuilder` → `TechFactorBuilder` 的 import |

## 首批因子明细

### TechFactorBuilder（OHLCV，39 个，保持不变）

returns(6) + volatility(4) + volume(4) + momentum(8) + turnover(4) + patterns(5) + skew_kurt(6) + hk(2)

### FundamentalFactorBuilder（F10，41 个）

**质量因子（7）**：`roe`, `roa`, `gross_margin`, `net_margin`, `debt_to_equity`, `current_ratio`, `interest_coverage`

**成长因子（4）**：`revenue_growth_yoy`, `eps_growth_yoy`, `net_profit_growth_yoy`, `asset_growth_yoy`

**盈利质量因子（3）**：`accruals_ratio`, `ocf_to_net_profit`, `revenue_to_cash_ratio`

**估值因子（5）**：`pe_percentile`, `pb_percentile`, `ps_percentile`, `pe_vs_5y_avg`, `peg_ratio`

**空头情绪因子（5）**：`short_ratio`, `days_to_cover`, `short_change_1m`, `short_volume_pct`, `short_utilization`

**资金流因子（4）**：`main_inflow_ratio`, `big_order_pct`, `retail_flow_divergence`, `flow_price_divergence`

**分析师因子（5）**：`target_price_upside`, `buy_ratio`, `rating_mean`, `rating_change_1m`, `analyst_count`

**Smart Money 因子（5）**：`inst_ownership_change`, `inst_accumulation_signal`, `hedge_fund_add_ratio`, `insider_buy_ratio`, `holder_concentration`

**财报事件因子（3）**：`earnings_price_move_avg`, `post_earnings_drift_5d`, `earnings_volatility`

## 关键接口

### _futu_base.py（共享基类）

```python
class FutuBaseAdapter:
    """F10 adapter 基类 — 封装 OpenD 连接、限流、GCS 写入、BQ LOAD。"""

    def __init__(self, host=None, port=None, symbols=None):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self.symbols = symbols or self._default_symbols()

    def _get_ctx(self) -> OpenQuoteContext: ...
    def _rate_limit(self): ...
    def fetch(self, symbol: str) -> pd.DataFrame:  # 子类实现
        raise NotImplementedError
    def fetch_all(self) -> dict[str, pd.DataFrame]: ...
    def write_to_gcs(self, data: dict): ...
    def load_to_bq(self): ...
```

### FundamentalFactorBuilder

```python
class FundamentalFactorBuilder:
    ALL_FACTOR_COLS: list[str]  # 41 因子列名
    LABEL_COLS = ["fwd_ret_5d", "fwd_ret_20d"]

    def compute(self, factor_names: list[str], data_map: dict[str, pd.DataFrame]) -> pd.DataFrame: ...
    def process_factors(self, df: pd.DataFrame) -> pd.DataFrame: ...  # winsorize + z-score
    def build_dataset(self, symbols, start, end, loader) -> pd.DataFrame: ...
    def compute_ic(self, df: pd.DataFrame, label="fwd_ret_5d") -> pd.Series: ...
```

### UniverseBuilder

```python
class UniverseBuilder:
    @staticmethod
    def from_screen(config_path: str) -> list[str]: ...
    @staticmethod
    def from_plate(plate_code: str) -> list[str]: ...
    @staticmethod
    def from_bq(market: str, date: str, min_market_cap=1e10, top_k=100) -> list[str]: ...
    @staticmethod
    def from_static(market: str) -> list[str]: ...
```

## 批处理调度

```
tech_factors_cron:   每日 02:00 UTC
  → TechFactorBuilder 从 BQ OHLCV 表读取
  → 计算 39 因子
  → 写入 factor_values

fund_factors_cron:   每日 03:00 UTC
  → FundamentalFactorBuilder 从 BQ F10 表读取
  → ffill 填充低频数据到日频
  → 计算 41 因子
  → 写入 factor_values

各 F10 adapter cron（错峰运行）：
  fin_valuation:      每日 00:30 UTC
  fin_short_interest: 每日 00:45 UTC
  fin_capital_flow:   每日 01:00 UTC
  fin_financials:     每周一 00:00 UTC（财报季可手动调为每日）
  fin_analyst:        每周一 00:15 UTC
  fin_shareholder:    每周一 00:30 UTC
```

## 为后续阶段预留的扩展点

| 扩展点 | 预留方式 |
|--------|---------|
| 新 F10 数据源（内部人交易、期权、经纪商等） | `_futu_base.py` 基类 + `collectors/adapters/` 目录下新增文件 |
| 新增因子 | `FundamentalFactorBuilder.ALL_FACTOR_COLS` 追加 + `init_factor_registry.py` 自动注册 |
| 新策略类型（Short Squeeze、Sector Rotation 等） | `paper/strategies.py` 新增 Strategy 子类 |
| 实时推送 | 参考现有 `collectors/ws_collector.py` 模式 |
| 期权生态 | 独立 `futu_option_adapter.py` + `factors/option_builder.py` |

## 验证

1. **数据完整性**：逐日跑的 adapter（估值/空头/资金流）在 2 周内无断点；逐周跑的 adapter（财报/分析师/股东）在一个月内无断点
2. **因子 IC**：新增 41 个 F10 因子中，至少 15 个通过 admission 标准（|IC| > 0.05，|t-stat| > 3.0，coverage > 90%）
3. **ML 增量验证**：`--factor-source all` vs `--factor-source tech` 的 OOS IC 有正向提升
4. **QARP 回测**：在 US 市场过去 2 年的 OOS walk-forward 中，Sharpe > BuyHold baseline
5. **向后兼容**：现有 104 个测试通过，`FactorBuilder` 别名仍可工作（含 deprecation warning）
