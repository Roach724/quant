# Futu API 调研报告：项目优化与未来扩展

## Context

当前项目 `D:\quant` 是一个多市场量化交易系统，覆盖数据采集 → 因子工程 → ML 训练 → 回测 → 执行 → 监控全链路。已通过 Futu API 完成 **OHLCV K 线采集** 和 **交易执行（股票+加密货币）**。但 Futu API 提供 66 个接口（35 行情 + 15 交易 + 7 推送 + 9 基础），当前项目仅用了其中约 **3-4 个**（`request_history_kline`、`OpenSecTradeContext`、`OpenCryptoTradeContext`、K 线推送订阅）。

本报告将 66 个 API 与项目各模块逐一对照，识别优化机会和未来扩展方向。

---

## 第一部分：可优化现有模块的 API

### 1. FactorBuilder（`factors/builder.py`）— 因子从 39 个扩展到 100+

**现状**：39 个因子全部来自 OHLCV 衍生（收益率、波动率、成交量、动量/技术指标、换手率、价格形态、高阶矩）。没有基本面、资金流、情绪、估值类因子。

**优化 API**：

| API | 提供的数据 | 可新增的因子类别 |
|-----|-----------|-----------------|
| `get_financials_statements.py` | 利润表/资产负债表/现金流量表/关键指标（ROE、EPS、营收、净利润、毛利率等） | **基本面因子**（15-20 个）：roe, roa, gross_margin, net_margin, revenue_growth, earnings_growth, debt_to_equity, current_ratio, fcf_yield, eps_surprise 等 |
| `get_capital_flow.py` + `get_capital_distribution.py` | 资金流向（主力/大户/中户/散户）、资金分布（超大单/大单/中单/小单） | **资金流因子**（8-10 个）：main_inflow_ratio, big_order_ratio, flow_divergence（价格 vs 资金背离）等 |
| `get_valuation_detail.py` | PE/PB/PS 当前值、历史分位数、均值、标准差、相对板块/市场排名 | **估值因子**（6-8 个）：pe_percentile, pb_percentile, pe_vs_sector, valuation_zscore 等 |
| `get_short_interest.py` + `get_daily_short_volume.py` | 空头持仓量、空头比例、回补天数、每日卖空量/占比 | **空头情绪因子**（5-6 个）：short_ratio, days_to_cover, short_change_pct, short_volume_pct 等 |
| `get_research_analyst_consensus.py` | 综合评级、目标价（最高/平均/最低）、评级分布（强力买入/买入/持有/跑输/卖出占比）、分析师覆盖数 | **分析师情绪因子**（5-6 个）：target_price_upside, buy_ratio, rating_momentum, analyst_count 等 |
| `get_shareholders_*.py` | 持股变动（增持/减持/新进/清仓）、机构持股历史、持股明细（可按类型筛选） | **Smart Money 因子**（6-8 个）：inst_ownership_change, inst_accumulation, holder_concentration 等 |
| `get_insider_trade_list.py`（仅美股） | 内部人买卖交易记录 | **内部人交易因子**（3-4 个）：insider_buy_ratio, insider_cluster_buy, insider_sell_signal 等 |
| `get_top_ten_buy_sell_brokers.py`（仅港股） | 十大净买入/净卖出经纪商 | **经纪商流向因子**（3-4 个）：broker_net_flow, top_broker_concentration 等 |
| `get_financials_earnings_price_move.py` | 历史财报日涨跌幅与波动率 | **财报效应因子**（4-5 个）：earnings_move_avg, earnings_volatility, post_earnings_drift 等 |
| `get_company_operational_efficiency.py` | 员工数、人均营收、人均利润、同比变化 | **经营效率因子**（4-5 个）：employee_growth, revenue_per_employee, profit_per_employee 等 |
| `get_corporate_actions_dividends.py` + `get_corporate_actions_buybacks.py` | 分红历史、回购历史 | **公司行动因子**（3-4 个）：dividend_yield, buyback_yield, dividend_growth 等 |

**实现方式**：新增 `factors/fundamental_builder.py`，遵循现有 `FactorBuilder` 模式（compute → process → build_dataset），通过 `data_loader` 回调获取 F10 数据。因子注册到 `quant.factor_registry` BQ 表。

### 2. 数据采集管线（`collectors/`）— 从仅 K 线到多维数据

**现状**：采集管线只拉取 OHLCV K 线数据。F10 基本面、资金流等数据完全未进入数据仓库。

**优化 API**：

| API | 用途 | 采集频率建议 |
|-----|------|-------------|
| `get_financials_statements.py` | 财报数据入 BQ | 每季度财报发布后（事件驱动）或每周检查更新 |
| `get_capital_flow.py` | 每日资金流向入 BQ | 每日收盘后 |
| `get_short_interest.py` | 空头持仓入 BQ | 每日/每周（取决于交易所发布频率） |
| `get_research_analyst_consensus.py` | 分析师评级入 BQ | 每周 |
| `get_valuation_detail.py` | 估值快照入 BQ | 每日 |

**实现方式**：新增 `collectors/adapters/futu_fundamental_adapter.py`，遵循现有 `FutuStockAdapter` 模式，由 cron 定时触发。数据写入 GCS → BigQuery LOAD，与现有 K 线管道一致。

### 3. 策略引擎（`engine/strategy.py`）— 策略从技术面扩展到基本面/情绪面

**现状**：4 个内置策略（BuyHold、SimpleMomentum、MeanReversion、FundingRateArbitrage）全部基于价格/收益率。`ctx.predictions` 已支持注入 ML 预测分数，但数据源仅限于 OHLCV 因子。

**优化 API**：

| 策略类型 | 依赖的 API | 策略思路 |
|---------|-----------|---------|
| **价值+质量** | `get_financials_statements.py` + `get_valuation_detail.py` | 买入高 ROE + 低 PE 分位 + 盈利增长 |
| **Smart Money 跟踪** | `get_shareholders_holding_changes.py` + `get_insider_trade_list.py` | 跟踪机构增持 + 内部人买入信号 |
| **空头挤压** | `get_short_interest.py` | 高空头持仓 + 正向催化剂（财报超预期）触发买入 |
| **财报事件驱动** | `get_financials_earnings_price_move.py` + `get_financials_earnings_price_history.py` | 财报前 IV 冲高做空波动率 / 财报后漂移动量 |
| **板块轮动** | `get_valuation_plate_stock_list.py` + `get_plate_stock.py` | 买入估值最便宜的板块，卖出最贵的板块 |
| **资金流跟踪** | `get_capital_flow.py` + `get_top_ten_buy_sell_brokers.py` | 价格停滞但主力资金持续流入 → 潜在突破 |

**实现方式**：策略通过 `ctx.predictions` 接收 ML 模型输出的综合得分（综合技术面+基本面+情绪面因子），策略本身只需做 portfolio construction（选股 + 权重分配）。

### 4. 风险管理（`engine/risk/` + `oms/`）— 增加非价格维度的风控

**现状**：风控仅基于价格（最大回撤、止损、暴露度、杠杆）。没有估值、情绪、流动性维度的风控。

**优化 API**：

| 风控维度 | API | 风控规则示例 |
|---------|-----|-------------|
| 估值风控 | `get_valuation_detail.py` | PE 分位 > 90% 时拒绝买入 / 减半仓位 |
| 拥挤交易风控 | `get_short_interest.py` | 空头持仓占比 > 40% 时提示挤压风险 |
| 流动性风控 | `get_snapshot.py`（换手率） | 日均换手率 < 0.1% 时限制仓位 |

**实现方式**：新增 `engine/risk/valuation.py`（估值风控规则）、`oms/risk_monitor.py` 已有架构支持，新增检查项即可。

### 5. 选股/Universe 管理（`paper/market.py` + `paper/strategies.py`）— 从静态池到动态筛选

**现状**：`paper/market.py` 中 `DEFAULT_SYMBOLS` 是硬编码的静态股票池（US 8 只、HK 8 只）。

**优化 API**：

| API | 用途 |
|-----|------|
| `get_stock_screen.py`（V2，244+ 因子） | 动态选股：按 PE、市值、动量、波动率、机构持仓等多维度筛选 |
| `get_stock_filter.py`（V1） | 简单条件选股：PE 10-30、市值 > 100 亿、涨跌幅等 |
| `get_plate_stock.py` | 按板块/主题/指数获取成分股：如恒生科技成分股、AI 概念板块 |
| `get_plate_list.py` | 搜索可用板块/主题 |

**实现方式**：`paper/market.py` 新增 `dyn_universe(screen_config)` 函数，通过 `get_stock_screen.py` 动态生成股票池。

---

## 第二部分：对未来有帮助的 API（因子挖掘/模型构建/策略构建）

### 1. 因子挖掘 — 全新维度的 Alpha 来源

**高价值 API（按因子挖掘潜力排序）**：

#### Tier 1（最高价值，直接产生 alpha 因子）

| API | 因子挖掘方向 | 市场 |
|-----|-------------|------|
| `get_financials_statements.py` | **质量因子**：ROE、ROIC、毛利率趋势、应计项目；**成长因子**：营收增速、EPS 增速、研发投入增速；**杠杆因子**：D/E ratio、利息覆盖倍数 | HK+US+A |
| `get_valuation_detail.py` | **价值因子**：PE/PB/PS 历史分位数（便宜/贵）、PEG、估值区间突破 | HK+US+A |
| `get_short_interest.py` | **情绪因子**：空头拥挤度、空头占比变化率、回补天数变化 | HK+US |
| `get_capital_distribution.py` | **资金流因子**：主力资金 vs 散户资金背离、大单占比趋势 | HK+US+A |
| `get_shareholders_holding_changes.py` | **Smart Money 因子**：机构增持信号、对冲基金建仓信号、持股集中度变化 | HK+US |

#### Tier 2（高价值，提供独特视角）

| API | 因子挖掘方向 | 市场 |
|-----|-------------|------|
| `get_research_analyst_consensus.py` | **分析师因子**：评级上调/下调动量、目标价上调幅度、评级分歧度（dispersion） | HK+US |
| `get_insider_trade_list.py` | **内部人因子**：内部人集群买入（cluster buy）、内部人卖出/买入比 | 仅 US |
| `get_financials_earnings_price_history.py` | **财报事件因子**：财报前后波动率变化（IV crush）、财报日 gap 幅度 | HK+US |
| `get_top_ten_buy_sell_brokers.py` | **经纪商因子**：高盛/摩根等顶级投行净买卖方向 | 仅 HK |
| `get_option_volatility.py` | **波动率因子**：IV rank、IV percentile、IV-HV spread（波动率溢价） | HK+US |
| `get_daily_short_volume.py` | **日频空头因子**：日内卖空占比、卖空成交活跃度 | HK+US |

#### Tier 3（中等价值，增强/替代现有因子）

| API | 因子挖掘方向 | 市场 |
|-----|-------------|------|
| `get_stock_screen.py` | **现成因子库**：直接使用 244+ 因子（技术形态、机构持仓、分析师评级、期权 IV 等） | HK+US |
| `get_shareholders_holder_detail.py` | **持股结构因子**：特定类型机构占比（对冲基金 vs 养老金）、股东稳定性 | HK+US |
| `get_company_operational_efficiency.py` | **效率因子**：人均创收/利润趋势、员工增长 vs 营收增长效率比 | HK+US+A |
| `get_corporate_actions_dividends.py` | **红利因子**：股息率、股息增长稳定性、派息比率 | HK+US+A |
| `get_corporate_actions_buybacks.py` | **回购因子**：回购收益率、回购频率、回购金额占市值比 | HK+A |
| `get_financials_revenue_breakdown.py` | **业务质量因子**：高利润率业务占比变化、地区收入增速分化 | HK+US+A |
| `get_research_morningstar_report.py` | **护城河因子**：晨星经济护城河评级、公允价值折溢价 | HK+US |

### 2. ML 模型构建 — 多模态特征融合

**现状**：`ml/trainer.py` 使用 39 个 OHLCV 因子 → OLS/Ridge/LightGBM → IC 评估。标签为 `fwd_ret_5d` 和 `fwd_ret_20d`。

**优化方向**：

1. **特征维度扩展**：将因子从 39 个扩展到 100+（加入上述 Tier 1 + Tier 2 的数据源）
2. **多时间尺度标签**：利用不同时间窗口的 forward return 构建多 horizon 模型
3. **市场间因子**：利用 `get_valuation_plate_stock_list.py` 构建板块中性化后的残差因子
4. **非线性因子交互**：LightGBM 可以自动发现基本面+技术面+资金流的交互效应

**关键 API 集成路径**：
```
F10 API → collectors/adapters/futu_fundamental_adapter.py → GCS → BQ tables
  → factors/fundamental_builder.py (compute → process → build_dataset)
  → factors/registry.py (register to BQ factor_registry)
  → ml/trainer.py (load expanded feature set → train → predict)
  → engine/data.py (DataFrameSource(pred=predictions) → ctx.predictions)
  → engine/strategy.py (strategy reads ctx.predictions for signals)
```

### 3. 策略构建 — 全新策略类型

| 策略类型 | 核心 API | 逻辑概述 |
|---------|---------|---------|
| **Quality-at-Reasonable-Price (QARP)** | `get_financials_statements.py` + `get_valuation_detail.py` | 筛选高 ROE (>15%) + 低 PE 分位 (<30%) + 盈利正增长的股票，等权持有 |
| **Insider Sentiment Momentum** | `get_insider_trade_list.py` + `get_shareholders_holding_changes.py` | 当内部人集群买入且机构同时增持时进场，内部人大额卖出时离场 |
| **Short Squeeze Hunter** | `get_short_interest.py` + `get_snapshot.py` | 筛选 short ratio > 20% + 价格从低点反弹 > 10% + 成交放量的标的 |
| **Sector Rotation** | `get_valuation_plate_stock_list.py` + `get_plate_stock.py` | 每月买入 PE 分位最低的 3 个板块的成分股，卖出 PE 分位最高的板块 |
| **Post-Earnings Drift** | `get_financials_earnings_price_move.py` + `get_financials_earnings_price_history.py` | 财报超预期后 1-5 天的漂移动量，叠加 IV crush 后的期权卖方策略 |
| **Flow-Momentum Hybrid** | `get_capital_flow.py` + `get_kline.py` | 价格横盘（低波动）+ 主力资金持续流入 → 突破信号 |
| **Dividend Capture** | `get_corporate_actions_dividends.py` | 在除息日前买入、除息日后卖出（需考虑税费和价格回补） |
| **Volatility Risk Premium** | `get_option_volatility.py` + `get_option_chain.py` | IV > HV 时卖出期权收取波动率溢价（需期权交易权限） |

### 4. 基础设施 — 需要新增的能力

**4.1 实时推送监控（`subscribe/` + `push_*.py`）**

当前 `ws_collector.py` 仅使用 `SubType.K_5M`。可扩展：
- `push_quote.py`：实时报价推送 → `dashboard/api.py` 的实时行情展示
- `push_orderbook.py`：买卖盘变化 → TWAP/VWAP 执行算法的实时市场深度
- `push_ticker.py`：逐笔成交 → 大单监控、异常交易检测
- `TradeOrderHandlerBase`：订单状态推送 → OMS 实时订单状态更新

**4.2 选股工具链**

| API | 项目中的应用 |
|-----|-------------|
| `get_stock_screen.py` | 替代当前硬编码的 `DEFAULT_SYMBOLS`，支持按 244+ 因子动态选股 |
| `get_plate_stock.py` | 支持按板块/指数/主题快速构建股票池 |
| `get_option_screen.py` | 期权策略开发：筛选高 IV、高持仓量、特定 delta 的期权合约 |

**4.3 数据质量增强**

| API | 用途 |
|-----|------|
| `get_trading_days.py` | 修正交易日历，避免在非交易日拉取数据 |
| `get_rehab.py` | 复权因子校验，确保回测价格准确性 |
| `get_corporate_actions_stock_splits.py` | 拆合股事件校正，避免回测中的伪收益 |

**4.4 期权生态（未来方向）**

| API | 用途 |
|-----|------|
| `get_option_chain.py` + `get_option_expiration_date.py` | 期权数据采集 → BQ → 期权策略回测 |
| `get_option_volatility.py` + `get_option_exercise_probability.py` | 期权 Greeks 和波动率分析 |
| `get_option_screen.py` | 期权筛选（按 IV、持仓量、Greeks） |

---

## 总结：优先级路线图

### 短期（1-2 周，高 ROI）
1. **因子扩展**：`get_financials_statements.py` + `get_valuation_detail.py` + `get_short_interest.py` → 新增 30+ 基本面/估值/情绪因子
2. **动态选股**：`get_stock_screen.py` → 替代硬编码股票池
3. **数据采集扩展**：新增 `futu_fundamental_adapter.py` 采集估值/空头/资金流数据入 BQ

### 中期（2-4 周）
4. **新策略开发**：QARP（质量+价值）、Post-Earnings Drift、Sector Rotation
5. **ML 模型升级**：100+ 特征 → LightGBM → 回测验证 IC 提升
6. **实时推送扩展**：报价推送 → dashboard 实时行情；订单推送 → OMS 实时状态

### 长期（1-3 月）
7. **期权生态**：期权链数据采集 → 期权策略回测 → 波动率交易
8. **事件驱动框架**：财报事件、分红事件、内部人交易事件 → 事件驱动策略引擎
9. **A 股扩展**：利用 Futu A 股 API 扩展至 A 股市场

---

## 验证方式

1. **因子验证**：新因子通过 `FactorEvaluation` IC 检验 → 注册到 `factor_registry`
2. **策略验证**：新策略通过 `engine/engine.py` 回测 → `WalkForward` 样本外验证
3. **数据验证**：新 adapter 采集的数据通过 `quality/` 模块检查完整性
4. **ML 验证**：扩展特征集在 `ml/trainer.py` 中对比 baseline（仅 OHLCV）vs enhanced（+ 基本面）的 IC 提升
