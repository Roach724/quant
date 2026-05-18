# 实施计划 — Futu OpenAPI 全面集成

> Date: 2026-05-18
> 基于设计文档: docs/plans/2026-05-18-futu-integration-design.md
> 计划状态: ⏸️ 暂停 — 待富途 API 协议签署后继续
> 每个任务 TDD 驱动

---

## 前置条件

- [ ] 富途牛牛 APP 上完成 **Futu API 问卷评估与协议确认**
- [ ] OpenD 能成功登录并保持运行（`FutuOpenD` → 登录成功）
- [ ] 权限确认：港股 LV2 / 美股 Nasdaq Basic / 加密币 LV1
- [ ] 确定富途证券账户已开通

---

## Phase 1: 数据适配器层

### Task 1.1 — FutuStockAdapter（港股 + 美股数据）

**文件**: `collectors/adapters/futu_stock_adapter.py`

| 接口 | 对应 Futu API |
|------|-------------|
| `__init__(host, port)` | `OpenQuoteContext` |
| `fetch_bars(symbols, start, end, freq)` | `request_history_kline` 分页 |
| `fetch_supported_symbols()` | `get_plate_stock` / `get_stock_basicinfo` |
| `market_hours(d)` | `get_market_state` |

**关键设计**:
- 符号格式透传: `HK.00700`, `US.AAPL`
- `OPEND_HOST`, `OPEND_PORT` 从环境变量读取（默认 127.0.0.1:11111）
- 复权类型: `AuType.QFQ`（前复权）
- 频率映射: `{"1m": KLType.K_1M, "5m": ..., "1d": KLType.K_DAY, ...}`
- 2000 只股票不可能一次 pull，用 `max_count=1000` 分页

**日志格式**: `Futu fetch: {symbol} {start}→{end} {chunk}/{total_chunks} rows={n}`

**测试**: `collectors/tests/test_futu_stock_adapter.py`
- `test_symbol_format_pass_through`: `"HK.00700"` 不变
- `test_fetch_supported_symbols`: 返回格式带 HK./US. 前缀
- `test_env_var_host_port`: `OPEND_HOST=192.168.1.1` 时使用该地址

---

### Task 1.2 — CryptoFutuAdapter（加密币数据）

**文件**: `collectors/adapters/crypto_futu_adapter.py`

| 接口 | 对应 Futu API |
|------|-------------|
| `__init__(host, port)` | `OpenQuoteContext` |
| `fetch_bars(symbols, start, end, freq)` | `request_history_kline` 分页 |
| `fetch_supported_symbols()` | 预定义列表（12个主流币对） |
| `market_hours(d)` | 返回 00:00-23:59（7×24） |

**关键设计**:
- 符号转换: `"BTC/USDT"` → `"CC.BTCUSD"`
- 复权类型: `AuType.NONE`（加密币不复权）
- 频率完全支持 1m/5m/15m/30m/1h/1d

**测试**:
- `test_to_futu_code`: `"BTC/USDT"` → `"CC.BTCUSD"`
- `test_market_hours`: 返回 00:00-23:59
- `test_fetch_supported_symbols`: 12 个预定义币对

---

### Task 1.3 — backfill.py 增加 Futu 数据源

**文件**: `collectors/backfill.py`

**改动**:
1. `--source` choices 增加 `futu_stock` 和 `futu_crypto`
2. 支持的环境变量: `OPEND_HOST`, `OPEND_PORT`
3. 路由到对应的适配器:

```python
elif source == "futu_stock":
    from adapters.futu_stock_adapter import FutuStockAdapter
    adapter = FutuStockAdapter(host=opend_host, port=opend_port)
    market = "STOCK"
elif source == "futu_crypto":
    from adapters.crypto_futu_adapter import CryptoFutuAdapter
    adapter = CryptoFutuAdapter(host=opend_host, port=opend_port)
    market = "CRYPTO"
```

4. `--all` 自动发现适配器支持的 symbols

**测试**: 手动验证（需 OpenD 运行）:
```bash
python3 collectors/backfill.py \
    --source futu_stock \
    --start 2026-05-01 --end 2026-05-18 \
    --symbols HK.00700,US.AAPL \
    --local-dir /tmp/futu_test
```

---

### Task 1.4 — collectors/main.py 增加 Futu 数据源

**文件**: `collectors/main.py`

**改动**:
1. `get_adapter()` 增加 `futu_stock`, `futu_crypto` 分支
2. `get_symbols()` 增加自动发现逻辑
3. 环境变量: `COLLECTOR_SOURCE=futu_stock`, `OPEND_HOST`, `OPEND_PORT`

---

## Phase 2: Cloud Run 部署

### Task 2.1 — Dockerfile.collector

**文件**: `docker/Dockerfile.collector`

**要点**:
- 多阶段构建: 第一阶段下载 OpenD，第二阶段组装
- `python:3.11-slim` 基础镜像
- `pip3 install futu-api pandas pyarrow --break-system-packages`
- 复制 OpenD 二进制到 `/opt/opend/`
- `CMD` 指向 `start_collect.sh`

**宿主机架构**: 使用 `--platform=linux/amd64`（OpenD 是 x86_64 二进制）

### Task 2.2 — start_collect.sh

**文件**: `docker/start_collect.sh`

**启动逻辑**:
```
1. 后台启动 OpenD (FutuOpenD -cfg_file=/opt/opend/FutuOpenD.xml &)
2. 轮询 get_global_state 判断登录状态（最多 30 秒）
3. 登录成功 → 运行 python3 -m collectors.main
4. 采集完成 → kill OpenD
5. 异常 → kill OpenD，exit 1
```

### Task 2.3 — FutuOpenD.xml（模板）

**文件**: `docker/FutuOpenD.xml`（模板，不包含真实密码）

**模板内容**:
```xml
<futu_opend>
    <ip>127.0.0.1</ip>
    <api_port>11111</api_port>
    <login_account>__LOGIN_ACCOUNT__</login_account>
    <login_pwd_md5>__LOGIN_PWD_MD5__</login_pwd_md5>
    <lang>chs</lang>
    <log_level>no</log_level>
    <!-- Cloud Run 环境下禁止日志文件写入 -->
</futu_opend>
```

**.gitignore** 增加 `FutuOpenD.xml`（含密码），确保不提交。

### Task 2.4 — Terraform Job 配置

**文件**: 
- `terraform/collector-futu-stock.tf`
- `terraform/collector-futu-crypto.tf`
- `terraform/collector-binance-crypto.tf`（保留现有）

**配置模板**:
```hcl
resource "google_cloud_run_job" "collector_futu_stock" {
  name     = "collector-futu-stock"
  location = "asia-east1"

  template {
    spec {
      template {
        spec {
          containers {
            image = "gcr.io/${var.project_id}/collector:latest"
            env {
              name  = "COLLECTOR_SOURCE"
              value = "futu_stock"
            }
            env {
              name  = "GCS_BUCKET"
              value = var.gcs_bucket
            }
            env {
              name  = "OPEND_HOST"
              value = "127.0.0.1"
            }
            env {
              name  = "OPEND_PORT"
              value = "11111"
            }
          }
        }
      }
    }
  }
}
```

**调度计划**:
| Job | cron 表达式 | 说明 |
|-----|------------|------|
| collector-futu-stock | `*/30 9-16 * * 1-5` | 盘中每 30 分钟 |
| collector-futu-crypto | `*/5 * * * *` | 每 5 分钟（7×24） |
| collector-binance-crypto | `*/5 * * * *` | 与上并行（备选） |

---

## Phase 3: 交易 Broker 层

### Task 3.1 — FutuStockBroker

**文件**: `oms/broker/futu_stock_broker.py`

**核心 API**:
| 方法 | Futu API |
|------|----------|
| `submit_order()` | `place_order` |
| `cancel_order()` | `modify_order(ModifyOrderOp.NORMAL)` |
| `get_order()` | `order_list_query` |
| `get_open_orders()` | `order_list_query(status_filter=SUBMITTED)` |
| `get_positions()` | `position_list_query` |
| `get_account()` | `accinfo_query` |

**关键设计**:
- trd_env: `TrdEnv.SIMULATE`（默认），`TrdEnv.REAL`（需解锁）
- 下单限频: 15 次/30 秒（Futu 侧限制）
- 限价单/市价单都支持

### Task 3.2 — CryptoFutuBroker

**文件**: `oms/broker/crypto_futu_broker.py`

**与 StockBroker 差异**:
- 使用 `OpenCryptoTradeContext` 而非 `OpenSecTradeContext`
- 仅 `TrdEnv.REAL`（不支持模拟）
- 符号带 `CC.` 前缀
- `_resolve_acc_id()` 自动发现加密货币账户
- 不支持 `modify_order`，只能 `cancel`

### Task 3.3 — RouterOrderManager

**文件**: `oms/manager.py`

**路由表**:
```python
BROKER_ROUTES = [
    (lambda sym: "/" in sym,       "futu_crypto"),  # "BTC/USDT"
    (lambda sym: sym.startswith("HK."), "futu_stock"),   # "HK.00700"
    (lambda sym: sym.startswith("US."), "futu_stock"),   # "US.AAPL"
    (lambda sym: sym.startswith("CC."),  "futu_crypto"), # "CC.BTCUSD"
    (lambda sym: sym.startswith("CRYPTO_"), "binance"),   # fallback
]
```

---

## Phase 4: 回测验证

### Task 4.1 — 用真实数据验证回测链路

步骤:
1. 本地启动 OpenD
2. 通过 `CryptoFutuAdapter` 拉取 BTC/USDT 日 K 数据
3. 通过 `FutuStockAdapter` 拉取 HK.00700 日 K 数据
4. 喂给 Engine + FactorBuilder + ModelTrainer
5. 验证收益曲线有波动（至少回测能跑）

### Task 4.2 — 验证模拟交易

步骤:
1. `FutuStockBroker(trd_env=SIMULATE)` 下单 HK.00700
2. 查询订单状态
3. 查询持仓
4. 查询账户
5. 撤单

---

## 时间估计

| Phase | 任务 | 估计时间 |
|-------|------|---------|
| 1.1 | FutuStockAdapter | 20 min |
| 1.2 | CryptoFutuAdapter | 15 min |
| 1.3 | backfill.py 适配 | 10 min |
| 1.4 | main.py 适配 | 5 min |
| 2.1-2.4 | Cloud Run 部署 | 30 min |
| 3.1 | FutuStockBroker | 20 min |
| 3.2 | CryptoFutuBroker | 15 min |
| 3.3 | RouterOrderManager | 10 min |
| 4.1-4.2 | 验证 | 20 min |
| **总计** | | **~2.5 小时** |

---

## 旧文件清理

执行前清理之前的草稿文件：
```
git rm docs/plans/2026-05-18-futu-crypto-design.md
git rm docs/plans/2026-05-18-futu-crypto-plan.md
```
