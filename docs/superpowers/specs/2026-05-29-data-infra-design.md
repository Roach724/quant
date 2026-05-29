# 数据基建完善方案

> **设计日期**: 2026-05-29  
> **状态**: 待评审  
> **背景**: 项目已从 Cloud Run 迁移至本地 VM (quant-vm)，OpenD 已部署，crontab 数据采集已上线但存在稳定性问题。目标是将数据基建提升至实盘级，补齐实时推送和历史回填。

---

## 一、现状与缺口

### 当前数据流

```
OpenD (futu_stock_adapter) / Binance (crypto_binance_adapter)
    ↓  cron 触发 (collectors/main.py)
GCS (freq=5m/1d, Hive 分区)
    ↓  cron 触发 (bigquery_loader/main.py)
BigQuery (6 表)
    ↓  on-demand
Go Query API (systemd, :8080) ← Python SDK
```

### 缺口

| # | 缺口 | 影响 | 严重度 |
|---|------|------|:---:|
| 1 | **无实时推送** — 只有 cron 轮询，最小间隔 5 分钟 | Live Trading Loop 无数据源 | 🔴 |
| 2 | **无历史数据** — GCS 数据仅从 2026-05-27 开始 | 回测无统计意义 | 🔴 |
| 3 | **无数据源 fallback** — Futu 断了 US/HK 全盲 | 单点故障 | 🟡 |
| 4 | **无质量监控** — quality/main.py 代码存在但未部署 | 数据缺失无感知 | 🟡 |
| 5 | **OpenD 会话过期无感知** — 超时后采集静默失败 | 之前已发生 | 🔴 |

---

## 二、方案目标

实盘级数据基建，两个维度：

- **实时层**：OpenD WebSocket 推送 5m K 线 → GCS
- **历史层**：backfill.py 回填 2020~2026 日线 + 近期 5m

---

## 三、实时层设计

### 3.1 架构

```
OpenD (端口 11111)
  │  SubType.K_5M 订阅
  │  完整 bar 到达时触发 on_bar 回调
  ▼
collectors/ws_collector.py (新增，systemd 守护)
  ├─ 内存 buffer：每根 bar append
  ├─ flush 策略：每 5 分钟 或 buffer ≥ 50 条
  │    └─ 调用 storage.write_bars_to_gcs() 复用现有写入
  ├─ 自动重连：on_disconnect → 指数退避 (1s→2s→4s…→60s cap)
  ├─ OpenD 状态检测：启动时 check 连接，未登录 → 日志告警
  └─ 心跳日志：每 30 分钟输出订阅状态摘要
  ▼
GCS (freq=5m, 路径格式不变)
  ▼
BQ Loader + Go Query API (不改)
```

### 3.2 OpenD 订阅细节

| 项目 | 取值 |
|------|------|
| 订阅类型 | `SubType.K_5M` |
| 回调触发 | 完整 bar 完成时（非实时 tick） |
| HK 时段 | UTC 1:30-8:00，订阅 HK stock pool |
| US 时段 | UTC 13:30-20:00，订阅 US stock pool |
| Crypto | 24/7，订阅 crypto pool |
| 进程数 | 单一进程管理所有订阅，避免多进程抢 OpenD 连接 |

市场时段切换逻辑：根据 `is_market_open()` 判断，收盘后自动退订，开盘前自动订阅，避免无效连接占用 OpenD 配额。

### 3.3 容错设计

| 场景 | 处理 |
|------|------|
| OpenD 断连 | 自动重连，指数退避 (max 60s) |
| OpenD 会话过期 | 日志 CRITICAL 告警 + 每 30 分钟重试 |
| GCS 写入失败 | 重试 3 次 + 落本地 `/home/quant/backup/` 备份 |
| 进程崩溃 | systemd `Restart=always` + `RestartSec=10` |
| 网络闪断 | buffer 在内存保留，恢复后一起 flush |

### 3.4 systemd unit

```
[Unit]
Description=Quant WebSocket 5m K-line Collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/quant/collectors
Environment=PYTHONPATH=/opt/quant/collectors
Environment=GCS_BUCKET=deductive-notch-495015-c2-quant-data
Environment=OPEND_HOST=127.0.0.1
Environment=OPEND_PORT=11111
ExecStart=/usr/bin/python3.12 ws_collector.py
Restart=always
RestartSec=10
StandardOutput=append:/home/quant/logs/ws_collector.log
StandardError=append:/home/quant/logs/ws_collector.log

[Install]
WantedBy=multi-user.target
```

---

## 四、历史层设计

### 4.1 回填策略

不写新代码，`backfill.py` 已完备。分两阶段执行：

**阶段 1：日线全量（优先，快速出回测数据）**

| 市场 | 数据源 | 频率 | 时间范围 | 符号数 | 预计耗时 |
|------|--------|:--:|----------|:-----:|:-------:|
| US | futu_stock | 1d | 2020-01~2026-05 | ~250 | 2-3h |
| HK | futu_stock | 1d | 2020-01~2026-05 | ~475 | 4-6h |
| Crypto | cryptobinance | 1d | 2020-01~2026-05 | 20 | 30min |

**阶段 2：5m 近期（策略调参 + 日内验证）**

| 市场 | 数据源 | 频率 | 时间范围 | 符号数 | 预计耗时 |
|------|--------|:--:|----------|:-----:|:-------:|
| US | futu_stock | 5m | 最近 30 天 | 留前 50 只 | 2-3h |
| Crypto | cryptobinance | 5m | 最近 30 天 | 20 | 1h |

> ⚠️ 注意 Futu 历史 K 线额度限制：300 次/30 天滚动。5m 数据只回填关键 symbol 避免耗尽额度。日线不受此限制（每次请求覆盖多年数据）。

### 4.2 执行方式

```bash
# 日线 (后台运行)
nohup python3.12 backfill.py \
  --start 2020-01-01 --end 2026-05-28 \
  --source futu_stock --all \
  --frequency 1d \
  --gcs-bucket deductive-notch-495015-c2-quant-data \
  --chunk-days 365 \
  > /home/quant/logs/backfill_us_1d.log 2>&1 &
```

---

## 五、加固项

### 5.1 OpenD 会话检测

`ws_collector.py` 启动时调用 `get_global_state()` 检查 OpenD 连接状态。如果返回未连接/未登录，记录 CRITICAL 日志并每 30 分钟重试连接，不给 cron collector 发送假数据。

### 5.2 数据源 fallback（cron collector）

`collectors/main.py` 中的 `get_adapter()` 逻辑扩展：当 `futu_stock` 适配器初始化失败时，自动降级到 yfinance（US）或 akshare（HK 1d），确保 cron 采集不会因为 OpenD 故障而完全中断。

改动点：
```python
# main.py get_adapter() 中：
if source == "futu_stock":
    try:
        adapter = FutuStockAdapter()
        adapter._get_ctx()  # 验证连接
        return adapter
    except Exception:
        logger.warning("Futu unavailable, falling back to yfinance")
        return YFinanceUSAdapter(fallback_adapter=AkshareUSAdapter())
```

### 5.3 质量监控上线

`quality/main.py` 已有完整代码（完整度/新鲜度/合理性检查），只需注册 systemd timer：

```
# /etc/systemd/system/quality-check.timer
[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

### 5.4 status.sh 扩展

`scripts/status.sh` 增加 ws_collector 状态检查，集中查看所有数据服务健康状况。

### 5.5 日志轮转

```
# /etc/logrotate.d/quant
/home/quant/logs/*.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
}
```

---

## 六、最终基建全景

```
        实时层                          历史层
   ┌──────────────┐              ┌──────────────┐
   │ ws_collector │  WebSocket   │  backfill.py │  一次性回填
   │  (systemd)   │  5m K线      │  (手动触发)   │
   └──────┬───────┘              └──────┬───────┘
          │                              │
          ▼                              ▼
   ┌──────────────────────────────────────────┐
   │           GCS (Parquet + JSON)           │
   │  freq=5m / freq=1d                       │
   └──────────────────┬───────────────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
      BQ Loader   Go Query API  Python SDK
      (crontab)    (systemd)   (quant.data)
```

**运行进程清单**：

| 进程 | 管理方式 | 作用 | 状态 |
|------|:-------:|------|:--:|
| OpenD | GUI/AppImage | 行情+交易网关 | ✅ 运行中 |
| query-api | systemd | REST bars API | ✅ 运行中 |
| ws_collector | systemd | 实时 5m K → GCS | 🆕 待实现 |
| collector cron | crontab | 日线 + 定时补充 | ✅ 已配置 |
| bq_loader cron | crontab | GCS → BigQuery | ✅ 已配置 |
| quality check | systemd timer | 每日数据质量校验 | 🆕 待部署 |
| logrotate | logrotate.d | 日志轮转 | 🆕 待配置 |

---

## 七、实施顺序

```
Step 1: OpenD 重新登录（需手机验证码）
    └─ 验证 OpenD 端口 11111 可连接

Step 2: 实现 + 部署 ws_collector.py
    ├─ 编写 ws_collector.py (~200 行)
    ├─ 注册 systemd unit
    ├─ 启动验证：确认 bar 能收到并写入 GCS
    └─ 观察 2 小时，确认无异常

Step 3: 日线回填（后台运行）
    └─ 依次启动 US → HK → Crypto 日线回填

Step 4: 5m 回填（后台运行）
    └─ 依次启动 US 前 50 → Crypto 5m 回填

Step 5: 加固项
    ├─ cron collector fallback 逻辑
    ├─ systemd timer for quality check
    ├─ logrotate 配置
    └─ status.sh 扩展

Step 6: 全链路验证
    └─ 实时数据 + 历史数据 → GCS → BQ → SDK bars() 查询
```

---

## 八、验收标准

| 验收项 | 指标 |
|------|------|
| OpenD 连接 | 7×24 运行，断连自动恢复 |
| WebSocket 采集 | 5m K 线延迟 ≤ 6 分钟（1 bar），数据完整率 > 99% |
| 日线回填 | US/HK 2020-2026 全覆盖，BQ 可查询 |
| 5m 回填 | US 前 50 + Crypto 20，最近 30 天覆盖 |
| Fallback | OpenD 故障时 US cron 自动切 yfinance |
| 质量监控 | 每日自动运行，缺失/异常告警 |
| 全链路 | `quant.data.bars()` 可查实时 + 历史数据 |
