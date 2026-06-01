# 项目进度追踪

> 最后更新: 2026-06-01 06:56 UTC  
> 更新原因: us_tech v2 注册 + Exp1 重启

---

## 📊 BQ 表行数

| 表 | 行数 | 最新时间戳 | 状态 |
|----|------|-----------|------|
| us_bars_1d | 372,723 | 2026-05-28 | 🟡 May 29 待 06:00 入库 |
| us_bars_5m | 365,470 | 2026-05-29 16:00 | 🟡 待开盘 |
| hk_bars_1d | 18,793 | 2026-05-28 | 🟢 增长中 |
| hk_bars_5m | 19,231 | 2026-06-01 12:00 HKT | 🟢 实时 |
| crypto_bars_1d | - | - | ⏸️ 搁置 |
| crypto_bars_5m | - | - | ⏸️ 搁置 |

---

## 🔄 定时任务状态 (2026-06-01 检查)

### 实时采集
| 任务 | 方式 | 状态 |
|------|------|------|
| ws_collector (US/HK/Crypto 5m) | systemd | 🟢 PID 1046, 261MB, 19h+ |
| OpenD (行情+交易) | AppImage | 🟢 PID 2436 |

### VM Cron (quant 用户)
| 任务 | 频率 | 状态 |
|------|------|------|
| US 1d 采集 | 21:30 UTC Mon-Fri | ✅ 上次 5/29 OK |
| HK 1d 采集 | 08:30 UTC Mon-Fri | ⏸️ 今天待执行 |
| US 5m BQ freq | */5 13-20 UTC | ⏸️ 开盘后启动 |
| US 5m BQ daily | 06:00 UTC Mon-Fri | ⏸️ 今天待执行 |
| US 1d BQ | 06:00 UTC Mon-Fri | ⏸️ 今天待执行 |
| HK 5m BQ freq | */5 1-8 UTC | 🟢 实时运行中 |
| HK 5m BQ daily | 09:30 UTC Mon-Fri | ⏸️ 今天待执行 |
| HK 1d BQ | 09:30 UTC Mon-Fri | ⏸️ 今天待执行 |
| Crypto BQ ×2 | 01:00/06:00 daily | ✅ 正常 |
| F10 Collector ×6 | 00:15-00:55 daily | ✅ 今天全部 OK |
| F10 BQ Loader ×6 | 00:00-01:00 daily | ✅ 今天全部 OK |
| Quality US 5m | 20:30 UTC Mon-Fri | 🆕 |
| Quality US 1d | 06:30 UTC Mon-Fri | 🆕 |
| Quality HK 5m | 08:30 UTC Mon-Fri | 🆕 |
| Quality HK 1d | 10:00 UTC Mon-Fri | 🆕 |

### OpenClaw Cron
| 任务 | 频率 | 状态 |
|------|------|------|
| factor-compute-tech-daily | 06:00 UTC Mon-Fri | 🆕 今天首跑 |
| factor-compute-f10-weekly | 02:00 UTC Sun | 🆕 |
| daily-memory-archive | 00:00 北京时间 | ✅ 上次 OK |

---

## 🏃 进行中实验

### 多日实盘模拟
| 实验 | 策略 | PID | 配置 | 特征 |
|------|------|-----|------|------|
| Exp1 | MLPredStrategy `us_tech` v2 | 188353 | `exp1_ml_us.yaml` | 39 tech |
| Exp2 | SimpleMomentum | 176061 | `exp2_momentum_us.yaml` | — |

**共同:** 240 symbols, $100k, 5 trading days, rebalance_every=13
**状态:** 等待 13:30 UTC 美股开盘

### Model Registry
| 模型 | 版本 | 特征 | RMSE | IC |
|------|------|------|------|----|
| us_tech | v1 (旧) | 13 | 0.1487 | 0.446* |
| **us_tech** | **v2** | **39** | **0.1564** | **0.0435** |

v1 IC 虚高因为 `fwd_ret_20d` 泄漏到了特征里。v2 用 explicit 模式指定全部 39 个 tech 因子。

---

## ✅ 今日完成

- [x] 多日 Live Loop 框架 (calendar + state + multi_day_loop)
- [x] exchange_calendars 节假日识别 (NYSE + HKEX)
- [x] MLPredStrategy 训练链路修复 (4个阻塞问题)
- [x] `us_tech` v2 模型训练 (explicit 39 features, Optuna 20 trials)
- [x] `us_tech` v2 注册到 ModelRegistry + Exp1 重启使用
- [x] runner.py `model_name`/`model_version` 从 config 读取
- [x] Quality Check 自适应交易日数 + ×4 cron 部署
- [x] logging 配置 (live/run.py)
- [x] Paper mode 回测验证 (+42.8%)
- [x] 全天定时任务巡检
- [x] MEMORY.md + tracker 更新
- [x] wechat-sendfile 技能创建

## ⏳ 待做

- [ ] 5天后 Exp1 vs Exp2 效果对比
- [ ] trainer `load_from_bq` 按 source 过滤因子 (避免 F10 混入 tech)
- [ ] ml/__init__.py lazy import (避免 optuna 强制依赖)
- [ ] BQ 表无前缀旧符号清理 (AAPL vs US.AAPL)
- [ ] 加密币模块恢复
