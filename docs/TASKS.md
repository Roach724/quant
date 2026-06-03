# 采集任务清单 (TASKS.md)

> 最后更新: 2026-06-03  
> 标的: US 234 / HK 270 (来自 `config/symbols.yaml`)

---

## 🔄 实时采集 (systemd daemon)

| # | 任务 | 服务 | 数据 | 频率 | 写入 | 覆盖 |
|---|------|------|------|------|------|------|
| 1 | HK/US 5m K线 | `ws-collector.service` | `{market}_bars_5m` | 每 5min flush | BQ 直写 | 504 只 |

---

## 📊 日线聚合 (cron)

| # | 任务 | 时间 (UTC) | 命令 | 覆盖 |
|---|------|-----------|------|------|
| 2 | `compute_hk_1d` | 08:30 Mon-Fri | `scripts/compute_daily_bars.py --market hk` | HK 270 |
| 3 | `compute_us_1d` | 21:30 Mon-Fri | `scripts/compute_daily_bars.py --market us` | US 234 |

---

## 📈 因子采集 (cron — 直接写 BQ)

### 每日 (Mon-Fri)

| # | 任务 | 时间 (UTC) | BQ 表 | 市场 |
|---|------|-----------|-------|------|
| 4 | `collect_hk_brokers` | 08:15 | `hk_top_ten_brokers` | HK |
| 5 | `collect_hk_capital_dist` | 08:20 | `hk_capital_distribution` | HK |
| 6 | `collect_hk_short_vol` | 08:25 | `hk_daily_short_volume` | HK |
| 7 | `collect_us_capital_dist` | 21:30 | `us_capital_distribution` | US |
| 8 | `collect_us_short_vol` | 21:35 | `us_daily_short_volume` | US |

### 每周×2 (Mon + Thu)

| # | 任务 | 时间 (UTC) | BQ 表 | 市场 |
|---|------|-----------|-------|------|
| 9 | `collect_us_insider_trade` | 04:00 | `us_insider_trade` | US |
| 10 | `collect_us_insider_holder` | 04:30 | `us_insider_holder` | US |

### 每周 (Mon)

| # | 任务 | 时间 (UTC) | BQ 表 | 市场 |
|---|------|-----------|-------|------|
| 11 | `collect_us_earnings_move` | 05:00 | `us_earnings_price_move` | US |
| 12 | `collect_hk_earnings_move` | 05:00 | `hk_earnings_price_move` | HK |
| 13 | `collect_us_earnings_hist` | 05:30 | `us_earnings_price_history` | US |
| 14 | `collect_hk_earnings_hist` | 05:30 | `hk_earnings_price_history` | HK |
| 15 | `collect_us_rehab` | 06:00 | `us_rehab` | US |
| 16 | `collect_hk_rehab` | 06:00 | `hk_rehab` | HK |

### 每月 (1号)

| # | 任务 | 时间 (UTC) | BQ 表 | 市场 |
|---|------|-----------|-------|------|
| 17 | `collect_us_owner_plate` | 06:00 | `us_owner_plate` | US |
| 18 | `collect_hk_owner_plate` | 06:00 | `hk_owner_plate` | HK |

### Phase 2 因子 (Mon/Thu)

| # | 任务 | 时间 (UTC) | BQ 表 | 市场 | 频率 |
|---|------|-----------|-------|------|------|
| 19 | `collect_us_morningstar` | 05:00 Mon | `us_morningstar` | US | 每周 |
| 20 | `collect_hk_morningstar` | 05:00 Mon | `hk_morningstar` | HK | 每周 |
| 21 | `collect_us_rating_summary` | 03:30 Mon/Thu | `us_rating_summary` | US | 每周×2 |
| 22 | `collect_us_stock_screen` | 06:00 Mon | `us_stock_screen` | US | 每周 |
| 23 | `collect_hk_stock_screen` | 06:00 Mon | `hk_stock_screen` | HK | 每周 |

---

## 📋 F10 采集 (cron — 直接写 BQ)

| # | 任务 | 时间 (UTC) | BQ 表 | 频率 |
|---|------|-----------|-------|------|
| 24 | `f10_collector_valuation` | 00:15 | `us_valuation` | 每日 |
| 25 | `f10_collector_short` | 00:25 | `us_short_interest` | 每日 |
| 26 | `f10_collector_flow` | 00:40 | `us_capital_flow` | 每日 |
| 27 | `f10_collector_fin` | 23:45 Sun | `us_financials` | 每周 |
| 28 | `f10_collector_analyst` | 23:55 Sun | `us_analyst` | 每周 |
| 29 | `f10_collector_shrhldr` | 00:10 Mon | `us_shareholder` | 每周 |

---

## ✅ 质量检查 (cron — 只读 BQ)

| # | 任务 | 时间 (UTC) | 检查内容 | 频率 |
|---|------|-----------|---------|------|
| 30 | `quality_us_5m` | 20:30 | US 5m 完整性/新鲜度 | Mon-Fri |
| 31 | `quality_us_1d` | 06:30 | US 1d 完整性/新鲜度 | Mon-Fri |
| 32 | `quality_hk_5m` | 08:30 | HK 5m 完整性/新鲜度 | Mon-Fri |
| 33 | `quality_hk_1d` | 10:00 | HK 1d 完整性/新鲜度 | Mon-Fri |

---

## 💾 备份 (cron)

| # | 任务 | 时间 (UTC) | 内容 |
|---|------|-----------|------|
| 34 | `backup_bq_gcs` | 06:00 每日 | BQ → GCS Parquet 归档 |

---

## 架构说明

```
实时层:   ws_collector (WebSocket) ──→ BQ 直写
日线层:   compute_daily_bars.py     ──→ BQ 聚合 (5m→1d)
因子层:   collect_futu_factors.py   ──→ BQ 直写
F10 层:   fundamental_collector.py  ──→ BQ 直写
质量层:   quality/main.py           ←── BQ 只读
备份层:   backup_bq_to_gcs.sh       ←── BQ → GCS
```

## 已删除任务

| 任务 | 原因 | 删除日期 |
|------|------|---------|
| `collector_hk_1d` | 5m→1d 聚合替代 | 2026-06-03 |
| `collector_us_1d` | 5m→1d 聚合替代 | 2026-06-03 |
| 34 条 BQ loader cron | BQ 直写替代 GCS 管道 | 2026-06-03 |

## 关键文件

| 文件 | 职责 |
|------|------|
| `collectors/ws_collector.py` | 5m K线 daemon |
| `collectors/collect_futu_factors.py` | 因子采集入口 |
| `collectors/fundamental_collector.py` | F10 采集入口 |
| `common/bq_writer.py` | BQ 直写封装 |
| `common/logging_util.py` | JSON 日志 |
| `scripts/compute_daily_bars.py` | 5m→1d 聚合 |
| `scripts/cron_wrapper.sh` | Cron 日志路由 |
| `scripts/backup_bq_to_gcs.sh` | BQ → GCS 备份 |
| `config/symbols.yaml` | 标的 SSOT |
