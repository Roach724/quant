# 策略库全面整治方案

> 日期: 2026-06-12 | 目标: 7 个存量策略审计 + 经典策略扩展

---

## 总览

| 优先级 | 策略 | 动作 | 工作量 |
|--------|------|------|--------|
| **P0** | BuyHold / SimpleMomentum / MLPrediction | 直接配置实验上线 | 0.5h |
| **P1** | MeanReversion | 修复退出逻辑 + 补 rebalance | 1h |
| **P2** | QARP / ShortSqueeze | 开发数据管道（因子复合分） | 3h |
| **P3** | SectorRotation / FundingRateArbitrage | 重写核心逻辑 | 3h+ |
| **P4** | MACD / 双均线 / ATR止损 / 布林带 / RSI-2 / 海龟 / 多因子 / 配对交易 | 新实现 8 个经典策略 | 6h |

---

## P0 — 立即可用（已有实验或一键配置）

### 1. BuyHold — 买入持有基准

```
策略: 第一个 bar 等权买入全仓，持有到底
参数: weight_per_symbol=0.1
状态: ✅ 完整可用
用途: 作为其他策略的 baseline 比较基准
```

**实验配置建议：**

```yaml
# paper/config_buyhold_us.yaml
market: us
strategy: BuyHold
capital: 100000
start: "2024-01-01"
end: "2025-12-31"
data_source: sdk
strategy_kwargs:
  weight_per_symbol: 0.1
```

### 2. SimpleMomentum — 动量排序

```
策略: 过去 N bar 涨幅排名，选 top_k，每 R bar 调仓
参数: lookback=60, top_k=5, rebalance_every=13
状态: ✅ 已有实验 Exp2(us) Exp4(hk)，完整可用
卖出: ✅ 清旧仓 → 建新仓
```

### 3. MLPrediction — ML 预测选股

```
策略: LightGBM 模型预测收益率，选 top_k，每 R bar 调仓
参数: model_name="momentum_lgbm", top_k=10, rebalance_every=5
状态: ✅ 已有实验 Exp1(us) Exp3(hk)，刚修完哨兵+卖出逻辑
卖出: ✅ 清旧仓 → 建新仓（2026-06-12 修）
```

---

## P1 — 小修即用（1 个）

### 4. MeanReversion — 均值回复

**当前问题：**

```
买入: z-score < -1.5  ✅
卖出: z-score > +1.5  ✅
但是: z 在 [-1.5, 1.5] 区间 → 永不处理 ❌
```

持仓可能进入"僵尸状态"——不够超买卖出，一直赖着不走。

**修复方案：**

| 改动 | 说明 |
|------|------|
| 加 `max_hold_bars` 参数 | 持仓超过 N bar 强制退出（默认 60） |
| 或改为 rebalance 模式 | 照 SimpleMomentum 每次调仓清旧换新 |
| `ctx.data.close[sym]` → `.iloc[bar][sym]` | 统一 DataFrameSource 访问方式 |

**建议：加超时退出 + 保持超买/超卖双向逻辑。** 均值回复策略的特质就是截断正态分布尾部，改成完全 rebalance 就变成动量策略了。

```yaml
# 实验配置
strategy: MeanReversion
strategy_kwargs:
  lookback: 30
  entry_threshold: -1.5    # 超卖买入
  exit_threshold: 1.5      # 超买卖出
  max_hold_bars: 60        # 新增：最大持仓 bar 数
  top_k: 5
```

---

## P2 — 需数据管道（2 个）

### 5. QARP — 质量价值复合分

**现状：** 策略逻辑完整（按排名选股 + rebalance + 卖出），但依赖 `ctx.predictions` 传入预计算的 QARP 复合分。当前无此管道 → `return []` 空跑。

**需要开发：**

```
QARP 分数 = 质量因子 + 价值因子 加权
├── 质量: ROE, ROA, 毛利率, 负债率
├── 价值: PE, PB, PS, FCF yield
└── 输出: dict[symbol → score] → ctx.predictions
```

**实现方式：**
1. 在 `factors/` 下新建 `composite.py`，实现 `compute_qarp_scores(symbols, bar_data, factor_weights)` 
2. 或者在 BQ 预计算好 QARP 分数表，backfill 时一起回填
3. QARP 策略读取并排名的逻辑已就绪，只需管道

### 6. ShortSqueeze — 空头挤压

**现状：** 策略逻辑完整（含 `Signal.target` 调权重 + 卖出），也依赖 `ctx.predictions`。

**需要数据：**
- 做空比率 (short interest ratio)
- 回补天数 (days to cover)  
- 近期价格动量

这些数据在美股可从 SEC/FINRA 获取，港股从 HKEX 获取。当前 F10 采集已覆盖部分字段。

**实现方式：**
1. 检查 F10 表是否已有 `short_interest` / `days_to_cover` 字段
2. 有 → 写一个 `compute_short_squeeze_scores()` 组合分数
3. 没有 → 在 F10 采集模块加这两个字段

---

## P3 — 需重写（2 个）

### 7. SectorRotation — 行业轮动

**当前问题（严重）：**

```python
factor: str = "roe"    # ← 声明用 ROE 排名，但一行都没用到
# 实际: 硬编码取 HSI+HSTECH 所有股票，不分青红皂白全买
syms = UniverseBuilder.from_plate(plate)
for sym in all_symbols[:50]:
    signals.append(Signal.buy(sym, ...))  # ← 永不卖出
```

**重写方案：**

```
真正的行业轮动逻辑:
1. 按 sector/plate 分组
2. 计算每组的聚合因子值（ROE 中位数 / 动量均值）
3. 选排名 top_k 的行业
4. 在选中的行业内，选 top_n 个股（等权或市值加权）
5. 每次 rebalance: 砍掉跌出排名的行业和个股
```

**依赖：**
- `UniverseBuilder.from_plate()` — 已有
- 因子计算 — `TechFactorBuilder` 已有
- 行业聚合 → 新逻辑

### 8. FundingRateArbitrage — 资金费率套利

**当前问题：** 需要 `ctx.predictions` 提供 `funding_rate` 数据。无数据源。

**实际情况：**
- 加密货币资金费率数据需要从交易所 API 实时获取（Binance/OKX 的 `fundingRate` 字段）
- 当前系统没有 crypto 数据管道
- **建议：** 暂缓，等 crypto 数据基建完成后再说

---

## P4 — 经典策略新增（8 个）

### 9. MACD — 指数平滑异同移动平均线

```
逻辑: 
  MACD线 = EMA(12) - EMA(26)
  信号线 = EMA(9) of MACD线
  金叉: MACD线上穿信号线 → 买入
  死叉: MACD线下穿信号线 → 卖出
参数: fast=12, slow=26, signal=9
信号: MACD > Signal 且持仓空 → buy / MACD < Signal 且持仓有 → close
难度: ⭐
依赖: 无（纯 EMA 计算）
```

**亮点：** Gerald Appel 1970 年代发明，全球最广泛使用的技术指标之一。包含趋势+动量双重信息。比纯双均线更灵敏，假信号更少。

### 10. MACrossover — 双均线趋势跟踪

```
逻辑: 快线上穿慢线 → 买入，下穿 → 卖出
参数: fast=10, slow=30
信号: 金叉 Signal.buy / 死叉 Signal.close
难度: ⭐
依赖: 无（纯价格计算）
```

**亮点：** 经典到不能再经典，每个量化库都有的元老策略。简单好用。与 MACD 互补：一个看斜率，一个看交叉 + 动量确认。

### 11. ATRTrailingStop — ATR 自适应跟踪止损

```
逻辑:
  入场: 不在持仓时，简单用均线上穿或动量
  止损: 最高价 - N × ATR（动态上移，不下降）
  离场: 收盘价跌破跟踪止损线 → 平仓
参数: atr_period=20, multiplier=3.0, entry_ma=50
信号: 价格 > MA50 且空仓 → buy / 价格 < 最高价 - 3*ATR → close
难度: ⭐⭐
依赖: 无（ATR + 均线纯价格计算）
```

**亮点：** 波动率自适应的止损，避免固定百分比止损在波动大时过早离场。趋势市效果好，让利润奔跑的同时控制回撤。海龟策略的止损核心。

### 12. BollingerBands — 布林带突破

```
逻辑: 价格触及下轨(mean-2σ) → 买入，突破上轨(mean+2σ) → 卖出
参数: window=20, sigma=2.0
信号: 下轨触及时持仓空 → buy / 上轨触及时持仓有 → close
难度: ⭐
依赖: 无（纯价格+标准差）
```

**亮点：** 统计严格（基于正态分布假设），调 sigma 可调节灵敏度。

### 13. RSI2 — Larry Connors 超短线均值回复

```
逻辑: 2 日 RSI < 10（极端超卖）→ 买入，RSI > 70 或次日 → 卖出
参数: rsi_period=2, buy_threshold=10, sell_threshold=70
信号: RSI<10 时 buy / RSI>70 或持仓 2 天以上 close
难度: ⭐
依赖: 无（纯 RSI）
```

**亮点：** Larry Connors 经典论文策略。高胜率（~85%），回撤控制好。适合震荡市。

### 14. TurtleTrading — 海龟交易法

```
逻辑: 
  入场: 价格突破 N 日最高价（Donchian 通道上沿）
  退出: 价格跌破 M 日最低价（Donchian 通道下沿）
  仓位: ATR-based 波动率自适应（风险 2%）
参数: entry_days=20, exit_days=10, atr_period=20, risk_pct=0.02
信号: 突破上轨 buy / 跌破下轨 close
难度: ⭐⭐
依赖: 无（Donchian + ATR 纯价格计算）
```

**亮点：** Richard Dennis 的传奇策略。完整的趋势跟踪体系，包含波动率自适应仓位管理。回测表现抗趋势市。

### 15. MultiFactorRank — 多因子等权排名

```
逻辑: 对每只股票计算多因子 z-score → 等权求和 → 排名选 top_k
因子池: 动量(20d/60d)、波动率(负向)、换手率(负向)、RSI
参数: factors=["mom_20d","mom_60d","vol_20d","turnover","rsi_14"], top_k=10
信号: 每次 rebalance → 清旧仓 → 买新 top_k
难度: ⭐⭐
依赖: TechFactorBuilder（已有）
```

**亮点：** 多因子分散风险，比单因子动量更稳健。可以扩展为 ICIR 加权或动态因子选择。因子注册表已有 39 个因子可用。

### 16. PairsTrading — 配对交易 / 统计套利

```
逻辑:
  1. 预选高相关性股票对（同行业/同市值）
  2. 实时计算价差 z-score = (spread - mean) / std
  3. z-score > +2.0 → 做空价差（short 强, long 弱）
  4. z-score < -2.0 → 做多价差（long 强, short 弱）
  5. z-score 回归 0 附近 → 平仓
参数: lookback=60, entry_z=2.0, exit_z=0.5, top_pairs=5
信号: Signal.buy(弱) + Signal.sell(强) 成对发出
难度: ⭐⭐⭐
依赖: 预计算相关性矩阵 + 实时价差跟踪
```

**亮点：** 市场中性的纯 alpha 策略，与大盘涨跌无关。真正的统计套利。需要 cointegration 检验 + 半衰期估计确保对子稳定。

**实现方式：**
- 离线: 跑协整检验筛选候选对子 → 存到对子注册表
- 运行时: 对每对对子计算 rolling z-score → 触发信号
- 对子库定期更新（每月/每季），避免协整关系退化

---

## 实施路线图

```
Week 1:  P0 配置实验 + P1 MeanReversion 修复
Week 2:  P4 前 4 个经典策略（MACD / 双均线 / Bollinger / RSI2）
Week 3:  P4 后 4 个（ATR止损 / 海龟 / 多因子 / 配对交易调研）
Week 4:  P2 管道开发 + P3 重写方案细化 + 配对交易实现
```

### 第一波（立即收益）

- ✅ BuyHold 纸交易 → 建立 baseline 基准曲线
- ✅ MeanReversion 修复上线 → 3 策略并行对比
- ✅ MACD / 双均线 / Bollinger / RSI2 → 策略库从 3 个可用扩充到 7 个

### 总策略库容量（完成后）

| 类型 | 数量 | 策略 |
|------|------|------|
| 基准 | 1 | BuyHold |
| 趋势跟踪 | 5 | SimpleMomentum, MACD, MACrossover, ATRTrailingStop, TurtleTrading |
| 均值回复 | 3 | MeanReversion, BollingerBands, RSI2 |
| ML选股 | 1 | MLPrediction |
| 多因子 | 2 | MultiFactorRank, QARP |
| 事件驱动 | 1 | ShortSqueeze |
| 行业轮动 | 1 | SectorRotation |
| 统计套利 | 1 | PairsTrading |
| 套利 | 1 | FundingRateArbitrage |
| **合计** | **16** | |
