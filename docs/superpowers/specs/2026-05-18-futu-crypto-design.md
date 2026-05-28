# Futu 加密货币集成设计文档

> Date: 2026-05-20
> Status: Draft
> 前置依赖: [Phase 0 数据管道修复](2026-05-18-futu-integration-design.md#12-前置依赖phase-0-数据管道修复) 需先完成
> 基于 Futu API Skills 文档 + quant 项目现有 crypto 结构

---

## 1. 背景

当前项目已有币安（Binance）加密货币支持：
- `collectors/adapters/crypto_binance_adapter.py` — 通过 ccxt 获取 OHLCV 数据
- `oms/broker/crypto_broker.py` — CryptoPaperBroker（回测）+ CryptoBinanceBroker（实盘）
- 符号格式：`BTC/USDT`

Futu OpenD 支持加密货币（权限：LV1），符号格式：`CC.BTCUSD`
- 行情：实时报价、K 线、快照、摆盘、资金流向
- 交易：`OpenCryptoTradeContext`（仅现金买入，无模拟）

目标是两家并行，用户可以按需选择数据源。

---

## 2. 符号格式映射

| 含义 | Binance | Futu |
|------|---------|------|
| 比特币/美元币对 | `BTC/USDT` | `CC.BTCUSD` |
| 以太坊/美元币对 | `ETH/USDT` | `CC.ETHUSD` |
| 比特币币种（行情/持仓） | `BTC` | `CC.BTC` |
| 以太坊币种（行情/持仓） | `ETH` | `CC.ETH` |

### 标准化内部格式

内部统一使用 **Binance 风格带 `/` 的格式**：
```python
INTERNAL_SYMBOL = "BTC/USDT"
```

适配器层负责转换：
```python
# binance adapter: 直接用
symbol = "BTC/USDT"

# futu adapter:
futu_quote_code = "CC." + symbol.replace("/", "")  # "CC.BTCUSD"
futu_currency_code = "CC." + symbol.split("/")[0]  # "CC.BTC"
```

---

## 3. 数据适配器

### 新增：FutuCryptoAdapter

```
collectors/adapters/
├── crypto_binance_adapter.py   ← 不变
├── crypto_futu_adapter.py      ← NEW
└── ...
```

### 接口实现

```python
class CryptoFutuAdapter(MarketAdapter):
    """Futu OpenD cryptocurrency market adapter."""

    market = "CRYPTO"

    # Futu 支持的加密货币币对（CC.BTCUSD 等）
    _SUPPORTED_SYMBOLS = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "LTC/USDT",
        "XRP/USDT", "DOT/USDT", "ADA/USDT", "AVAX/USDT",
        "LINK/USDT", "UNI/USDT", "ATOM/USDT", "BCH/USDT",
    ]

    _FREQ_MAP = {
        "1m": KLType.K_1M, "5m": KLType.K_5M, "15m": KLType.K_15M,
        "30m": KLType.K_30M, "1h": KLType.K_60M,
        "1d": KLType.K_DAY, "1w": KLType.K_WEEK,
    }

    def __init__(self, host="127.0.0.1", port=11111):
        self.ctx = OpenQuoteContext(host=host, port=port)

    def _to_futu_code(self, symbol: str) -> str:
        """Convert 'BTC/USDT' → 'CC.BTCUSD'"""
        return "CC." + symbol.replace("/", "")

    def fetch_bars(self, symbols, start, end, frequency="1m") -> pd.DataFrame:
        """Fetch OHLCV via request_history_kline, paginated."""
        ktype = self._FREQ_MAP.get(frequency, KLType.K_1M)
        records = []
        for sym in symbols:
            futu_code = self._to_futu_code(sym)
            page_key = None
            while True:
                ret, data, page_key = self.ctx.request_history_kline(
                    futu_code, start=start, end=end,
                    ktype=ktype, autype=AuType.NONE,  # crypto no复权
                    max_count=1000, page_req_key=page_key,
                )
                if ret != RET_OK:
                    break
                for _, row in data.iterrows():
                    records.append({
                        "symbol": sym,
                        "timestamp": row["time_key"],
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(float(row["volume"])),
                        "market": self.market,
                    })
                if page_key is None:
                    break
        return pd.DataFrame(records)

    def fetch_supported_symbols(self) -> list[str]:
        return self._SUPPORTED_SYMBOLS

    def market_hours(self, d) -> tuple:
        return (time(0, 0), time(23, 59, 59))  # 7×24
```

---

## 4. 交易 Broker

### 新增：FutuCryptoBroker

```
oms/broker/
├── crypto_broker.py              ← 不变（含 PaperBroker + BinanceBroker）
├── crypto_futu_broker.py         ← NEW
└── ...
```

### 核心差异

| 维度 | Binance Broker | Futu Broker |
|------|---------------|-------------|
| Context 类 | ccxt.binance | `OpenCryptoTradeContext` |
| 符号格式 | `BTC/USDT` | `CC.BTCUSD` |
| 订单数量 | 整数或小数 | 支持小数（如 0.000136 BTC） |
| 模拟交易 | 有（testnet） | ❌ **不支持模拟** |
| 实盘交易 | 需 Binance API Key | 需富途账户 + 交易密码 |
| 下单限频 | 无严格限制 | 15 次/30 秒 |
| 改单 | ⚠️ 不支持 | ❌ 不支持（只支持撤单/全撤） |
| 限价单有效期 | 自定义 | GTC（FUTUHK/FUTUINC），限价单（FUTUSG） |
| 市价单 | 支持 | FUTUHK/FUTUINC 支持，FUTUSG 不支持 |

### 接口实现

```python
class FutuCryptoBroker(Broker):
    """Futu OpenD cryptocurrency trading broker (REAL only)."""

    def __init__(self, host="127.0.0.1", port=11111,
                 security_firm=SecurityFirm.FUTUINC):
        self.ctx = OpenCryptoTradeContext(
            host=host, port=port,
            security_firm=security_firm,
        )
        self._acc_id = None

    def _resolve_acc_id(self) -> int:
        """Auto-discover crypto account ID."""
        if self._acc_id:
            return self._acc_id
        ret, data = self.ctx.get_acc_list()
        if ret == RET_OK:
            for _, row in data.iterrows():
                if "CRYPTO" in str(row.get("trdmarket_auth", "")):
                    self._acc_id = int(row["acc_id"])
                    return self._acc_id
        raise RuntimeError("No crypto account found")

    def _to_futu_code(self, symbol: str) -> str:
        return "CC." + symbol.replace("/", "")

    async def submit_order(self, symbol, side, qty, order_type="market",
                           limit_price=None) -> BrokerOrder:
        acc_id = self._resolve_acc_id()
        futu_side = TrdSide.BUY if side == "buy" else TrdSide.SELL

        # Futu crypto: only cash buy, no margin
        ret, data = self.ctx.place_order(
            price=limit_price or 0.0,
            qty=qty,
            code=self._to_futu_code(symbol),
            trd_side=futu_side,
            order_type=OrderType.NORMAL if order_type == "limit" else OrderType.ABS_MARKET,
            trd_env=TrdEnv.REAL,      # crypto has NO SIMULATE
            acc_id=acc_id,
            remark="quant-futu-crypto",
        )
        if ret != RET_OK:
            raise RuntimeError(f"Order failed: {data}")
        return BrokerOrder(
            broker_id=str(data["order_id"][0]),
            symbol=symbol, side=side, qty=qty,
            status="submitted",
        )

    async def get_positions(self) -> list[BrokerPosition]:
        acc_id = self._resolve_acc_id()
        ret, data = self.ctx.position_list_query(trd_env=TrdEnv.REAL, acc_id=acc_id)
        if ret != RET_OK:
            return []
        positions = []
        for _, row in data.iterrows():
            code = row["code"]  # "CC.BTC"
            internal_sym = code.replace("CC.", "") + "/USD"  # simplified
            positions.append(BrokerPosition(
                symbol=internal_sym,
                qty=float(row["qty"]),
                avg_entry_price=float(row["cost_price"]),
                market_value=float(row["market_val"]),
                unrealized_pnl=float(row.get("unrealized_pl", 0)),
            ))
        return positions

    async def get_account(self) -> BrokerAccount:
        acc_id = self._resolve_acc_id()
        ret, data = self.ctx.accinfo_query(trd_env=TrdEnv.REAL, acc_id=acc_id)
        if ret != RET_OK:
            return BrokerAccount(cash=0, equity=0, buying_power=0)
        row = data.iloc[0]
        return BrokerAccount(
            cash=float(row["cash"]),
            equity=float(row["total_asset"]),
            buying_power=float(row.get("cash_buy_power", 0)),
        )

    async def cancel_order(self, broker_id: str) -> bool:
        acc_id = self._resolve_acc_id()
        ret, data = self.ctx.modify_order(
            modify_order_op=ModifyOrderOp.NORMAL,
            order_id=broker_id, qty=0, price=0,
            trd_env=TrdEnv.REAL, acc_id=acc_id,
        )
        return ret == RET_OK

    async def get_order(self, broker_id: str) -> BrokerOrder:
        acc_id = self._resolve_acc_id()
        ret, data = self.ctx.order_list_query(
            trd_env=TrdEnv.REAL, acc_id=acc_id,
        )
        if ret != RET_OK:
            return None
        match = data[data["order_id"].astype(str) == broker_id]
        if match.empty:
            return None
        row = match.iloc[0]
        return BrokerOrder(
            broker_id=str(row["order_id"]),
            symbol=row["code"],
            side="buy" if row["trd_side"] == "BUY" else "sell",
            qty=float(row["qty"]),
            filled_qty=float(row["dealt_qty"]),
            status=row["order_status"].lower(),
            avg_price=float(row.get("dealt_avg_price", 0)),
        )

    async def get_open_orders(self):
        acc_id = self._resolve_acc_id()
        ret, data = self.ctx.order_list_query(
            status_filter=OrderStatusFilter.SUBMITTED,
            trd_env=TrdEnv.REAL, acc_id=acc_id,
        )
        if ret != RET_OK:
            return []
        orders = []
        for _, row in data.iterrows():
            orders.append(BrokerOrder(
                broker_id=str(row["order_id"]),
                symbol=row["code"],
                side="buy" if row["trd_side"] == "BUY" else "sell",
                qty=float(row["qty"]),
                filled_qty=float(row["dealt_qty"]),
                status=row["order_status"].lower(),
            ))
        return orders
```

---

## 5. Broker 路由机制

参考 [Futu 集成设计文档 §4.3](2026-05-18-futu-integration-design.md#43-符号--broker-路由)。

路由规则中加密货币部分的要点：

| Symbol 模式 | Broker | Context | 环境 |
|-------------|--------|---------|------|
| `CC.BTCUSD`（Futu 原生） | `FutuCryptoBroker` | `OpenCryptoTradeContext` | 仅 REAL |
| `BTC/USDT`（内部标准） | `FutuCryptoBroker`（含 `/` → `CC.` 转换） | `OpenCryptoTradeContext` | 仅 REAL |
| `CRYPTO_xxx` | `CryptoBinanceBroker` | `ccxt.binance` | SIMULATE / REAL |

全部路由逻辑集中在 `oms/broker/__init__.py` 的 `RouterOrderManager` 中，
**不在这个文档中重复**，以集成设计文档为准。

---

## 6. 限频与风险

| 限制 | 值 | 影响 |
|------|----|------|
| 订阅额度 | 100 | ETH/BTC 等 top 币种行情足够 |
| 下单限频 | 15 次/30秒 | 低频策略够用 |
| 加密货币账户 | 需在富途开通 | 未开通走 binance fallback |
| crypto 不支持模拟 | 只能实盘 | 回测用 CryptoPaperBroker |

---

## 7. 向后兼容

| 现有组件 | 改动 | 是否兼容 |
|----------|------|---------|
| `crypto_binance_adapter.py` | 不变 | ✅ |
| `crypto_broker.py` (PaperBroker) | 不变 | ✅ |
| `crypto_broker.py` (BinanceBroker) | 不变 | ✅ |
| `backfill.py` —source=binance | 不变 | ✅ |
| `backfill.py` —source=cryptofutu | 新增 | 🆕 |
| `OrderManager` | 改为路由模式 | ⚠️ 需小幅改造 |
