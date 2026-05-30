# 历史数据回填追踪

> 最后更新: 2026-05-30 06:52 UTC

## 回填任务清单

### 日线数据 (1d) — 2020-01-01 → 2026-05-28

| # | 品种 | 标的 | 状态 | 行数 | GCS 路径 | BQ |
|---|------|------|------|------|----------|----|
| 1 | 🇺🇸 美股 | 234只 | 🔄 运行中 (3/7 chunk) | ~373K 预计 | `raw/us/bars/freq=1d/` | ⬜ |
| 2 | 🇭🇰 港股 | 15只 | ⏳ 待触发 (16:00 UTC) | ~23K 预计 | `raw/hk/bars/freq=1d/` | ⬜ |

### 5分钟数据 (5m) — 近30天

| # | 品种 | 标的 | 状态 | 备注 |
|---|------|------|------|------|
| 3 | 🇺🇸 美股 | 234只 | ⏳ 待触发 | at-job chain 步骤 2 |
| 4 | 🇭🇰 港股 | 15只 | ⏳ 待触发 | at-job chain 步骤 3 |

---

## 执行计划

```
现在 06:52 UTC
  │
  ├── 🔄 US 1d 回填 (PID 125505) — chunk 3/7, ETA ~14:00 UTC
  │
  └── ⏰ at-job: 16:00 UTC (北京时间 00:00)
        │
        ├── [1/4] HK 1d 回填    (~30min, 15只×7 chunk)
        ├── [2/4] US 5m 回填    (~15min, 234只×5 chunk)
        ├── [3/4] HK 5m 回填    (~2min,  15只×5 chunk)
        └── [4/4] BQ Loader ×4  (~10min)
              ├── us_bars_1d (2500天, 2020-01-01起)
              ├── us_bars_5m (30天)
              ├── hk_bars_1d (2500天, 2020-01-01起)
              └── hk_bars_5m (30天)
```

### 串行原因

Futu API 限制 60 请求/30秒。US backfill 本身 ~1.67 req/s。并行会导致超限。串行安全。

---

## 基础设施状态

| 组件 | 状态 | 备注 |
|------|------|------|
| ws_collector (5m 实时) | ✅ 运行中 | US 234 + HK 15 + Crypto 10 |
| main.py 1d cron ×2 | ✅ 已部署 | US 21:30 / HK 8:30 UTC |
| BQ Loader cron ×6 | ✅ 已部署 | US/HK/Crypto × 5m+1d |
| Quality Check timer | ✅ 已激活 | 每日 06:30 UTC |

---

## 今日代码改动

| Commit | 改动 |
|--------|------|
| `a1d8c84` | fix: BQ loader dedup preserves table partitioning and clustering |
| `603fa1e` | add backfill chain script |
| `4a762db` | fix: backfill GCS path bug + ws_collector DataFrame alignment |

---

## 已知问题

| 问题 | 状态 |
|------|------|
| BQ loader dedup 丢失分区属性 | ✅ 已修复 |
| HK 1d 回填数据丢失 (写错路径) | 🔄 待重跑 |
| US 1d 回填 BQ loader 过早执行 (全 404) | 🔄 回填完再 load |
| `raw/mixed/` 路径废弃 | ✅ 不再使用 |

## 待确认

| # | 任务 | 优先级 |
|---|------|--------|
| 1 | 验证 at-job 执行结果 (明早检查) | 🔴 |
| 2 | BQ 数据量确认 (US ~373K / HK ~23K) | 🔴 |
| 3 | cron BQ loader 首次正确跑完验证 | 🟡 |
