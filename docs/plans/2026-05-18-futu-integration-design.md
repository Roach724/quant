# Futu OpenAPI 全面集成设计文档

> Date: 2026-05-18
> Status: Draft（待富途 API 协议签署后执行）
> 范围: 港股 + 美股 + 加密币的数据适配器、交易 Broker、Cloud Run 部署

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

**替换关系**:
| 当前数据源 | 替代方 | 优先级 | 说明 |
|-----------|-------|--------|------|
| yfinance (港股) | Futu HK LV2 | **高** | 实时 vs 15min 延迟，数据质量提升最大 |
| akshare_hk (港股) | Futu HK LV2 | **高** | 消除 akshare 不稳定性 |
| yfinance (美股) | Futu Nasdaq Basic | **中** | 日K 免费，分钟K 有延迟，可保留备用 |
| akshare_us (美股) | Futu Nasdaq Basic | **中** | 兜底保留 |
| alpaca (美股) | Futu Nasdaq Basic | **低** | 如果已有 Alpaca 账户，可留 |
| crypto_binance (加密币) | Futu LV1 | **中** | 并行，按需选源 |

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
        Works for both HK (LV2) and US (Nasdaq Basic).
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

3 个 Job，各有独立 Dockerfile 和 cron 触发：
```

| Job 名称 | 镜像 | 触发方式 | 数据源 |
|---------|------|---------|--------|
| `collector-futu-stock` | `Dockerfile.collector` | 每30分钟 cron（盘中） | Futu OpenD |
| `collector-futu-crypto` | `Dockerfile.collector` | 每5分钟 cron（7×24） | Futu OpenD |
| `collector-binance-crypto` | `Dockerfile.collector` | 每5分钟 cron（备选） | ccxt Binance |

```
使用 Terraform 部署，每个 Job 独立 timer。
想切换数据源时，启用/禁用 timer 即可。
```

**为什么不搞并行双源？** 同一个 Job 一次只能跑一个 source。双源 = 两个 Job，各自独立配置和调度周期。

### 5.2 Dockerfile + 启动脚本

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

### 5.3 连接参数

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
    <login_account>+852 52689274</login_account>
    <login_pwd_md5>1a705dda1eb57a936deae580328022cf</login_pwd_md5>
    <lang>chs</lang>
    <log_level>info</log_level>
    <!-- 无需 WebSocket / Telnet，Job 不需要 -->
</futu_opend>
```

注意：**密码用 MD5 密文，不要明文存**。密文是 `Tweakdxv3s7` 的 MD5。

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
| 订阅额度 100 | 同时只能看 100 只行情 | 用 query_subscription 管理, 不用的就 unsubscribe |
| 美股 LV2 (Nasdaq Basic) 非完全实时 | 基本报价实时, 深度摆盘受限 | 日K 回测不受影响 |
| OpenD 是 x86_64 二进制 | Cloud Run 需 x86 架构 | `--platform=linux/amd64` |
| OpenD 日志文件写入 | Cloud Run 只写 /tmp | 配置 log_path=/tmp |
| 美股期权/期货无权限 | 后面可能用到 | 到时再买行情卡 |
| crypto 仅限价/市价单 | 不支持止盈止损 | 策略层加 | 
