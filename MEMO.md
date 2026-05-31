# Quant 项目 — 状态备忘

> 更新日期: 2026-05-31 15:00 UTC · 当前分支: `main` · VM: e2-standard-4

---

## 一、整体进度总览

```
数据管道 ██████████████ 100%   ← 回填全完成 + F10 基本面全入库
因子注册 ██████████████ 100%   ← FactorRegistry BQ双表 + 39因子已入库
Futu 接入 █████████████░  95%   ← OpenD 运行中，ws_collector + cron 1d 已部署
策略验证 ████████████░░  85%   ← W1/W2✅ + W3 5m POC✅ + F10 IC完成
因子管道 ██████████████ 100%   ← 79因子 + 31.5M值 + 81评估
回测引擎 ████████████░  90%   ← ML pred 支持已接入，walk-forward 就绪
OMS/执行 ████████████░  90%   ← Futu Stock/Crypto Broker + Router 已实现
实盘交易 ██████░░░░░░░  40%   ← Paper Runner 就绪，缺 Live Loop + 状态持久化
全自动   ███░░░░░░░░░░  20%   ← 缺实时策略引擎、生产加固
```

---

## 二、策略验证进度

| 阶段 | 内容 | 状态 | 结果 |
|------|------|------|------|
| W1 | SimpleMomentum 全链路验证 | ✅ | 年化 11.00%, Sharpe 0.76, MaxDD -13.25% |
| W2 | ML LightGBM vs 动量 walk-forward | ✅ | ML 4.00% > 动量 3.32%, Sharpe 0.64 |
| W3 | 5m + cron | ✅ | Momentum 5m 20.24% (POC) |
| W4 | 评估 & Live Loop | ⏳ | 待执行 |
| Phase2 | F10 ML 扩展 | ✅ | 7 tasks 完成，已合并 main |

### W2 实验详情

```
📊 ML LightGBM vs SimpleMomentum (2026-01 → 2026-05, 20 stocks)

  Metric             Momentum      ML LightGBM
  ───────────────────────────────────────────
  Total Return         3.32%          4.00%  ✅
  Annual Return        8.51%         10.30%  ✅
  Sharpe               0.65           0.64   ≈
  Max Drawdown       -12.34%        -14.90%  ⚠️
  Win Rate            42.00%         43.00%  ✅
```

---

## 三、F10 基本面数据（2026-05-31 完成）

### 采集与 BQ 状态

| 数据类型 | US | HK | BQ 分区 | 备注 |
|----------|-----|-----|---------|------|
| valuation | 876,576 行 / 156 只 | 64,258 行 / 11 只 | DAY(ingest_time) | PE/PB/PS x 3区间 x 历史 |
| capital_flow | 234 行 / 234 只 | 15 行 / 15 只 | DAY | 主力/大/中/小单资金流 |
| analyst | 234 行 / 234 只 | 15 行 / 15 只 | DAY | 分析师评级+目标价 |
| shareholder | 9,347 行 / 234 只 | 600 行 / 15 只 | DAY | 股东分布+持股变化 |
| financials | 1,176,997 行 / 128 只 | 42,006 行 / 11 只 | NONE | 4 类报表 x 19 个指标，含 yoy/qoq |
| short_interest | — | — | — | ❌ Futu API 不支持美股卖空数据 |

**总计: 10 张 F10 表 + 6 张 bars 表 + 2 张因子库表 = 19 张 BQ 表**

### Financials Adapter 修复记录

```
初始问题: structure_list 只有 field_id 无 name → 列名回落 c0-c9
         report_list[0] 是表头被当数据行
         item_list 嵌套 [{field_id, data, yoy, qoq}] 未展开

修复方案:
  1. report_list[0] 作列名 → report_list[1:] 作数据
  2. 展开 item_list → 一行=一个财务指标 (19 field_id)
  3. field_id → field_name 映射表 (revenue / net_income / eps...)
  4. 保留 qoq/yoy 增速字段

采样验证:
  AAPL Q1/2026: revenue=143.756B, yoy=+15.7%, qoq=+40.3%  ✓
```

### 灌 BQ 过程教训

| 问题 | 解决方案 |
|------|----------|
| JSON 列类型不支持 | load_table_from_dataframe 不支持 JSON → 改 STRING |
| 逐文件上传慢 | 每个 2-3s × 264 个 ≈ 8min → 改用 load_table_from_uri 原生 Parquet |
| BQ 不支持 ** glob | 需显式 list_blobs 传 URI 列表 |
| us_capital_flow 分区丢失 | 第一次 autodetect+分区冲突重试时忘了补，已修复 |
| HK financials 列类型 qoq INT32 | 整型不一致 → pandas concat + to_numeric 统一 |
| Python 3.10 缺 datetime.UTC | 改 timezone.utc (3.10 compat)；VM 已升 python3.12 |

### F10 因子体系

| 组件 | 状态 | 详情 |
|------|------|------|
| F10Transformer | ✅ | 9分类41因子，BQ原始表→builder格式 |
| factor_registry | ✅ | 79因子 (39 tech + 40 F10) |
| factor_values | ✅ | 31.5M行，dbdate bug已修复 |
| factor_evaluations | ✅ | 81行IC，16因子通过准入 |
| compute 脚本 | ✅ | registry-driven + incremental (tech每日/F10每周) |
| cron 定时任务 | ✅ | tech每日增量 + F10每周全量 |

---

## 四、运行中基础设施

### VM 部署 (GCE asia-east2-a)

| 组件 | 方式 | 状态 | 详情 |
|------|------|------|------|
| OpenD | 手动 | ✅ 运行 | `127.0.0.1:11111`，行情+交易双登录 |
| ws_collector (5m) | systemd | ✅ 运行 | WebSocket 推送，US 234 + HK 15 + Crypto 10 |
| US 1d 采集 | cron (21:30 UTC) | ✅ | Futu API，Mon-Fri，收盘后 |
| HK 1d 采集 | cron (08:30 UTC) | ✅ | Futu API，Mon-Fri，收盘后 |
| BQ Loader ×6 | cron | ✅ | US/HK/Crypto × 1d+5m；cron_wrapper.sh + cd /opt/quant |

### BQ 数据状态

| 表 | 行数 | 状态 |
|----|------|------|
| us_bars_1d | 372,723 | ✅ 就绪 (2020-2026) |
| us_bars_5m | 365,470 | 🔄 持续入库中 (ws_collector) |
| hk_bars_1d | 10,342 | 🔄 fallback 加载中 |
| hk_bars_5m | 18,950 | 🔄 持续入库中 |
| crypto_bars_1d | 10 | ⏸️ 搁置 |
| crypto_bars_5m | 530 | ⏸️ 搁置 |
| us_valuation | 876,576 | ✅ 新入库 |
| hk_valuation | 64,258 | ✅ 新入库 |
| us_financials | 1,176,997 | ✅ 新入库 |
| hk_financials | 42,006 | ✅ 新入库 |

---

## 五、数据采集架构

```
实时层:   ws_collector (systemd, WebSocket 5m) ──→ GCS ──→ BQ
定时层:   main.py cron ×2 (收盘 1d)            ──→ GCS ──→ BQ
历史层:   backfill.py (回填)                    ──→ GCS ──→ BQ
基本面:   fundamental_collector.py               ──→ GCS ──→ BQ

标的:     三者统一通过 Futu API fetch_supported_symbols() 获取
          US=234 / HK=15 / Crypto=10
GCS 路径: raw/{market}/{type}/year={YYYY}/month={MM}/day={DD}/symbol={SYMBOL}.parquet
BQ 表:    quant.{market}_{type} — PARTITION BY DATE(ingest_time), CLUSTER BY symbol
查询层:  所有模块直接查询 BigQuery（GCS 仅写入归档）
```

---

## 六、回填进度 — ✅ 全部完成

| # | 任务 | 标的 | 行数 | 状态 |
|---|------|------|------|------|
| 1 | 🇺🇸 US 1d | 234只 | 373,595 | ✅ 完成 + BQ 入库 |
| 2 | 🇭🇰 HK 1d | 15只 | 28,161 | ✅ 完成 + BQ loading |
| 3 | 🇺🇸 US 5m | 234只 | 456,300 | ✅ 完成 + BQ loading |
| 4 | 🇭🇰 HK 5m | 15只 | 23,760 | ✅ 完成 + BQ loading |
| 5 | 🪙 加密币 | 15对 | — | ⏸️ 搁置 |

---

## 七、项目结构

| 目录 | 内容 | 状态 |
|------|------|------|
| `strategies/` | MLPredStrategy + SimpleMomentum | ✅ |
| `factors/registry.py` | FactorRegistry — BQ 双表 + 准入标准 | ✅ |
| `factors/evaluation.py` | 因子 IC/衰减/覆盖率评估 | ✅ |
| `ml/trainer.py` | +load_from_bq() 方法 | ✅ |
| `collectors/` | fundamental_collector.py + 6 F10 adapters | ✅ 新增 |
| `scripts/` | init_factor_registry / run_w2_experiment / load_f10_bq | ✅ |
| `sql/` | factor_registry_schema + f10_schemas | ✅ |

---

## 八、最近代码改动

| Commit | Branch | 改动 |
|--------|--------|------|
| `e6ac647` | feature/phase2-ml-strategy | fix: Python 3.10 compat + F10 BQ loader |
| `4e1aa4b` | main | Merge PR #8: f10-factor-expansion |
| `eae0b41` | main | docs: W3-W4 实施计划 |
| `6cd86b7` | main | feat: W2 完成 — ML LightGBM beats momentum |

---

## 九、已解决 vs 待解决

### ✅ 已解决

| 问题 | 解决方案 |
|------|----------|
| Phase 0 数据管道 | VM cron + ws_collector + backfill |
| BQ dedup 分区冲突 | CREATE OR REPLACE TABLE 显式 PARTITION/CLUSTER |
| Parquet 纳秒时间戳 | storage.py 微秒 + BQ loader pandas fallback |
| 因子注册制 | FactorRegistry BQ 双表 + 79 因子 (39 tech + 40 F10) |
| PaperRunner 全链路 | W1 动量验证 + W2 ML 双策略 |
| F10 基本面 | 6 种数据 × 2 市场 = 10 张 BQ 表 |

### 🔴 仍待解决

| # | 问题 | 说明 |
|---|------|------|
| 1 | **F10 IC 偏弱** | F10 因子日频 IC 偏低 (均<0.02)，需季度fwd_ret |
| 2 | **F10 样本量有限** | 季度财报仅覆盖~128只美股，训练集受限 |
| 3 | **cron 未首次验证** | evaluate/compute cron 已部署但未跑过首轮 |
| 4 | **无 Live Trading Loop** | 没有 run_live.py 主循环 |
| 5 | **状态无持久化** | Engine/Portfolio 重启丢失 |

---

## 十、当前决策

| 决策项 | 决定 |
|--------|------|
| 数据源 | Futu API 作为 US/HK/Crypto 主源 |
| 数据查询 | 一律走 BigQuery，GCS 仅写入归档 |
| 部署方式 | VM cron + systemd，不用 Cloud Run |
| 回填策略 | 串行执行（Futu API 限速 60req/30s） |
| 策略验证路线 | B→A：先 paper 验证策略有效性，再上实盘 |
| 因子注册制 | BQ 双表 + IC>0.05/t-stat>3/cov>90% 准入 |
| Parquet 兼容 | storage.py 微秒精度 + BQ loader pandas fallback |
| 加密币 | 暂时搁置 |
| F10 数据 | 全量写入 GCS 再批量灌 BQ（原生 Parquet URI 加载） |

---

## 十一、教训

| 教训 | 详情 |
|------|------|
| Futu API 限速 | 60/30s，回填必须串行 |
| GCS 路径陷阱 | adapter.market="MIXED" → 写入 raw/mixed/ → 数据丢失 |
| BQ dedup 分区 | CREATE OR REPLACE TABLE 不保留分区→需显式声明 |
| Parquet 纳秒 vs 微秒 | PyArrow 写 INT64_NANOS，BQ 只读 MICROS |
| BQ JSON 类型 | load_table_from_dataframe 不支持 JSON 列 → 用 STRING |
| F10 BQ 加载速度 | 逐文件 DataFrame upload 极慢 → load_table_from_uri 原生加载 |
| Futu financials API | structure_list 无 name 只有 field_id；列名在 report_list[0] |
| Financials item_list | 嵌套 [{field_id, data, yoy, qoq}] 必须展开为扁平行 |
| Python 3.10 compat | datetime.UTC → timezone.utc；crypto_binance_adapter 修复 |
| BQ autodetect 分区 | autodetect 创建的表无列级分区 → 需显式 CREATE 加 PARTITION BY |

| dbdate 类型陷阱 | BQ DATE 列回 pandas 是 `dbdate` 对象，直接 `pd.DatetimeIndex` 导致 join 全 NaN → 需 `.astype(str).str.slice(0,10)` |
| F10 symbol 前缀 | F10 用 `US_AAPL`（下划线），bars 用 `US.AAPL`（点） → factor 构建需统一 |
| BQ streaming buffer | `insert_rows_json` 后数据在 streaming buffer ~90min 无法 UPDATE/DELETE → 因子计算需等 buffer 清空 |

---

## 十二、关键指标

- **Be行**: Python ~15k 行 / Terraform ~600 行
- **测试覆盖**: 140+ Python 测试
- **因子库**: 79 因子注册 (39 tech + 40 F10)，准入标准 IC>0.05/t-stat>3/cov>90%
- **策略**: SimpleMomentum + MLPredStrategy (LightGBM)
- **BQ 表**: 6 bars + 2 因子库 + 10 F10 = 18 张，分区+聚簇
- **Git 分支**: `main` / `feature/phase2-ml-strategy` (活跃)
- **VM**: GCE e2-standard-4, Ubuntu 24.04, python3.12
