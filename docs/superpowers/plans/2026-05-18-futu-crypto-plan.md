# 实施计划 — Futu 加密货币集成

> Date: 2026-05-18
> 基于设计文档: docs/plans/2026-05-18-futu-crypto-design.md
> 每个任务 TDD 驱动

---

## 前置条件

- OpenD 已启动并登录成功（账号问卷已签）
- `pip3 install futu-api --break-system-packages` 已安装（futu-api 10.5.6508 ✅）

---

## 任务清单

### Task 1: 创建 crypto_futu_adapter.py（数据适配器）

**文件**: `collectors/adapters/crypto_futu_adapter.py`

**内容**:
- `CryptoFutuAdapter` 类，实现 `MarketAdapter` 协议
- `__init__(host="127.0.0.1", port=11111)` — 创建 `OpenQuoteContext`
- `_to_futu_code(symbol)` — `"BTC/USDT"` → `"CC.BTCUSD"`
- `fetch_bars(symbols, start, end, frequency)` — 调用 `request_history_kline` 分页
- `fetch_supported_symbols()` — 返回支持列表（12 个币对）
- `market_hours(d)` — 返回 00:00-23:59（7×24）
- 频率映射: `{"1m": KLType.K_1M, "5m": ..., "15m": ..., "1h": ..., "1d": ...}`

**测试**: `collectors/tests/test_crypto_futu_adapter.py`
- `test_to_futu_code`: `"BTC/USDT"` → `"CC.BTCUSD"`
- `test_fetch_supported_symbols`: 返回列表非空
- `test_market_hours`: 返回 00:00-23:59

**运行**:
```bash
cd /home/node/.openclaw/workspace/quant && LD_LIBRARY_PATH=/home/node/.local/lib:$LD_LIBRARY_PATH python3 -m pytest collectors/tests/test_crypto_futu_adapter.py -v
```

---

### Task 2: backfill.py 增加 --source cryptofutu

**文件**: `collectors/backfill.py`

**改动**:
- 在 `backfill()` 函数中增加 `cryptofutu` 数据源分支
- 参数列表中 `choices` 增加 `"cryptofutu"`：

```python
parser.add_argument("--source", default=os.environ.get("BACKFILL_SOURCE", "yfinance"),
                    choices=["yfinance", "alpaca", "cryptobinance", "yfinancehk", "cryptofutu"],
                    help="Data source adapter (default: yfinance)")
```

- 在 `cryptobinance` 分支后面增加 `cryptofutu` 分支：

```python
if source == "cryptofutu":
    from adapters.crypto_futu_adapter import CryptoFutuAdapter
    adapter = CryptoFutuAdapter(host=OPEND_HOST, port=OPEND_PORT)
    market = "CRYPTO"
```

- `${source}_all_symbols` 支持：从 `CryptoFutuAdapter` 获取

**测试**: 手动验证：
```bash
cd /home/node/.openclaw/workspace/quant && python3 collectors/backfill.py \
    --source cryptofutu \
    --start 2026-05-01 --end 2026-05-18 \
    --symbols BTC/USDT,ETH/USDT \
    --local-dir /tmp/crypto_test
```

---

### Task 3: 创建 crypto_futu_broker.py（交易 Broker）

**文件**: `oms/broker/crypto_futu_broker.py`

**内容**:
- `FutuCryptoBroker` 类，实现 `Broker` 协议
- `__init__(host, port, security_firm)` — 创建 `OpenCryptoTradeContext`
- `_resolve_acc_id()` — 自动发现加密货币账户 ID
- `submit_order(symbol, side, qty, order_type, limit_price)` — 下单（⚠️ 仅 REAL）
- `get_positions()` — 查询持仓
- `get_account()` — 查询账户资金
- `cancel_order(broker_id)` — 撤单
- `get_order(broker_id)` — 订单查询
- `get_open_orders()` — 未结订单

**关键差异 vs 股票 Broker**:
| 差异 | 处理 |
|------|------|
| 不支持模拟交易 | trd_env 固定为 TrdEnv.REAL |
| 符号格式 CC.BTCUSD | broker 内部转换 |
| 小数数量 | 直接传 float |
| acc_id 需自动发现 | 遍历 get_acc_list 找 CRYPTO |
| 不支持 modify_order | 只做 cancel |

**测试**: `oms/tests/test_crypto_futu_broker.py`
- 用 mock 测试 `_resolve_acc_id`
- 用 mock 测试 `_to_futu_code`

---

### Task 4: OrderManager 路由改造

**文件**: `oms/manager.py`

**改动**:
- 新增 `RouterOrderManager` 类，继承 `OrderManager`
- 添加 `_broker_map: dict[str, Broker]` 配置
- `_broker_for(symbol)` → 根据 symbol 格式选择 broker

```python
class RouterOrderManager(OrderManager):
    """Routes orders to correct broker by symbol prefix."""

    def __init__(self, broker_map: dict[str, Broker], default_broker: Broker = None):
        super().__init__(default_broker or list(broker_map.values())[0])
        self._broker_map = broker_map
        # key matchers: "HK." → stock broker, "/" → crypto broker

    def _broker_for(self, symbol: str) -> Broker:
        for prefix, broker in self._broker_map.items():
            if symbol.startswith(prefix):
                return broker
            if prefix == "CRYPTO/" and "/" in symbol:
                return broker
        return self._broker  # default
```

**路由规则**:
| Symbol 模式 | Broker |
|-------------|--------|
| `HK.xxx` | FutuStockBroker（未来） |
| `US.xxx` | FutuStockBroker（未来） |
| `xxx/xxx` (含 `/`) | FutuCryptoBroker |
| `CRYPTO_xxx` | BinanceBroker（备选） |

**测试**: `oms/tests/test_manager.py`
- `test_route_hk_stock`: "HK.00700" → 路由到 stock broker
- `test_route_crypto`: "BTC/USDT" → 路由到 crypto broker
- `test_route_unknown`: 未知格式抛异常

---

### Task 5: collectors/main.py 增加 cryptofutu 支持

**文件**: `collectors/main.py`

**改动**:
- `get_adapter()` 函数增加 `cryptofutu` 分支

```python
def get_adapter(source: str, frequency: str = "1m", host="127.0.0.1", port=11111):
    ...
    if source == "cryptofutu":
        from adapters.crypto_futu_adapter import CryptoFutuAdapter
        return CryptoFutuAdapter(host=host, port=port)
    ...
```

- `get_symbols()` 增加 `cryptofutu` 情况：调用 `fetch_supported_symbols()`

---

## 时间估计

| 任务 | 内容 | 估计时间 |
|------|------|---------|
| 1 | crypto_futu_adapter.py | 15-20 min |
| 2 | backfill.py + cryptofutu | 10 min |
| 3 | crypto_futu_broker.py | 20-25 min |
| 4 | OrderManager 路由 | 10-15 min |
| 5 | collectors/main.py | 5 min |
| **总计** | | **60-75 min** |

---

## 执行方式

同上一轮：Subagent 驱动，每个任务 TDD → review → 下一任务。
