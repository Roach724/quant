# Crypto 量化策略与因子体系设计方案

**设计人**: Quant Agent 📈
**设计日期**: 2025-05-08
**定位**: 从零开始的 Crypto 量化研究路线图，纯基于 Crypto 市场特性独立设计
**目标市场**: CEX 现货 + 永续合约（Binance / OKX / Bybit 主力）

---

## 一、Crypto 市场特性分析

### 1.1 与传统市场的核心差异

| 维度 | 传统市场（港股/美股） | Crypto 市场 | 策略含义 |
|------|---------------------|------------|---------|
| **交易时间** | 固定时段（港股 5.5h/日） | **24/7/365** 连续交易 | 需要处理周末和凌晨的数据漂移；不能假设「隔夜收益」 |
| **波动率水平** | 年化 15–35%（个股） | **年化 60–120%**（BTC），**80–200%**（山寨币） | Sharpe 分母大，需要更高的 Alpha 才能获得同等 IR；杠杆天然适配 |
| **市场微观结构** | 中央限价订单簿（成熟） | 多交易所碎片化订单簿 + AMM DEX 并行 | 跨交易所套利机会持续存在 |
| **参与者结构** | 机构主导（>60%） | **散户主导**（>70%），情绪驱动强 | 行为因子（恐慌/贪婪、社交媒体）预测力远超传统市场 |
| **数据透明度** | 持仓/成交量可能不透明 | **链上完全透明**（地址级），CEX 订单簿也透明 | 可以构建传统市场无法实现的链上因子 |
| **费率结构** | 印花税+佣金+滑点 | Maker -0.005%~+0.02%, Taker 0.04%~0.06% | **费率极低**，高频策略可行 |
| **做空机制** | 港股有限制（Tick Rule） | **无限制**（永续合约天生双向） | 多空策略更容易实现 |
| **杠杆** | 通常 1-2x | **现货杠杆 3-5x，合约 20-125x** | 资金管理极度重要；爆仓风险真实 |
| **相关性结构** | 个股间低相关（0.1-0.3） | **币种间极高相关**（BTC-山寨 0.6-0.9） | 纯多策略分散化效果有限；需配对交易或市场中性的 Alpha |

### 1.2 数据可得性矩阵

| 数据类别 | 获取方式 | 成本 | 历史深度 | 难度 |
|---------|---------|:---:|:---:|:---:|
| **CEX OHLCV** | CCXT / Binance API / Cryptoquant | 免费 | 2017+ | 低 |
| **CEX 订单簿 tick** | 交易所 WebSocket | 免费 | 实时（历史需自存） | 中 |
| **合约资金费率** | CCXT `fetch_funding_rate` | 免费 | 2019+ | 低 |
| **合约未平仓量 (OI)** | CCXT `fetch_open_interest` | 免费 | 2019+ | 低 |
| **合约多空比** | 交易所 API | 免费 | 2020+ | 中 |
| **链上活跃地址** | Glassnode / Dune / Etherscan API | 部分付费 | 2009+ | 中 |
| **链上大额转账** | Whale Alert API | 免费/付费 | 实时 | 低 |
| **稳定币净流量** | Glassnode / CoinMetrics | 付费 | 2019+ | 中 |
| **交易所净流入/流出** | CryptoQuant / Glassnode | 付费 | 2019+ | 中 |
| **社交媒体情绪** | LunarCrush / Santiment | 付费 | 2018+ | 中 |
| **BTC ETF 流量** | Farside Investors / Bloomberg | 免费 | 2024+ | 低 |
| **DEX 交易量** | Dune Analytics / The Graph | 免费 | 2020+ | 中 |
| **Gas 费用** | Etherscan API | 免费 | 2015+ | 低 |

**推荐数据栈**：
```
免费路线: CCXT (CEX数据) + CoinGecko API (市值/排名) + Etherscan (ETH链上) 
         + Whale Alert (大额转账) + Glassnode Free Tier (BTC/ETH 基础指标)

升级路线: Glassnode Advanced ($29/mo) + CryptoQuant ($29/mo) → 链上全覆盖
```

### 1.3 Crypto 特有风险

| 风险类别 | 具体表现 | 策略应对 |
|---------|---------|---------|
| **交易所风险** | FTX 2022 倒闭、Binance 宕机 | 多交易所部署 + 资产分仓（≤20%/交易所） |
| **插针（Wick）** | 瞬时价格极端波动后立即回归 | 回测剔除 ±10σ 异常点；实盘用 TWAP 或限价单 |
| **资金费率风险** | 极端行情下费率可达 -0.1%~+0.3%/8h | 资金费率套利需监控费率曲线 |
| **流动性危机** | 山寨币流动性枯竭（买卖价差 >5%） | 市值+成交量过滤（市值 > $100M, 日成交量 > $10M） |
| **监管风险** | 某币被交易所下架或认定为证券 | 每日监控交易所公告和下架通知 |
| **稳定币脱锚** | USDT/USDC 脱锚（如 2023-03 SVB 事件） | 多稳定币分散 + USDC/USDT 利差监控 |
| **MEV/抢跑** | 链上交易被 MEV bot 抢跑 | 非链上执行策略影响小；链上策略需 flashbots 保护 |
| **山寨币归零** | 90%+ 山寨币生命周期 < 2 年 | 指数化选币 + 动态淘汰机制 |

---

## 二、因子体系设计

### 2.1 量价因子（Technical / Price-Volume）

| # | 因子名 | 逻辑 | 输入 | 输出 | 频率 | 难度 |
|:---:|------|------|------|------|:---:|:---:|
| 1 | `crypto_momentum_7d` | Crypto 动量效应极强（7日动量对短期收益预测力远超传统市场），但衰减快（>30日反转） | close | z-score(ret_7d) | 1d | 低 |
| 2 | `crypto_momentum_cross_section` | 截面动量：过去 N 日表现最强的币，未来 N 日持续跑赢。Crypto 中截面动量 > 时序动量 | close（全币种） | cross-sectional rank(ret_Nd) | 1d | 低 |
| 3 | `crypto_short_term_reversal` | 1–4 小时级别反转效应显著（散户追涨杀跌后的均值回归）。频率越高，反转越强 | close | -ret_1h（或 ret_4h 反转） | 1h | 低 |
| 4 | `crypto_vol_regime` | 波动率聚类在 Crypto 极度显著（GARCH α+β 常 >0.99）。当前波动率水平预测未来波动率，进而影响仓位 | close | realized_vol_20d / realized_vol_60d | 1d | 低 |
| 5 | `crypto_volume_price_divergence` | 量价背离：价格上涨但量下降 → 趋势衰竭信号。在 Crypto 比传统市场更有效（散户跟风买但主力已撤退） | close, volume | sign(ret_5d) × (-Δvolume_5d) | 1d | 低 |
| 6 | `crypto_amihud_illiquidity` | 流动性不足的币有正流动性溢价（散户推高价格），但也面临更高的崩盘风险 | close, volume | abs(ret_1d) / dollar_volume_1d | 1d | 中 |
| 7 | `crypto_weekend_effect` | 周末交易量低、波动高、方向性弱。周一开盘常有趋势突破 | close, volume | I(weekend) × ret_fri_to_mon | 1d | 低 |

### 2.2 衍生品因子（Derivatives）

| # | 因子名 | 逻辑 | 输入 | 输出 | 频率 | 难度 |
|:---:|------|------|------|------|:---:|:---:|
| 8 | `perp_funding_rate` | 资金费率是 Crypto 最独特、最有效的因子。极端正费率 → 多头拥挤 → 即将回调；极端负费率 → 空头拥挤 → 即将反弹 | funding_rate | z-score(funding_rate, cross_section) | 8h 或 1d | 低 |
| 9 | `perp_funding_rate_momentum` | 费率方向变化比费率绝对值更有信息量。费率从负转正 → 市场情绪从空转多 | funding_rate | funding_rate(t) - funding_rate(t-3d) | 1d | 低 |
| 10 | `perp_open_interest_change` | OI 增加 + 价格上涨 = 多头加仓（趋势确认）。OI 增加 + 价格下跌 = 空头加仓（趋势加速）。OI 减少 = 仓位出清/趋势衰竭 | open_interest, close | ΔOI_1d × sign(ret_1d) | 1d | 中 |
| 11 | `perp_oi_vs_volume` | OI/Volume 比：高 = 杠杆率高 = 潜在的踩踏风险。极端值预示大波动 | open_interest, volume | OI / volume_24h | 1d | 中 |
| 12 | `perp_long_short_ratio` | 多空比极度偏离 1 → 单边拥挤 → 反转。在极端值（>3 或 <0.3）时预测力最强 | long_short_ratio | (LSR - 1) 的 z-score | 1d | 中 |
| 13 | `perp_basis` | 永续合约 vs 现货基差 = 隐含的资金成本。基差异常扩大 → 套利资金即将介入 → 价格收敛 | perp_price, spot_price | (perp - spot) / spot | 1h | 低 |

### 2.3 链上因子（On-Chain）

| # | 因子名 | 逻辑 | 输入 | 输出 | 频率 | 难度 |
|:---:|------|------|------|------|:---:|:---:|
| 14 | `onchain_active_addresses_change` | 活跃地址数是链上基本面中最稳健的领先指标。地址数增长 → 网络价值增长 → 币价上行（Metcalfe's Law） | daily_active_addresses | Δlog(addresses_30d) | 1d | 中 |
| 15 | `onchain_whale_accumulation` | 大额地址（>1000 BTC / >10000 ETH）持仓变化是「聪明钱」指标。大地址增持 → 看涨 | whale_balance | Δwhale_balance_7d / total_supply | 1d | 中 |
| 16 | `onchain_exchange_netflow` | 净流入交易所 → 准备卖出（看跌）；净流出交易所 → 转冷钱包（看涨）。反转信号：极端流入后反弹，极端流出后回调 | exchange_inflow, exchange_outflow | (inflow - outflow) / total_supply | 1d | 中 |
| 17 | `onchain_stablecoin_exchange_reserve` | 交易所稳定币储备增加 → 购买力增强 → 短期看涨。是 Crypto 版的「dry powder」指标 | stablecoin_exchange_balance | Δlog(balance_7d) | 1d | 中 |
| 18 | `onchain_nvt_ratio` | NVT（Network Value to Transactions）= 市值 / 链上转账量。高 NVT → 估值过高（市值为交易量不支撑）。类似于传统 PE 比率 | market_cap, transaction_volume | NVT 的 z-score（90日滚动） | 1d | 中 |
| 19 | `onchain_sopr` | SOPR（Spent Output Profit Ratio）= 花费的 UTXO 的已实现盈亏比。SOPR > 1 = 持有人盈利卖出。持续 >1 = 牛市（获利盘被吸收） | realized_value, spent_value | SOPR 的 7日 EMA | 1d | 高 |
| 20 | `onchain_mvrv_zscore` | MVRV Z-Score = (市值 - 已实现市值) / σ。识别周期顶部（>7）和底部（<0）。历史上精确标注了 4 个牛熊分界 | market_cap, realized_cap | MVRV Z-Score | 1d | 中 |

### 2.4 情绪/另类因子（Sentiment / Alternative）

| # | 因子名 | 逻辑 | 输入 | 输出 | 频率 | 难度 |
|:---:|------|------|------|------|:---:|:---:|
| 21 | `sentiment_fear_greed_index` | Crypto Fear & Greed Index（0-100）。极端恐惧（<25）→ 买入机会；极端贪婪（>75）→ 卖出信号 | fear_greed_index | -z-score(index)（方向反转） | 1d | 低 |
| 22 | `sentiment_social_volume` | 社交媒体提及量（Twitter/Reddit/Telegram）。异常高社交量 → 散户 FOMO 买入 → 短期顶部。在 Meme 币上尤其有效 | social_mentions_count | z-score(Δmentions_24h)（方向反转） | 4h | 中 |
| 23 | `sentiment_social_sentiment_score` | NLP 情绪打分。积极情绪占比上升 → 短期看涨（动量）→ 中期看跌（反转）。双周期特征 | social_text_sentiment | avg_sentiment_24h | 4h | 高 |
| 24 | `alt_btc_dominance_change` | BTC 市值占比变化。BTC.D 上升 = 山寨季结束 → 山寨币看跌；BTC.D 下降 = 山寨季开始 → 山寨币看涨 | btc_market_cap, total_market_cap | -ΔBTC.D_7d | 1d | 低 |
| 25 | `alt_stablecoin_flow_to_exchanges` | 稳定币净流入交易所（所有链合计）= 场外资金入场信号。100M+ USDT/USDC 日净流入异常值 → 短期看涨 | stablecoin_net_exchange_flow | z-score(net_flow_24h) | 1d | 中 |

### 2.5 宏观因子（Macro / Cross-Asset）

| # | 因子名 | 逻辑 | 输入 | 输出 | 频率 | 难度 |
|:---:|------|------|------|------|:---:|:---:|
| 26 | `macro_btc_nasdaq_corr` | BTC 与纳斯达克高度正相关（>0.6 自 2020）。当相关性脱离正常区间 → 均值回归交易机会 | btc_ret, nasdaq_ret | rolling_corr_30d(btc, nasdaq) | 1d | 低 |
| 27 | `macro_dxy_impact` | 美元指数（DXY）与 BTC 负相关。DXY 连续 5 日上涨 → BTC 短期承压 | dxy_index | -ret_5d(DXY) | 1d | 低 |
| 28 | `macro_btc_etf_flow` | BTC ETF 日净流量是机构情绪的实时代理。持续净流入 → 机构看涨 | btc_etf_net_flow | z-score(flow_5d) | 1d | 低 |
| 29 | `macro_fed_net_liquidity` | 美联储净流动性（Fed Balance Sheet - TGA - RRP）是 BTC 中长期最重要的宏观驱动。流动性扩张 → BTC 上涨 | fed_balance_sheet, tga, rrp | Δlog(net_liquidity_30d) | 1d | 中 |
| 30 | `macro_global_crypto_market_cap_trend` | 总加密市值趋势（排除了 BTC 自身）是市场的 Beta。总市值上升趋势中，多策略胜率更高 | total_market_cap | ret_30d / realized_vol_30d | 1d | 低 |

### 2.6 因子优先级排序

按 **预期有效性 × 数据可得性 × 实现难度** 的综合评分：

| 排名 | 因子 | 评分 | 原因 |
|:---:|------|:---:|------|
| 🥇 | `perp_funding_rate` | 9.5/10 | Crypto 独有、IC 最高、数据免费、逻辑清晰 |
| 🥈 | `crypto_momentum_7d` | 9.0/10 | 在 Crypto 极强、CCXT 就能取、任何频率都有效 |
| 🥉 | `crypto_short_term_reversal` | 8.5/10 | 散户情绪驱动的反转是高 Sharpe 的来源 |
| 4 | `perp_open_interest_change` | 8.5/10 | 量-价-仓三维信号，CCXT 免费获取 |
| 5 | `onchain_exchange_netflow` | 8.0/10 | 最及时的链上信号，但需要 Glassnode/CryptoQuant |
| 6 | `alt_btc_dominance_change` | 8.0/10 | 极简因子、免费、轮动信号强 |
| 7 | `sentiment_fear_greed_index` | 7.5/10 | 免费 API，简单有效 |
| 8 | `macro_btc_etf_flow` | 7.5/10 | 2024 后新变量，数据免费 |

---

## 三、策略框架建议

### 3.1 策略一：跨币种截面动量（Cross-Sectional Momentum）⭐⭐⭐⭐⭐

**逻辑描述**：
每周计算所有币种的过去 7 日收益率，做多前 20%、做空后 20%（或纯多前 10%）。Crypto 中截面动量的超额收益远超传统市场（年化 30–60% 在 Top 20 币种），因为散户追涨杀跌、机构调仓慢。

**因子组合**：
- 主信号：`crypto_momentum_7d`（截面排名）
- 过滤条件：`perp_funding_rate`（排除费率高危币种）、`crypto_amihud_illiquidity`（排除流动性差币种）
- 增强信号：`onchain_whale_accumulation`（鲸鱼增持的币额外加分）

**持仓周期**：7 日（每周调仓），约 52 次/年

**风险特征**：
- Sharpe 目标：1.5–3.0（Crypto 下合理）
- MaxDD：-25% ~ -40%（极端山寨季回调）
- 资金费率拖累：做多高费率币种每年 ~5-15% 的费率成本需考虑

**实现思路**：
```
1. 每周日 UTC 00:00 计算所有币种 ret_7d 截面排名
2. 过滤：市值 > $100M、日成交 > $10M、资金费率 < 0.05%/8h
3. 做多排名前 20%（或前 10 只），等权
4. 7 日后调仓，全量更换
5. 可选：叠加 OI 变化确认信号质量
```

### 3.2 策略二：资金费率套利 + 反转（Funding Rate Mean-Reversion）⭐⭐⭐⭐⭐

**逻辑描述**：
这是 Crypto 市场最经典的策略。当资金费率极端为正（> 0.1%/8h）→ 多头拥挤 → 预期价格回调。策略做空高费率币种 + 做多低费率币种，赚取**费率收入** + **价格均值回归**双重收益。

**因子组合**：
- 主信号：`perp_funding_rate`（截面极端值）
- 确认信号：`perp_open_interest_change`（OI 不再增长 → 拥挤可能解除）
- 出场信号：费率回归正常区间或价格突破止损

**持仓周期**：8 小时–3 天（费率周期驱动的短周期）

**风险特征**：
- Sharpe 目标：2.0–4.0（Crypto 最优策略之一）
- 尾部风险：极端行情下费率可能持续高企数周（如 2021 年牛市），导致空头被挤压
- 爆仓风险：裸空高费率币种在牛市中有 gamma 风险 → 必须严格止损

**实现思路**：
```
1. 每 8 小时计算所有币种的资金费率和其 z-score
2. 做空 z-score > 2.0 的币种（多头极度拥挤）
3. 做多 z-score < -2.0 的币种（空头极度拥挤）
4. 止损：如果价格反向移动超过 2σ → 平仓
5. 止盈：费率回归均值（|z-score| < 0.5）或持有 3 个费率周期后平仓
6. 收益 = 价格变动 + 收取的资金费率 × 持仓时间
```

### 3.3 策略三：BTC 主导的宏观轮动（BTC Dominance Rotation）⭐⭐⭐⭐

**逻辑描述**：
Crypto 市场存在明确的「BTC → ETH → Large Cap → Mid Cap → Meme」资金轮动节奏。用 BTC 市值占比（BTC.D）识别轮动阶段：BTC.D 上升 = 防御期（持有 BTC）、BTC.D 下降 = 风险偏好期（换仓山寨币）。

**因子组合**：
- 主信号：`alt_btc_dominance_change`（BTC.D 变化方向）
- 风险信号：`macro_btc_nasdaq_corr` + `macro_dxy_impact`
- 执行信号：`crypto_momentum_cross_section`（在风险偏好期选强势山寨币）

**持仓周期**：BTC.D 周期为 30–90 天，持仓期较长

**风险特征**：
- Sharpe 目标：1.0–2.0（轮动策略的 Sharpe 通常低于纯 Alpha）
- 优势：降低组合整体回撤（防御期持有 BTC 天然抗跌）
- 劣势：轮动信号有滞后性（BTC.D 通常趋势性变化）

**实现思路**：
```
1. 计算 BTC.D 的 14 日 EMA 趋势方向
2. BTC.D 上升：100% 仓位配 BTC（或 BTC+ETH）
3. BTC.D 下降：做多截面动量 Top 15 山寨币（排除 BTC/ETH）
4. 过渡期（BTC.D 震荡）：50% BTC + 50% 山寨币
5. 调仓频率：每周评估，每 2 周调仓
```

---

## 四、回测方案

### 4.1 回测设计

| 维度 | 建议 | 理由 |
|------|------|------|
| **数据频率** | 1h OHLCV（主）+ 1d OHLCV（长期趋势验证） | 1h 捕获日内微观结构；1d 验证宏观逻辑 |
| **回测区间** | 2020-01 ~ 至今（5+年） | 涵盖完整牛熊周期（2020-2021牛市 → 2022熊市 → 2023-2024复苏 → 2024-2025 ETF牛） |
| **样本外测试** | 2024-01 ~ 至今（ETF 后新范式） | BTC ETF 后的市场结构有根本变化，需独立验证 |
| **币种池** | 动态 Top 50-100（按市值），每月更新 | 避免幸存者偏差（已归零的山寨币也需计入） |
| **基准** | BTC 持有（buy & hold BTC）+ 等权 Top 20 持有 | BTC 基准 = 策略的 Alpha 来源是否超越被动持有 |
| **成本模型** | Taker 6bps + Slippage（市值加权：大币 2bps / 中币 10bps / 小币 30bps） | Crypto 的滑点远低于传统市场但山寨币不可忽略 |

### 4.2 关键注意事项

| 注意事项 | 具体做法 |
|---------|---------|
| **幸存者偏差** | 使用历史每个时点的 Top N 币种，包含已归零/下架的币。数据源：CoinGecko 历史快照 |
| **前视偏差** | 不能用「现在」的市值排名来回测 2020 年的选币。需要每个时间点的截面排名 |
| **停币/下架处理** | 如果某币在回测中被下架，假设以当时市价卖出扣除 50bps 流动性惩罚 |
| **稳定币处理** | 从币种池中排除 USDT/USDC/DAI 等稳定币（或单独策略交易） |
| **插针处理** | 对单日涨跌幅 > 5σ 的数据做 winsorize（截尾到 5σ），避免极端值扭曲统计 |
| **多交易所差异** | 不同交易所的价格差可达 0.1-1%。选一个主交易所（如 Binance）做回测，实盘跨交易所执行时计入价差 |

### 4.3 Crypto 特有回测陷阱

| 陷阱 | 影响 | 解决方案 |
|------|------|---------|
| **资金费率复利** | 忽略资金费率 → 多策略高估 5-20% 年化收益 | 回测中明确计算每 8h 的费率收支 |
| **牛市偏差** | 仅回测 2020-2021-2023-2024 牛 → 高估 Sharpe | 必须包含 2022 年（BTC -65%）和 2018 年（-73%） |
| **新币效应** | 新上币短期暴涨（+200%~+1000%）→ 回测过度拟合 | 排除上市不足 30 天的币种 |
| **交易所数据不一致** | Binance vs OKX 的 K 线在插针处差异大 | 使用统一数据源（推荐 Binance）或取多家交易所中位数 |
| **流动性假象** | Orderbook 深度可能在极端行情瞬间消失 | 回测中增加「流动性冲击」模拟：超过日均交易量 1% 的订单加额外 20bps 滑点 |
| **稳定币收益率** | 闲置资金在 Crypto 中可赚 5-15% APY（staking/lending） | 在回测的现金部分计入稳定币收益（保守用 5% APY） |

---

## 五、Paper Trading 优先级建议

### 5.1 落地路线图

```
Phase 1 (2周): 资金费率套利 Paper Trading ← 最先做
  ├─ 数据：CCXT + funding_rate（免费、已就绪）
  ├─ 逻辑：极端费率 → 反向单边 + 止盈止损
  ├─ 币种：Top 20 永续合约
  ├─ 为什么先做：最低风险、最高 Sharpe、逻辑最独立
  └─ 验收：1 个月纸交，Sharpe > 2.0

Phase 2 (2周): 截面动量 Paper Trading
  ├─ 数据：CCXT OHLCV（免费、已就绪）
  ├─ 逻辑：每周选 Top 10 动量币种纯多
  ├─ 币种：Top 50（市值过滤）
  └─ 验收：1 个月纸交，Sharpe > 1.5

Phase 3 (3周): 链上因子增强
  ├─ 数据：Glassnode/CryptoQuant API
  ├─ 增强：将资金费率 + 动量 + 链上净流量融合
  └─ 验收：3 个月纸交，综合策略 Sharpe > 2.5

Phase 4 (4周): 实盘小资金验证
  ├─ 初始资金：$5,000-10,000
  ├─ 交易量：每笔 $100-500（控制滑点）
  ├─ 交易所：Binance 为主
  └─ 目标：复现纸交 Sharpe 的 60-80%
```

### 5.2 策略优先级对比

| 策略 | 预期 Sharpe | 预期 MaxDD | 数据就绪度 | 逻辑复杂度 | 推荐优先级 |
|------|:---:|:---:|:---:|:---:|:---:|
| 资金费率套利 | 2.5–4.0 | -10%~-20% | ✅ 100% | 低 | **🥇 #1** |
| 截面动量 | 1.5–3.0 | -25%~-40% | ✅ 100% | 低 | **🥈 #2** |
| BTC 轮动 | 1.0–2.0 | -20%~-35% | ✅ 100% | 低 | **🥉 #3** |
| 链上增强综合 | 3.0–5.0 | -15%~-30% | 🟡 50% | 中 | #4 |

### 5.3 资金费率套利 → 最优先的原因

1. **数据零成本**：CCXT 一行 `fetch_funding_rate()` 就能拿到全币种历史费率
2. **逻辑白盒**：费率×价格的双因子结构完全可解释，不存在黑盒风险
3. **回测验证快**：8h 频率 → 1 年就有 ~1095 个独立样本
4. **与现有系统兼容**：PaperTrader 只需新增 funding_rate 数据源
5. **风险可控**：止损严格 + 做空用合约（自带杠杆）→ 单笔最大亏损可控在 2% 以内

---

## 六、因子与策略映射总图

```
                      因子层
    ┌──────────────────┼──────────────────┐
    │                  │                  │
  量价因子          衍生品因子          链上因子
 (7个因子)         (6个因子)          (7个因子)
    │                  │                  │
    ├─ 截面动量 ───────┤                  │
    │                  │                  │
    │            ┌─────┴─────┐            │
    │            │ 资金费率   │            │
    │            │  OI 变化   │            │
    │            │  多空比    │            │
    │            └─────┬─────┘            │
    │                  │                  │
    ▼                  ▼                  ▼
┌──────────┐   ┌──────────────┐   ┌──────────────┐
│ 截面动量  │   │ 费率反转套利  │   │ 宏观轮动     │
│ 策略      │   │ 策略         │   │ 策略          │
└──────────┘   └──────────────┘   └──────────────┘
    │                  │                  │
    └──────────────────┼──────────────────┘
                       ▼
              综合多因子策略
         (因子得分加权 + 风险平价)
```

---

**本方案文档仅供 Crypto 量化策略研究使用。因子实现和回测验证请交由工程开发 Agent 执行。**
