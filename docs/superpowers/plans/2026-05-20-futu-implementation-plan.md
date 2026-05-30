# Futu OpenAPI 集成 — 实施计划

> Date: 2026-05-20
> 依据: `2026-05-18-futu-integration-design.md` · `2026-05-18-futu-crypto-design.md`
> 前置: Phase 0 数据管道修复（freq 分隔 + append-only）需先完成

---

## 一、实施总览

| 阶段 | 内容 | 文件数 | 预估工时 | 产出 |
|------|------|--------|---------|------|
| **P0** | 基础设施 | 4 | 2h | Dockerfile + 启动脚本 + OpenD XML + .gitignore |
| **P1** | Stock 数据适配器 | 2 | 3h | `futu_stock_adapter.py` + 单元测试 |
| **P2** | Crypto 数据适配器 | 2 | 2h | `crypto_futu_adapter.py` + 单元测试 |
| **P3** | Stock 交易 Broker | 2 | 3h | `futu_stock_broker.py` + 单元测试 |
| **P4** | Crypto 交易 Broker | 2 | 3h | `crypto_futu_broker.py` + 单元测试 |
| **P5** | Broker 路由 | 1 | 1h | `oms/broker/__init__.py` 改造 |
| **P6** | 管线集成 | 4 | 2h | `main.py` / `backfill.py` / Terraform |
| **P7** | 集成测试 + 验证 | — | 3h | 端到端验证脚本 |

---

## 二、前置条件

- [ ] Phase 0 已部署（freq 分隔的 GCS 路径 + WRITE_APPEND + dedup 就位）
- [ ] 富途账户已开通（总资产 ≥ 1 万 HKD）
- [ ] `pip install futu-api` 已验证可连接
- [ ] OpenD 二进制已下载（x86_64，`--platform=linux/amd64` 可运行）
- [ ] `quant/docs/Futu-API-Doc-zh-Python.md` 已阅读确认

---

## 三、逐阶段实施

---

### P0：基础设施

#### P0.1 创建 Dockerfile

**文件：** `quant/docker/Dockerfile.collector`

```dockerfile
FROM python:3.11-slim

# 系统依赖（OpenD 需要 libc 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖
RUN pip install --no-cache-dir futu-api==3.8.2 pandas pyarrow

# 复制 OpenD 二进制
ARG OPEND_VERSION=7.1.7707
ADD https://softwarefile.futunn.com/FutuOpenD_${OPEND_VERSION}_linux_x86_64.zip /tmp/opend.zip
RUN unzip /tmp/opend.zip -d /opt/opend/ && rm /tmp/opend.zip

# 启动脚本
COPY start_collect.sh /opt/
RUN chmod +x /opt/start_collect.sh

# 应用代码（由 Cloud Run Build 挂载）
COPY collectors/ /app/collectors/

CMD ["/opt/start_collect.sh"]
```

> **注：** OpenD 版本号以 [futu 官网](https://futunn.com) 实际下载为准。
> 开发阶段可先手动下载，后续 CI 自动下载。

#### P0.2 创建启动脚本

**文件：** `quant/docker/start_collect.sh`

内容见设计文档 §5.3，关键逻辑：
1. 启动 OpenD（后台，接收 stdout/stderr 到 /tmp）
2. 轮询等待登录成功（≤30s）
3. 执行 `python3 -m collectors.main`
4. 关闭 OpenD 后退出

#### P0.3 创建 OpenD 配置模板

**文件：** `quant/docker/FutuOpenD.xml.template`

```xml
<futu_opend>
    <ip>127.0.0.1</ip>
    <api_port>11111</api_port>
    <login_account>${FUTU_LOGIN_ACCOUNT}</login_account>
    <login_pwd_md5>${FUTU_LOGIN_PWD_MD5}</login_pwd_md5>
    <lang>chs</lang>
    <log_level>info</log_level>
    <log_path>/tmp</log_path>
</futu_opend>
```

启动脚本中 `envsubst` 或 `sed` 替换占位符 → `FutuOpenD.xml`。

#### P0.4 更新 .gitignore

```
# Futu
docker/FutuOpenD.xml
*.log
```

#### P0.5 创建 Terraform 模板

**文件：** `quant/terraform/collector-futu-stock.tf`

```hcl
resource "google_cloud_run_job" "collector_futu_stock" {
  name     = "quant-collector-futu-stock"
  location = var.region

  template {
    spec {
      template {
        spec {
          containers {
            image = "gcr.io/${var.project_id}/collector-futu-stock:latest"
            env {
              name  = "COLLECTOR_SOURCE"
              value = "futu_stock"
            }
            env {
              name  = "GCS_BUCKET"
              value = google_storage_bucket.quant_data.name
            }
            env {
              name  = "OPEND_HOST"
              value = "127.0.0.1"
            }
            env {
              name  = "OPEND_PORT"
              value = "11111"
            }
            env {
              name  = "FUTU_LOGIN_ACCOUNT"
              value = var.futu_login_account
            }
            env {
              name  = "FUTU_LOGIN_PWD_MD5"
              value = var.futu_login_pwd_md5
            }
            resources {
              limits = {
                cpu    = "2"
                memory = "4Gi"
              }
            }
          }
        }
      }
    }
  }
}

resource "google_cloud_scheduler_job" "collector_futu_stock" {
  name             = "quant-collector-futu-stock-trigger"
  schedule         = "*/30 1-10 * * 1-5"  # 港股盘中 30min 间隔（UTC）
  time_zone        = "Asia/Hong_Kong"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/quant-collector-futu-stock:run"
    oauth_token {
      service_account_email = google_service_account.cloud_run_sa.email
    }
  }
}
```

---

### P1：FutuStockAdapter（港股 + 美股数据）

#### P1.1 创建适配器

**文件：** `quant/collectors/adapters/futu_stock_adapter.py`

```python
"""Futu OpenD stock market adapter — HK (LV2) + US (LV3) equities."""

import os
import logging
from datetime import date, time, datetime
from typing import Optional

import pandas as pd
from futu import (
    OpenQuoteContext, RET_OK, AuType, KLType,
    Market, SecurityFirm,
)

logger = logging.getLogger(__name__)


class FutuStockAdapter:
    """Futu OpenD stock market adapter for HK + US equities.

    Uses request_history_kline with pagination.
    Supports both HK (LV2) and US (LV3: NasBasic+TotalView+Arcabook).
    """

    market = "MIXED"

    _FREQ_MAP = {
        "1m": KLType.K_1M,
        "5m": KLType.K_5M,
        "15m": KLType.K_15M,
        "30m": KLType.K_30M,
        "1h": KLType.K_60M,
        "1d": KLType.K_DAY,
        "1w": KLType.K_WEEK,
    }

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self._ctx: Optional[OpenQuoteContext] = None

    def _get_ctx(self) -> OpenQuoteContext:
        if self._ctx is None:
            self._ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self._ctx

    def _map_frequency(self, frequency: str):
        mapped = self._FREQ_MAP.get(frequency)
        if mapped is None:
            raise ValueError(f"Unsupported frequency: {frequency}")
        return mapped

    def _determine_autype(self, code: str) -> int:
        """HK stocks use QFQ (forward-adjusted), US uses NONE."""
        if code.startswith("HK."):
            return AuType.QFQ
        return AuType.NONE

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV bars via request_history_kline with pagination.

        Args:
            symbols: ["HK.00700", "HK.09988", "US.AAPL"] format
            start: start datetime
            end: end datetime
            frequency: "1m", "5m", "1d", etc.

        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low,
            close, volume, market
        """
        ctx = self._get_ctx()
        ktype = self._map_frequency(frequency)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        records = []
        for code in symbols:
            autype = self._determine_autype(code)
            page_key = None

            while True:
                ret, data, page_key = ctx.request_history_kline(
                    code, start=start_str, end=end_str,
                    ktype=ktype, autype=autype,
                    max_count=1000, page_req_key=page_key,
                )
                if ret != RET_OK:
                    logger.warning("Futu fetch failed for %s: %s", code, data)
                    break

                for _, row in data.iterrows():
                    records.append({
                        "symbol": code,
                        "timestamp": row["time_key"],
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(float(row["volume"])),
                        "market": "HK" if code.startswith("HK.") else "US",
                    })

                if page_key is None:
                    break

        return pd.DataFrame(records)

    def fetch_supported_symbols(self) -> list[str]:
        """Return known symbols from Futu.

        Uses get_plate_stock for HK stocks.
        US stocks use a predefined watchlist since full NASDAQ listing
        exceeds the 300 subscription quota.

        TODO: Implement get_plate_stock for HK dynamic symbol discovery.
        """
        # Placeholder — will be populated from config/watchlist
        return []

    def market_hours(self, d: date) -> tuple[time, time]:
        """Return trading hours based on market state.

        HK: 09:30-16:00 (with lunch break 12:00-13:00)
        US: 09:30-16:00 ET

        TODO: Use get_market_state for dynamic hours.
        """
        return (time(9, 30), time(16, 0))

    def close(self):
        """Close the OpenD context."""
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
```

#### P1.2 单元测试

**文件：** `quant/collectors/tests/test_futu_stock_adapter.py`

测试要点：
- `_map_frequency` 映射正确（1m → K_1M, 1d → K_DAY）
- `_determine_autype` HK.QFQ / US.NONE
- `_to_futu_code` 不适用（股票符号直接用 `HK.xxx`）
- `fetch_bars` 空 symbols → 空 DataFrame
- `market_hours` 返回正确时间元组
- 非法 freq → `ValueError`

> **注：** 真实 Futu 调用需要 OpenD 进程，测试用 mock。

---

### P2：CryptoFutuAdapter（加密币数据）

#### P2.1 创建适配器

**文件：** `quant/collectors/adapters/crypto_futu_adapter.py`

要点：
- 继承 `MarketAdapter` Protocol（同 base.py 接口）
- 符号转换：`BTC/USDT` → `CC.BTCUSD`
- 使用 `AuType.NONE`（加密货币无复权）
- 支持的币对列表：BTC/USDT, ETH/USDT, SOL/USDT, LTC/USDT, XRP/USDT, DOT/USDT, ADA/USDT, AVAX/USDT, LINK/USDT, UNI/USDT
- `fetch_bars` 实现同 StockAdapter 模式，paginated request_history_kline

#### P2.2 单元测试

**文件：** `quant/collectors/tests/test_crypto_futu_adapter.py`

测试要点：
- `_to_futu_code`：`BTC/USDT` → `CC.BTCUSD`，`ETH/USDT` → `CC.ETHUSD`
- `fetch_supported_symbols` 返回完整列表
- `market_hours` 返回 00:00-23:59
- 非法 freq → ValueError

---

### P3：FutuStockBroker（港股 + 美股交易）

#### P3.1 创建 Broker

**文件：** `quant/oms/broker/futu_stock_broker.py`

实现 `Broker` Protocol 的全部接口：

| 接口 | Futu API | 说明 |
|------|----------|------|
| `submit_order` | `place_order`（OpenSecTradeContext） | 支持限价/市价，默认 SIMULATE |
| `cancel_order` | `modify_order(op=NORMAL)` | 撤单 |
| `get_order` | `order_list_query` | 按 broker_id 过滤 |
| `get_positions` | `position_list_query` | 统一持仓查询 |
| `get_account` | `accinfo_query` | 账户现金/资产/购买力 |
| `get_open_orders` | `order_list_query(filter=SUBMITTED)` | 待成交订单 |

设计文档 §4.1 已有完整代码骨架，实现时需注意：
- 使用 `TrdEnv.SIMULATE` 作为默认（可行模拟环境测试）
- 切换 `TrdEnv.REAL` 时需 OpenD GUI 解锁
- `SecurityFirm` 根据用户券商设置（默认 `FUTUSECURITIES`）

#### P3.2 单元测试

**文件：** `quant/oms/tests/test_futu_stock_broker.py`

- Mock `OpenSecTradeContext`
- 验证下单参数正确传递
- 验证空持仓/空订单的正确处理
- 验证异常 case（Futu RET != OK）

---

### P4：FutuCryptoBroker（加密币交易）

#### P4.1 创建 Broker

**文件：** `quant/oms/broker/crypto_futu_broker.py`

实现 `Broker` Protocol 全部接口，使用 `OpenCryptoTradeContext`。

关键差异（vs StockBroker）：
- ⚠️ **仅支持 `TrdEnv.REAL`**（无模拟环境）
- 需要 `_resolve_acc_id()` 自动发现加密币账户 ID
- 符号格式：内部 `BTC/USDT` → 对外 `CC.BTCUSD`
- 下单限频：15 次/30 秒

设计文档 §4.2（crypto doc §4）已有完整代码骨架。

#### P4.2 单元测试

**文件：** `quant/oms/tests/test_crypto_futu_broker.py`

同 P3.2 测试模式，额外覆盖：
- `_resolve_acc_id` 自动发现逻辑
- `_to_futu_code` 转换
- 无加密币账户时的错误处理

---

### P5：Broker 路由改造

#### P5.1 改造 `oms/broker/__init__.py`

在现有 `Broker`、`BrokerOrder`、`BrokerPosition`、`BrokerAccount`、`PaperBroker` 之上：

```python
class RouterOrderManager:
    """Routes orders to the correct broker based on symbol prefix."""

    def __init__(self, stock_broker: Broker, crypto_broker: Broker,
                 fallback_broker: Optional[Broker] = None):
        self._stock_broker = stock_broker      # FutuStockBroker
        self._crypto_broker = crypto_broker    # FutuCryptoBroker
        self._fallback = fallback_broker       # e.g. CryptoBinanceBroker

    def _broker_for(self, symbol: str) -> Broker:
        if "/" in symbol:           # "BTC/USDT" → crypto
            return self._crypto_broker
        if symbol.startswith("HK.") or symbol.startswith("US."):
            return self._stock_broker
        if symbol.startswith("CRYPTO_"):
            return self._fallback or self._crypto_broker
        raise ValueError(f"Unknown symbol prefix: {symbol}")

    async def submit_order(self, symbol, side, qty, **kwargs):
        return await self._broker_for(symbol).submit_order(symbol, side, qty, **kwargs)

    async def cancel_order(self, broker_id):
        # Try all brokers until found
        ...

    async def get_order(self, broker_id):
        ...

    async def get_positions(self):
        ...

    async def get_account(self):
        ...

    async def get_open_orders(self):
        ...
```

#### P5.2 单元测试

**文件：** `quant/oms/tests/test_router.py`

- 路由规则验证（HK. → stock, US. → stock, BTC/USDT → crypto）
- 未知 symbol → ValueError
- 路由层不改变 broker 返回的数据结构

---

### P6：管线集成

#### P6.1 更新 `collectors/main.py`

增加 `futu_stock` 和 `futu_crypto` 两个 source 路由：

```python
SOURCES = {
    "yfinance": YFinanceAdapter,
    "yfinance_hk": YFinanceHKAdapter,
    "akshare_hk": AkshareHKAdapter,
    "akshare_us": AkshareUSAdapter,
    "crypto_binance": CryptoBinanceAdapter,
    "futu_stock": FutuStockAdapter,       # ← NEW
    "futu_crypto": CryptoFutuAdapter,     # ← NEW
}
```

不需要改动现有 Collector 的运行逻辑，新增 source 用 `--source futu_stock` 触发。

#### P6.2 更新 `collectors/backfill.py`

增加 `futu_stock` 和 `futu_crypto` 作为 `--source` 可选值。
与 `main.py` 共享采集逻辑，backfill 本质是相同的 fetch_bars + write_bars_to_gcs 流程。

#### P6.3 创建 Terraform 部署文件

**文件：**
- `quant/terraform/collector-futu-stock.tf` — 见 P0.5
- `quant/terraform/collector-futu-crypto.tf` — 同上模式，调度周期 5min（7×24）

---

### P7：集成测试 + 验证

#### P7.1 本地验证（需 OpenD 进程）

```bash
# 1. 启动 OpenD
FutuOpenD -cfg_file=FutuOpenD.xml &

# 2. 测试数据采集
python3 -m collectors.main --source futu_stock --symbols HK.00700 --frequency 1d

# 3. 验证 GCS 输出
gsutil ls gs://quant-data-xxx/raw/hk/bars/freq=futu_1d/...

# 4. 测试交易（模拟环境）
python3 -c "
from oms.broker.futu_stock_broker import FutuStockBroker
import asyncio
broker = FutuStockBroker(trd_env=TrdEnv.SIMULATE)
print(asyncio.run(broker.get_account()))
"
```

#### P7.2 测试用例汇总

| 测试 | 文件 | 类型 |
|------|------|------|
| Stock Adapter | `test_futu_stock_adapter.py` | 单元（mock） |
| Crypto Adapter | `test_crypto_futu_adapter.py` | 单元（mock） |
| Stock Broker | `test_futu_stock_broker.py` | 单元（mock） |
| Crypto Broker | `test_crypto_futu_broker.py` | 单元（mock） |
| Router | `test_router.py` | 单元 |
| 端到端采集 | 本地手动 | 集成 |
| 端到端交易 | 本地手动 | 集成 |

#### P7.3 验收标准

- [ ] 所有单元测试通过（≥90% 覆盖率 on new code）
- [ ] `--source futu_stock` 能成功获取 HK.00700 日线并写入 GCS
- [ ] `--source futu_crypto` 能成功获取 BTC/USDT 分钟线并写入 GCS
- [ ] GCS 路径包含 `freq=futu_1d` / `freq=futu_1m`
- [ ] `RouterOrderManager` 能正确路由 HK./US./BTC/USDT
- [ ] Stock Broker 在 SIMULATE 环境下能下单、查持仓
- [ ] Crypto Broker（REAL）能查账户、查持仓
- [ ] 现有 yfinance/akshare/Binance 采集器不受影响

---

## 四、文件变更清单

| # | 文件 | 操作 | 阶段 |
|---|------|------|------|
| 1 | `docker/Dockerfile.collector` | **创建** | P0 |
| 2 | `docker/start_collect.sh` | **创建** | P0 |
| 3 | `docker/FutuOpenD.xml.template` | **创建** | P0 |
| 4 | `.gitignore` | **修改** | P0 |
| 5 | `terraform/collector-futu-stock.tf` | **创建** | P0 |
| 6 | `terraform/collector-futu-crypto.tf` | **创建** | P0 |
| 7 | `collectors/adapters/futu_stock_adapter.py` | **创建** | P1 |
| 8 | `collectors/tests/test_futu_stock_adapter.py` | **创建** | P1 |
| 9 | `collectors/adapters/crypto_futu_adapter.py` | **创建** | P2 |
| 10 | `collectors/tests/test_crypto_futu_adapter.py` | **创建** | P2 |
| 11 | `oms/broker/futu_stock_broker.py` | **创建** | P3 |
| 12 | `oms/tests/test_futu_stock_broker.py` | **创建** | P3 |
| 13 | `oms/broker/crypto_futu_broker.py` | **创建** | P4 |
| 14 | `oms/tests/test_crypto_futu_broker.py` | **创建** | P4 |
| 15 | `oms/broker/__init__.py` | **修改** | P5 |
| 16 | `oms/tests/test_router.py` | **创建** | P5 |
| 17 | `collectors/main.py` | **修改** | P6 |
| 18 | `collectors/backfill.py` | **修改** | P6 |

**合计：14 个新文件 + 4 个文件修改**

---

## 五、回滚与风险

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| OpenD 版本升级 API 不兼容 | 低 | 高 | 固定版本号，CI 锁版本 |
| 美股 LV3 权限未通过 | 中 | 中 | fallback 到 yfinance |
| 订阅额度不足（300） | 低 | 中 | 按需 subscribe，采集完 unsubscribe |
| 历史 K 线额度耗尽（300/7天） | 低 | 低 | 分散回填计划，优先高优先级 symbol |
| Crypto 无模拟交易 | 中 | 中 | 回测用 PaperBroker，实盘前手动验证 |

**回滚方案：** 所有新增 Futu 文件不影响现有 collector/broker。
回滚只需禁用 Terraform timer、删除新增适配器即可。
