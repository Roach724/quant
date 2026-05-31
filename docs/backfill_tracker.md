# BQ 数据加载进度追踪

> 最后更新: 2026-05-31 01:15 UTC  
> 数据来源: BigQuery 实时查询 + BQ loader 日志

---

## 📊 BQ 表行数

| 表 | 当前行数 | 上次记录 | 增量 | 状态 |
|----|---------|---------|------|------|
| us_bars_1d | 372,723 | 372,723 | — | ✅ 稳定 |
| **us_bars_5m** | **365,470** | 122,898 | **+242,572** | 🟢 大幅增长 |
| **hk_bars_1d** | **10,342** | 2,211 | **+8,131** | 🟢 增长中 |
| hk_bars_5m | 18,950 | 18,950 | — | ✅ 稳定 |
| crypto_bars_1d | 10 | — | — | ⚠️ cron 失败 |
| crypto_bars_5m | 530 | — | — | ⚠️ cron 失败 |

---

## 🔄 BQ Loader 运行日志

### US
| Job | 上次运行 | 结果 | 备注 |
|-----|---------|------|------|
| us_1d | 5/30 02:29 UTC | ✅ OK | 2342 days loaded |
| us_5m | 5/30 20:18 UTC | ✅ OK | 30 days (chain run) |
| us_5m v2 | 5/30 16:16 | ✅ OK | 30 days |

### HK
| Job | 上次运行 | 结果 | 备注 |
|-----|---------|------|------|
| hk_1d | 5/30 02:54 UTC | ✅ OK | 2342 days |
| hk_1d v2 | 5/30 16:31 | ⚠️ 部分失败 | 纳秒时间戳仍报错 |
| hk_1d v3 | 5/31 01:07 | 🔄 进行中 | pandas fallback |
| hk_5m | 5/30 16:17 | ✅ OK | 30 days |
| hk_5m v2 | 5/30 16:57 | ✅ OK | 30 days |

### Crypto
| Job | 上次运行 | 结果 | 备注 |
|-----|---------|------|------|
| crypto_1d | 5/31 01:00 | ❌ FAILED | ModuleNotFoundError |
| crypto_5m | 5/30 06:00 | — | 日志丢失 |

---

## 🚨 已知问题

### 1. Crypto BQ Loader cron 失败
**时间:** 5/31 01:00 UTC  
**错误:** `ModuleNotFoundError: No module named 'bigquery_loader'`  
**原因:** T15 修复改用 cron_wrapper.sh，但丢失了原来的 `cd /opt/quant &&` 前缀。Python 找不到 bigquery_loader 模块。  
**影响:** crypto_1d + crypto_5m 两条 cron  
**修复:** cron 条目加回 `cd /opt/quant &&` 或在 PYTHONPATH 中加 `/opt/quant`

### 2. HK 1d 纳秒时间戳残留
**影响:** 部分旧 HK 日线数据仍用纳秒精度  
**缓解:** v3 pandas fallback 进行中

---

## 📈 趋势

- **us_bars_5m**: 122K → 365K，ws_collector + BQ loader 效果显著
- **hk_bars_1d**: 2.2K → 10.3K，v3 pandas fallback 推进中
- **us_bars_1d / hk_bars_5m**: 稳定

---

## ⏭️ 下一步

1. 修复 crypto cron 的 PYTHONPATH 问题
2. 确认 hk_1d v3 加载完成
3. us_bars_5m 达标后启动 W3
