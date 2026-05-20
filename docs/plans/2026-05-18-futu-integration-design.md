# Futu OpenAPI 全面集成设计文档

> Date: 2026-05-20
> Status: Draft（待 Phase 0 数据管道修复完成 + Futu OpenD 接入后执行）
> 权限依据: https://openapi.futunn.com/futu-api-doc/intro/authority.html
> 资产状态: 已确认总资产 ≥ 1 万 HKD → 订阅额度 300 / 历史K线额度 300
> ⚠️ 美股 API 行情与客户端权限不共用（需单独购买 Nasdaq Basic 行情卡）

---

## 1. 覆盖范围

```
┌─────────────────────────────────────────────────────────────┐
│                    Futu OpenAPI 集成                         │
├──────────────┬──────────────┬──────────────┬────────────────┤
│  港股 LV2    │  美股 Nasdaq │  加密币 LV1  │  Cloud Run     │
│              │   Basic      │              │  部署架构       │
├──────────────┼──────────────┼──────────────┼────────────────┤
│ 数据适配器   │ 数据适配器    │ 数据适配器    │ 方案 A: Job    │
│  (Future)   │  (Future)    │  (Planned)   │  瞬启瞬灭      │
│ 交易 Broker  │ 交易 Broker  │ 交易 Broker  │  后续可升级    │
│  (Future)   │  (Future)    │  (Planned)   │  到 Service    │
└──────────────┴──────────────┴──────────────┴────────────────┘
```

### 1.1 权限详情（开户后，总资产≥1万HKD）

| 市场 | 品种 | 权限 | 时延 | 摆盘 | 获取方式 |
|------|------|------|------|------|---------|
| 港股 | 股票/ETF/窝轮 | **LV2** | 实时 | 10档 | 境内IP免费 |
| 港股 | 期权/期货 | LV2 | 实时 | — | 推广期免费 |
| 美股 | 股票/ETF | **LV1** ⚠️ | — | 基本报价 | 需购买 Nasdaq Basic 行情卡¹ |
| 美股 | 期权 | LV1 | — | — | 达标免费 |
| A股 | 股票/ETF | LV1 | 实时 | — | 境内IP免费 |
| 加密币 | 主流币种/币对 | LV1 | 实时 | 1/5/10/20/40档 | 推广期免费 |

| 额度类型 | 数量 | 重置周期 |
|----------|------|---------|
| 订阅额度 | **300** | 释放即恢复 |
| 历史K线额度 | **300** | **最近30天滚动**² |

> ¹ 美股 API 行情与客户端权限不共用。LV1 = Nasdaq Basic（基本报价），需购买行情卡。LV2 = Nasdaq Basic+TotalView（含深度摆盘），也需购买。见 https://openapi.futunn.com/futu-api-doc/intro/authority.html
> ² 历史 K 线额度释义：最近 30 天内，每请求 1 只股票的历史 K 线占用 1 个额度，**重复请求同一只不重复累计**。30 天滚动窗口，非固定周期重置。

---

### 1.2 前置依赖：Phase 0 数据管道修复

**Futu 采集器的部署前提：Phase 0（append-only + freq 分隔）必须先完工。**

Phase 0 将 GCS 路径从旧格式升级为带频率维度的新格式：

```
# 旧（当前）
raw/{market}/bars/year=.../month=.../day=.../symbol={S}.parquet

# 新（Phase 0 完成后）
raw/{market}/bars/freq={5m,1d}/year=.../month=.../day=.../symbol={S}.parquet
```

**为什么 Futu 需要等 Phase 0：**
1. Futu 数据本质上与现有 yfinance/akshare 数据是**同市场同频率**的
2. 如果沿用旧路径，Futu 写入时会与旧源数据碰撞（同一 symbol+date 覆盖）
3. 只有 freq 维度就位，Futu 才能作为独立数据源写入自己的 `freq=futu_1m/` 路径
4. BQ 表的 `WRITE_APPEND + _ingest_time dedup` 机制也允许 Futu 数据与旧源共存，便于切换验证

**接入阶段建议：**
1. Phase 0 完成 → GCS/BQ 体系就位
2. Futu 采集器以 `freq=futu_1m` / `freq=futu_1d` 写入，与 yfinance/akshare 源并行
3. 数据验证无误后，策略层默认使用 Futu 数据；yfinance/akshare 保留作为 fallback（不删除、不切换）

---

## 2. 现有数据源现状

```
collectors/adapters/
├── yfinance_adapter.py         ← 美股日K/分钟K (15min 延迟)
├── yfinance_hk_adapter.py      ← 港股日K (15min 延迟)
├── akshare_hk_adapter.py       ← 港股日K (兜底, 质量不稳)
├── akshare_us_adapter.py       ← 美股日K (兜底)
├── alpaca_adapter.py           ← 美股实时 (需 Alpaca API Key)
├── crypto_binance_adapter.py   ← 加密币 (ccxt Binance)
└── base.py                     ← MarketAdapter Protocol
```

#### 新增策略

Futu 是**新增数据源**，与 yfinance/akshare/Binance 并行，不替代任何人。

| 阶段 | 操作 | 说明 |
|------|------|------|
| 1 | 新建 Futu 采集器，以独立 freq 路径写入（`freq=futu_1m`） | 与 yfinance `freq=5m` 互不冲突 |
| 2 | 数据质量验证（对比 Futu vs YFinance 延迟、完整性、正确性） | 确保 Futu 数据可靠 |
| 3 | 策略层默认切换到 Futu 数据源 | yfinance/akshare 保留定时调度，作为 failover 数据源 |
| 4 | 长期运维：Futu 异常时，采集器代码和 Terraform timer 保留，可一键切回 | 不删除旧源 Job |

**数据源关系**（新增关系标 **→**）：

| 市场 | 主数据源（新增） | 备用数据源（保留） | 优先级说明 |
|------|-----------------|-------------------|-----------|
| 港股 | **Futu HK LV2** → 实时，10档 | yfinance（15min 延迟） / akshare（兜底） | Futu 提升最大：实时 vs 15min |
| 美股 | **Futu LV1** → Nasdaq Basic 行情 | yfinance（15min 延迟） / akshare（兜底） | API 美股行情需购买 Nasdaq Basic 行情卡¹ |
| 加密币 | **Futu LV1** / **Binance**（并行） | — | 两者并行，用户按需选源 |
| 加密币（交易） | **Futu Crypto Broker**（实盘） | **Binance Broker**（回测+备选） | Futu 无模拟，回测用 Binance PaperBroker |

---

## 3. 新增数据适配器

### 3.1 FutuStockAdapter（港股 + 美股）

```
collectors/adapters/futu_stock_adapter.py   ← NEW
```

```python
class FutuStockAdapter:
    """Futu OpenD stock market adapter for HK + US equities."""

    market = "MIXED"  # 或按实际市场区分

    # Futu 符号格式: HK.00700, US.AAPL
    # 传递时直接用 "HK.00700" 格式

    def __init__(self, host="127.0.0.1", port=11111):
        self.ctx = OpenQuoteContext(host=host, port=port)

    def fetch_bars(self, symbols, start, end, frequency="1d") -> pd.DataFrame:
        """Fetch OHLCV via request_history_kline with pagination.
        
        symbols format: ["HK.00700", "HK.09988", "US.AAPL"]
        Works for both HK (LV2) and US (LV1: Nasdaq Basic / LV2: TotalView).
        """
        ktype = self._map_frequency(frequency)
        records = []
        for code in symbols:
            page_key = None
            while True:
                ret, data, page_key = self.ctx.request_history_kline(
                    code, start=start, end=end,
                    ktype=ktype, autype=AuType.QFQ,  # 前复权
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
        """Get all traded symbols from Futu platform.
        
        Uses get_plate_stock for HK stocks, plus predefined US stock list.
        Can also use get_stock_basicinfo with market parameter.
        """
        ...

    def market_hours(self, d: date) -> tuple[time, time]:
        """Use get_market_state to determine current market hours."""
        ...
```

### 3.2 CryptoFutuAdapter（加密币）

见独立设计文档。使用 `CC.BTCUSD` 格式，通过 `request_history_kline` 获取 K 线。

---

## 4. 新增交易 Broker

### 4.1 FutuStockBroker

```
oms/broker/futu_stock_broker.py   ← NEW
```

```python
class FutuStockBroker(Broker):
    """Futu OpenD stock trading broker."""

    def __init__(self, host="127.0.0.1", port=11111, trd_env=TrdEnv.SIMULATE):
        self.ctx = OpenSecTradeContext(
            host=host, port=port,
            security_firm=SecurityFirm.FUTUSECURITIES,
        )
        self.trd_env = trd_env

    async def submit_order(self, symbol, side, qty, order_type="market",
                           limit_price=None) -> BrokerOrder:
        ret, data = self.ctx.place_order(
            price=limit_price or 0.0, qty=qty, code=symbol,
            trd_side=TrdSide.BUY if side == "buy" else TrdSide.SELL,
            order_type=OrderType.NORMAL if order_type == "limit" else OrderType.ABS_MARKET,
            trd_env=self.trd_env,
        )
        ...

    async def get_positions(self) -> list[BrokerPosition]:
        ret, data = self.ctx.position_list_query(trd_env=self.trd_env)
        ...

    async def get_account(self) -> BrokerAccount:
        ret, data = self.ctx.accinfo_query(trd_env=self.trd_env)
        ...

    # ... cancel_order, get_order, get_open_orders 同上模式
```

**模拟/实盘**：
- 默认 `TrdEnv.SIMULATE`（走富途模拟环境）
- 切实盘需参数 `trd_env=TrdEnv.REAL`，且需在 OpenD GUI 上手动解锁交易

### 4.2 FutuCryptoBroker

```
oms/broker/crypto_futu_broker.py   ← NEW
```

使用 `OpenCryptoTradeContext`。⚠️ 仅支持 `TrdEnv.REAL`（无模拟环境）。
符号格式：`CC.BTCUSD`。

### 4.3 符号 → Broker 路由

```
oms/broker/__init__.py — RouterOrderManager
```

| Symbol 模式 | Broker | Context |
|-------------|--------|---------|
| `HK.xxx` | `FutuStockBroker` | `OpenSecTradeContext` |
| `US.xxx` | `FutuStockBroker` | `OpenSecTradeContext` |
| `CC.xxx` | `FutuCryptoBroker` | `OpenCryptoTradeContext` |
| `xxx/xxx` (含`/`) | `FutuCryptoBroker` | `OpenCryptoTradeContext` |
| `CRYPTO_xxx` | `BinanceBroker` | `ccxt.binance`（备选） |

---

## 5. Cloud Run 部署架构

### 5.1 最终方案：方案 A（纯 Job）

```
不是方案 A/B 混合。全部走 Cloud Run Job。

原因是：
- 定时抓取   → 自然就是 Job
- 回填        → 自然就是 Job
- 实时推送    → 当前不需要，后续上 Service 也兼容 ┃
- 实盘交易    → OrderManager 不跑在 Cloud Run 上，跑在本地
                 （实盘交易在用户本地终端，不是云上）

3 个 Job（Phase 0 的 6 个 Collector 之外，额外 3 个 Futu 源）：
```

| Job 名称 | 镜像 | 触发方式 | 数据源 |
|---------|------|---------|--------|
| `collector-futu-stock` | `Dockerfile.collector` | 每30分钟 cron（盘中） | Futu OpenD |
| `collector-futu-crypto` | `Dockerfile.collector` | 每5分钟 cron（7×24） | Futu OpenD |
| `collector-binance-crypto` | `Dockerfile.collector` | 每5分钟 cron（并行） | ccxt Binance |

```
使用 Terraform 部署，每个 Job 独立 timer。
Futu Job 和 Binance Job 同时运行（并行双源），策略层按需选数据源。
```

**并行双源的设计**：
- 同一市场、同一频率的数据，Futu 和 Binance 各自写入 `freq=futu_1m` / `freq=binance_1m` 路径
- 策略层 / SDK 通过 `source` 参数选择用哪个源，或基于可用性自动 fallback
- Futu 异常时，切换指向旧源只需改 `--source` 参数，无需重新部署

### 5.2 OpenD 运行时架构

OpenD 是 x86_64 二进制，运行方式有两种选择：

#### 方案 A：Job 内嵌 OpenD（定时采集场景）

```
Cloud Run Job（定时触发）
├── 启动 OpenD（后台）
├── 等待登录成功（轮询 ≤30s）
├── 采集数据 → 写入 GCS
├── 关闭 OpenD
└── Job 结束
```

**适合：** 定时采集 K 线（当前场景）
**优点：** 无额外进程管理，Job 瞬启瞬灭
**缺点：** 每次启动需要 OpenD 登录（约 5-15s 开销），不适合 WebSocket 实时流

#### 方案 B：本地常驻 OpenD + Cloud Run 拉取（实时流场景）

```
用户本地 / VPS
└── OpenD 常驻（24/7）
    ├── WebSocket 实时行情推送 → Live Trading Loop
    └── REST API（历史 K 线） ← Cloud Run Job 定时拉取
                          ^
                    ┌─────┴─────┐
              Cloud Run Collector Job
              （OPEND_HOST 指向本地 OpenD）
```

**适合：** 实时 WebSocket + 实盘交易
**优点：** OpenD 只需登录一次，WebSocket 长连接持续推送
**缺点：** 需要一台 24/7 机器（本地 PC 或 VPS），Cloud Run 仍可定时拉取历史数据

#### 推荐路径

| 阶段 | 架构 | 说明 |
|------|------|------|
| 第一阶段（当前） | 方案 A | 定时采集历史 K 线，验证数据质量 |
| 第二阶段（Live Loop） | 方案 B | 本地/VPS 跑 OpenD + Live Trading Loop |

Cloud Run Collector Job 始终通过 `OPEND_HOST` / `OPEND_PORT` 环境变量连接 OpenD，
两种方案下代码不感知差异，只配不同的连接地址。

---

### 5.3 Dockerfile + 启动脚本

```dockerfile
# Dockerfile.collector — 适用于所有 collector 类型的 Cloud Run Job
FROM python:3.11-slim

# 安装依赖
RUN pip3 install futu-api pandas pyarrow --break-system-packages

# 复制 OpenD
COPY --from=opend-builder /opt/opend/ /opt/opend/
COPY FutuOpenD.xml /opt/opend/

# 复制应用代码
COPY collectors/ /app/collectors/

# 启动脚本
COPY start_collect.sh /opt/
RUN chmod +x /opt/start_collect.sh
CMD ["/opt/start_collect.sh"]
```

```bash
# start_collect.sh — 通用启动脚本
#!/bin/bash
set -e

# 1. 后台启动 OpenD
/opt/opend/FutuOpenD -cfg_file=/opt/opend/FutuOpenD.xml &
OPEND_PID=$!

# 2. 轮询等待 OpenD 登录成功（最多等 30 秒）
echo "Waiting for OpenD login..."
for i in $(seq 1 30); do
    if python3 -c "
from futu import *
ctx = OpenQuoteContext(host='${OPEND_HOST:-127.0.0.1}', port=${OPEND_PORT:-11111})
ret, state = ctx.get_global_state()
ctx.close()
exit(0 if ret == RET_OK and state.get('qot_logined') else 1)
" 2>/dev/null; then
        echo "OpenD logged in successfully"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "ERROR: OpenD login timeout"
        kill $OPEND_PID 2>/dev/null
        exit 1
    fi
    sleep 1
done

# 3. 运行采集任务
echo "Starting collector..."
python3 -m collectors.main

# 4. 退出
echo "Collector done, shutting down OpenD..."
kill $OPEND_PID 2>/dev/null
wait $OPEND_PID 2>/dev/null
```

### 5.4 连接参数

```python
# collectors/adapters/futu_stock_adapter.py
OPEND_HOST = os.environ.get("OPEND_HOST", "127.0.0.1")
OPEND_PORT = int(os.environ.get("OPEND_PORT", "11111"))
```

```
# terraform Job 环境变量
OPEND_HOST=127.0.0.1    # 同容器内就是本地
OPEND_PORT=11111
GCS_BUCKET=quant-data-xxx
COLLECTOR_SOURCE=futu_stock
```

---

## 6. FutuOpenD.xml 配置

```xml
<futu_opend>
    <ip>127.0.0.1</ip>
    <api_port>11111</api_port>
    <login_account>${FUTU_LOGIN_ACCOUNT}</login_account>
    <login_pwd_md5>${FUTU_LOGIN_PWD_MD5}</login_pwd_md5>
    <lang>chs</lang>
    <log_level>info</log_level>
    <!-- 无需 WebSocket / Telnet，Job 不需要 -->
</futu_opend>
```

> ⚠️ **密码安全：**
> - `login_pwd_md5` 是登录密码的 **MD5 小写 32 位哈希**，由部署者通过环境变量注入
> - 不要将任何密码（明文或 MD5）提交到代码仓库
> - `.gitignore` 中已排除 `FutuOpenD.xml`，本地文件也需注意安全
> - MD5 生成方式：`echo -n 'your_password' | md5sum | cut -d' ' -f1`
> - 推荐方案：部署时通过 Terraform 的 `environment_variables` 或 Secret Manager 传入

---

## 7. 项目目录变化

```
quant/
├── collectors/
│   ├── adapters/
│   │   ├── crypto_futu_adapter.py   ← NEW 加密币数据
│   │   ├── futu_stock_adapter.py    ← NEW 港股+美股数据
│   │   └── ...
│   ├── backfill.py                   ← 加 --source futu_stock / futu_crypto
│   └── main.py                       ← 加 futu_stock / futu_crypto 适配
├── oms/
│   ├── broker/
│   │   ├── crypto_futu_broker.py    ← NEW 加密币交易
│   │   ├── futu_stock_broker.py     ← NEW 港股+美股交易
│   │   └── __init__.py              ← RouterOrderManager 改造
├── docker/
│   ├── Dockerfile.collector         ← NEW 采集器镜像
│   └── start_collect.sh             ← NEW 启动脚本
├── terraform/
│   ├── collector-futu-stock.tf      ← NEW Cloud Run Job
│   ├── collector-futu-crypto.tf     ← NEW Cloud Run Job
│   └── collector-binance-crypto.tf  ← 保留现有
└── docs/
    └── Futu-API-Doc-zh-Python.md    ← 已存
```

---

## 8. 向后兼容矩阵

| 现有组件 | 改动 | 兼容性 |
|----------|------|--------|
| `yfinance_adapter.py` | 不变 | ✅ 可继续用 |
| `crypto_binance_adapter.py` | 不变 | ✅ 保留备选 |
| `crypto_broker.py` (PaperBroker) | 不变 | ✅ 回测继续用 |
| `alpaca_broker.py` | 不变 | ✅ |
| `order_manager.py` | 路由改造 | ⚠️ 现有代码依赖路由策略 |
| `backfill.py` | 加 source | ✅ 现有 source 照跑 |
| `main.py` | 加适配器 | ✅ 现有 source 照跑 |
| `FutuOpenD.xml` | 不纳入 repo | ⚠️ 含密码, 加 .gitignore |

---

## 9. 已知限制与风险

| 限制 | 影响 | 应对 |
|------|------|------|
| 订阅额度 300 | 同时只能看 300 只行情 | 用 query_subscription 管理, 不用的就 unsubscribe |
| 美股行情需单独购买行情卡 | API 行情与客户端不共用，Nasdaq Basic 需购买 | 先确认当前 OpenD 环境下实际可用的美股权限 |
| 历史K线额度 300（30天滚动） | 每 30 天内最多 300 只不同标的 | 优先回填高优 symbol，重复回填不消耗额度 |
| OpenD 是 x86_64 二进制 | Cloud Run 需 x86 架构 | `--platform=linux/amd64` |
| OpenD 日志文件写入 | Cloud Run 只写 /tmp | 配置 log_path=/tmp |
| 美股期权/期货无权限 | 后面可能用到 | 到时再买行情卡 |
| crypto 仅限价/市价单 | 不支持止盈止损 | 策略层加 |
| A股 LV1 仅限境内IP | Cloud Run 可能非境内 | 本地跑可加速A股回填 | 
