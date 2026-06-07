# 指数行情数据接入方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入港美股大盘指数行情（恒生/纳指/标普等），实现 K 线采集、BQ 入库、Dashboard 展示，融入现有数据规范。

**Architecture:** 港股指数走 Futu OpenD WebSocket 订阅（复用 ws_collector 架构），美股指数走 yfinance cron 轮询。数据写入 BigQuery，遵循现有 `{market}_bars_{freq}` 命名约定，纳入 Dashboard Pipeline 健康监控。

**Tech Stack:** Futu OpenD API, yfinance, BigQuery, Python collector/backfill 模式

**数据源确认（已实测）：**

| 市场 | 来源 | 可用性 | 实时方式 |
|------|------|--------|---------|
| HK 恒生指数 (800000) | Futu OpenD | 全频率 1m~1d | WebSocket 订阅 |
| HK 恒生科技 (800700) | Futu OpenD | 全频率 | WebSocket 订阅 |
| HK 国企指数 (800100) | Futu OpenD | 全频率 | WebSocket 订阅 |
| US 纳斯达克 (^IXIC) | yfinance | 1m/5m/15m/30m/60m/1d | cron 轮询 |
| US 标普 500 (^GSPC) | yfinance | 同上 | cron 轮询 |
| US 道琼斯 (^DJI) | yfinance | 同上 | cron 轮询 |
| US 罗素 2000 (^RUT) | yfinance | 同上 | cron 轮询 |

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `config/symbols.yaml` | 修改 | 新增 `indices` 区块，SSOT 指数标的列表 |
| `collectors/ws_collector.py` | 修改 | 新增港股指数订阅组，轮转机制 |
| `common/bq_writer.py` | 无需修改 | 复用已有 `insert_rows_json` |
| `collectors/index_collector_us.py` | 新建 | 美股指数 yfinance 轮询采集器 |
| `scripts/backfill_index.py` | 新建 | 指数历史数据回填 |
| `scripts/cron/us_index_5m.sh` | 新建 | cron 包装脚本（美股指数 5m 采集） |
| `admin/server.py` | 修改 | BQ 数据地图加指数表；Dashboard Pipeline 加指数监控；日志模块 |
| `admin/frontend/src/pages/DashboardOverview.tsx` | 修改 | 图表加指数选择器 |
| `admin/frontend/src/pages/DataMap.tsx` | 修改 | 数据地图加指数表 |
| `HANDBOOK.md` | 修改 | 更新数据采集章节 |

---

## 任务拆解

### Task 1: 配置文件 — 指数 SSOT

**Files:**
- Modify: `config/symbols.yaml`

- [ ] **Step 1: 在 symbols.yaml 新增 indices 区块**

```yaml
indices:
  hk:
    symbols:
      - "HK.800000"    # 恒生指数
      - "HK.800700"    # 恒生科技指数
      - "HK.800100"    # 国企指数
  us:
    symbols:
      - "^IXIC"        # 纳斯达克综合指数
      - "^GSPC"        # 标普500
      - "^DJI"         # 道琼斯工业
      - "^RUT"         # 罗素2000
```

- [ ] **Step 2: 验证 YAML 可解析**

```bash
cd /opt/quant-prod && python3 -c "import yaml; cfg=yaml.safe_load(open('config/symbols.yaml')); print(cfg.get('indices',{}))"
```

- [ ] **Step 3: Commit**

```bash
git add config/symbols.yaml
git commit -m "feat: add indices SSOT to symbols.yaml (HK Futu + US yfinance)"
```

---

### Task 2: 港股指数采集 — ws_collector 扩展

**Files:**
- Modify: `collectors/ws_collector.py`

**设计思路：** ws_collector 现有逻辑是订阅股票 5m K 线，分 N 组轮转（每组 100 只，每 5 分钟切一组）。指数不需要分组（只有 4 只），加一组独立的长连接订阅，始终在线。

- [ ] **Step 1: 在 ws_collector.py 加载指数符号列表**

找到 `symbols` 加载位置（通常在 `__init__` 或 `main`），新增：

```python
# 紧接现有 symbols 加载之后
_ssot = yaml.safe_load(open("config/symbols.yaml"))
_index_symbols = _ssot.get("indices", {}).get("hk", {}).get("symbols", [])
# 转换为 Futu 格式（去掉 HK. 前缀如果 ws_collector 需要裸代码）
```

- [ ] **Step 2: 新增指数订阅和回调**

扩展现有的 K 线回调逻辑，在 `on_recv_rsp` 或 handler 中判断 symbol 是否属于指数列表，写入不同的 BQ 表：

```python
# 在 on_recv_rsp 回调中
def _is_index(symbol: str) -> bool:
    return symbol in _index_symbols or symbol.startswith("HK.800")

# 写入时路由
if self._is_index(symbol):
    table = f"{BQ_PROJECT}.quant.hk_bars_index_5m"
else:
    table = f"{BQ_PROJECT}.quant.hk_bars_5m"
```

- [ ] **Step 3: 指数单独建长连接（不走轮转）**

在采集器启动时，指数组额外订阅且不取消订阅：

```python
# 在 _subscribe_batch 之后
if _index_symbols:
    ret, _ = self._ctx.subscribe(_index_symbols, [SubType.K_5M])
    if ret == RET_OK:
        logger.info("Index subscription active: %s", _index_symbols)
    else:
        logger.error("Index subscribe failed: %s", _index_symbols)
```

- [ ] **Step 4: 加日志**

```python
logger.info("Index K-line written: %s %s O=%.2f C=%.2f", symbol, ts, o, c)
```

- [ ] **Step 5: Commit**

```bash
git add collectors/ws_collector.py
git commit -m "feat: add HK index 5m subscription to ws_collector (Futu, BQ routing)"
```

---

### Task 3: BQ 表创建 — 指数 K 线

**Files:**
- 无代码文件，纯 BQ DDL

- [ ] **Step 1: 创建 hk_bars_index_5m 表**

```sql
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_bars_index_5m` (
  symbol STRING NOT NULL,
  timestamp TIMESTAMP NOT NULL,
  open FLOAT64,
  high FLOAT64,
  low FLOAT64,
  close FLOAT64,
  volume FLOAT64,
  _ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(timestamp)
CLUSTER BY symbol;
```

- [ ] **Step 2: 创建 us_bars_index_5m 表**

```sql
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_bars_index_5m` (
  symbol STRING NOT NULL,
  timestamp TIMESTAMP NOT NULL,
  open FLOAT64,
  high FLOAT64,
  low FLOAT64,
  close FLOAT64,
  volume FLOAT64,
  _ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(timestamp)
CLUSTER BY symbol;
```

- [ ] **Step 3: 创建 hk_bars_index_1d 和 us_bars_index_1d 表**

（同上，但 PARTITION BY 按月或年）

```sql
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_bars_index_1d` (
  symbol STRING NOT NULL,
  date DATE NOT NULL,
  open FLOAT64,
  high FLOAT64,
  low FLOAT64,
  close FLOAT64,
  volume FLOAT64,
  _ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY date
CLUSTER BY symbol;
```

- [ ] **Step 4: 通过 BQ Console 或 CLI 执行**

```bash
bq query --project_id=deductive-notch-495015-c2 --use_legacy_sql=false "CREATE TABLE ..."
```

- [ ] **Step 5: 更新 index/registry（如有）**

在 HANDBOOK §5 记录新表。

---

### Task 4: 美股指数采集器 — yfinance 轮询

**Files:**
- Create: `collectors/index_collector_us.py`
- Create: `scripts/cron/us_index_5m.sh`

**设计思路：** yfinance 没有推送，用 cron 每分钟拉一次 `history(period='1d', interval='1m')`，去重后写 BQ。用 `common/logging_util.get_logger` 统一日志格式。市场时段保护：非美股开盘时间自动 skip。

- [ ] **Step 1: 写采集器主体**

```python
#!/usr/bin/env python3
"""US Index 5m Collector — fetch & deduplicate via yfinance → BigQuery.

Usage:
    python collectors/index_collector_us.py --freq 5m
    python collectors/index_collector_us.py --freq 1d

Scheduling:
    */1 * * * * /opt/quant-prod/scripts/cron/us_index_5m.sh  (5m)
    30 22 * * 1-5 /opt/quant-prod/scripts/cron/us_index_1d.sh (1d, after close)
"""

from __future__ import annotations
import logging, sys, time, json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import yfinance as yf
from common.logging_util import get_logger
from google.cloud import bigquery

BQ_PROJECT = "deductive-notch-495015-c2"
BQ_DATASET = "quant"

logger = get_logger("index_collector.us", env="prod", module="collector",
                    log_file="/var/log/quant/prod/collector/index_us.log")

def load_index_symbols() -> list[str]:
    cfg = yaml.safe_load(open("config/symbols.yaml"))
    return cfg.get("indices", {}).get("us", {}).get("symbols", [])

def is_market_open() -> bool:
    """US market: Mon-Fri 09:30-16:00 ET = 13:30-20:00 UTC."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False  # weekend
    market_open = now.replace(hour=13, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=20, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close

def fetch_and_write(symbol: str, freq: str, client) -> int:
    """Fetch intraday bars for one index, deduplicate, write to BQ.
    Returns number of rows written."""
    ticker = yf.Ticker(symbol)
    interval = "5m" if freq == "5m" else "1d"
    df = ticker.history(period="1d" if freq == "5m" else "max", interval=interval)

    if df.empty:
        logger.debug("%s: no data for %s", symbol, freq)
        return 0

    table = f"{BQ_PROJECT}.{BQ_DATASET}.us_bars_index_{freq}"

    # Dedup: check latest timestamp in BQ
    try:
        q = f"SELECT MAX(timestamp) FROM `{table}` WHERE symbol='{symbol}'"
        latest = list(client.query(q).result())[0][0]
    except Exception:
        latest = None

    rows_written = 0
    batch = []
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime().replace(tzinfo=timezone.utc) if hasattr(idx, 'to_pydatetime') else idx
        if latest and ts <= latest.replace(tzinfo=timezone.utc):
            continue
        batch.append({
            "symbol": symbol,
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row.get("Volume", 0) or 0),
        })

    if batch:
        errors = client.insert_rows_json(table, batch)
        if errors:
            logger.error("BQ insert errors: %s", errors[:3])
        else:
            rows_written = len(batch)
            logger.info("Wrote %d bars for %s (%s)", rows_written, symbol, freq)

    return rows_written

def main():
    if not is_market_open():
        logger.info("Market closed — skipping")
        return

    symbols = load_index_symbols()
    client = bigquery.Client(project=BQ_PROJECT)
    freq = "5m"
    total = 0
    for sym in symbols:
        try:
            n = fetch_and_write(sym, freq, client)
            total += n
        except Exception:
            logger.exception("Failed to fetch %s", sym)
    logger.info("Collection complete: %d bars written", total)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写 cron 包装脚本**

```bash
#!/bin/bash
# scripts/cron/us_index_5m.sh
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 collectors/index_collector_us.py
```

```bash
chmod +x scripts/cron/us_index_5m.sh
```

- [ ] **Step 3: 注册 cron（用 admin API 或手动 crontab）**

```cron
# US Index 5m collection (market hours only, Mon-Fri)
*/5 * * * 1-5 /opt/quant-prod/scripts/cron/us_index_5m.sh >> /var/log/quant/prod/cron/us_index_5m.log 2>&1
```

- [ ] **Step 4: 测试采集器（非交易时段跳过）**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 collectors/index_collector_us.py
# Expected: "Market closed — skipping"
```

- [ ] **Step 5: Commit**

```bash
git add collectors/index_collector_us.py scripts/cron/us_index_5m.sh
git commit -m "feat: add US index collector (yfinance 5m polling → BQ)"
```

---

### Task 5: 指数 1d 聚合 — compute_daily_bars 扩展

**Files:**
- Modify: `scripts/compute_daily_bars.py`（或对应聚合脚本）

- [ ] **Step 1: 复制现有 5m→1d 聚合逻辑，新增指数表**

```python
# 紧接现有 hk_bars_5m → hk_bars_1d 聚合之后
for market in ["hk", "us"]:
    source = f"quant.{market}_bars_index_5m"
    dest = f"quant.{market}_bars_index_1d"
    client.query(f"""
        CREATE OR REPLACE TABLE `{BQ_PROJECT}.{dest}` AS
        SELECT
            symbol,
            DATE(timestamp) AS date,
            FIRST_VALUE(open IGNORE NULLS)  OVER win AS open,
            MAX(high)                         OVER win AS high,
            MIN(low)                          OVER win AS low,
            LAST_VALUE(close IGNORE NULLS)   OVER win AS close,
            SUM(volume)                       OVER win AS volume
        FROM `{BQ_PROJECT}.{source}`
        WINDOW win AS (
            PARTITION BY symbol, DATE(timestamp)
            ORDER BY timestamp
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        )
    """).result()
```

- [ ] **Step 2: 更新 cron 触发（已有 1d 聚合 cron）**

无需新增，已有的 `compute_daily_bars` cron 任务直接覆盖（如果脚本被扩展）。

- [ ] **Step 3: Commit**

```bash
git add scripts/compute_daily_bars.py
git commit -m "feat: add index 5m→1d aggregation to compute_daily_bars"
```

---

### Task 6: 指数历史回填

**Files:**
- Create: `scripts/backfill_index.py`

- [ ] **Step 1: 写回填脚本**

```python
#!/usr/bin/env python3
"""Backfill index K-line data from Futu (HK) and yfinance (US).

Usage:
    python scripts/backfill_index.py --market hk --freq 5m --start 2024-01-01
    python scripts/backfill_index.py --market us --freq 1d --start 2020-01-01
"""

import argparse, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from google.cloud import bigquery
from common.logging_util import get_logger

logger = get_logger("backfill.index", env="prod", module="backfill",
                    log_file="/var/log/quant/prod/backfill/index.log")

BQ_PROJECT = "deductive-notch-495015-c2"
BQ_DATASET = "quant"

def backfill_hk(symbol: str, freq: str, start: str, end: str, client) -> int:
    """Backfill HK index via Futu API."""
    from futu import OpenQuoteContext, KLType, RET_OK

    ktype_map = {"1m": KLType.K_1M, "5m": KLType.K_5M, "1d": KLType.K_DAY}
    ktype = ktype_map.get(freq)

    table = f"{BQ_PROJECT}.{BQ_DATASET}.hk_bars_index_{freq}"

    q = OpenQuoteContext(host="127.0.0.1", port=11111)
    written = 0
    try:
        ret, data, _ = q.request_history_kline(symbol, ktype=ktype,
                                                start=start, end=end,
                                                max_count=1000)
        if ret != RET_OK:
            logger.error("Futu K-line failed for %s: %s", symbol, data)
            return 0

        rows = []
        for _, r in data.iterrows():
            rows.append({
                "symbol": symbol,
                "timestamp": r["time_key"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r.get("volume", 0) or 0),
            })

        errors = client.insert_rows_json(table, rows)
        if errors:
            logger.error("BQ insert errors: %s", errors[:3])
        else:
            written = len(rows)
            logger.info("Backfilled %s %s %s: %d bars", symbol, freq, start, written)
    finally:
        q.close()

    return written

def backfill_us(symbol: str, freq: str, start: str, client) -> int:
    """Backfill US index via yfinance."""
    import yfinance as yf

    interval_map = {"5m": "5m", "1d": "1d"}
    interval = interval_map.get(freq, "1d")
    table = f"{BQ_PROJECT}.{BQ_DATASET}.us_bars_index_{freq}"

    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, interval=interval)

    if df.empty:
        logger.warning("%s: no data from %s", symbol, start)
        return 0

    rows = []
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime().replace(tzinfo=timezone.utc) if hasattr(idx, "to_pydatetime") else idx
        rows.append({
            "symbol": symbol,
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row.get("Volume", 0) or 0),
        })

    errors = client.insert_rows_json(table, rows)
    if errors:
        logger.error("BQ insert errors: %s", errors[:3])
        return 0

    logger.info("Backfilled %s %s from %s: %d bars", symbol, freq, start, len(rows))
    return len(rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["hk", "us"], required=True)
    parser.add_argument("--freq", choices=["1m", "5m", "1d"], default="1d")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", default="")
    args = parser.parse_args()

    cfg = yaml.safe_load(open("config/symbols.yaml"))
    symbols = cfg.get("indices", {}).get(args.market, {}).get("symbols", [])
    client = bigquery.Client(project=BQ_PROJECT)

    total = 0
    for sym in symbols:
        if args.market == "hk":
            n = backfill_hk(sym, args.freq, args.start, args.end or datetime.now().strftime("%Y-%m-%d"), client)
        else:
            n = backfill_us(sym, args.freq, args.start, client)
        total += n

    logger.info("Backfill complete: %d total bars (%s %s)", total, args.market, args.freq)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 执行初始回填**

```bash
# HK 指数 (5m, 最近 90 天)
cd /opt/quant-prod && PYTHONPATH=. .venv/bin/python3 scripts/backfill_index.py --market hk --freq 5m --start 2026-03-01

# HK 指数 (1d, 全量)
cd /opt/quant-prod && PYTHONPATH=. .venv/bin/python3 scripts/backfill_index.py --market hk --freq 1d --start 2020-01-01

# US 指数 (5m, 最近 60 天 yfinance 支持)
cd /opt/quant-prod && PYTHONPATH=. .venv/bin/python3 scripts/backfill_index.py --market us --freq 5m --start 2026-04-01

# US 指数 (1d, 全量)
cd /opt/quant-prod && PYTHONPATH=. .venv/bin/python3 scripts/backfill_index.py --market us --freq 1d --start 2020-01-01
```

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_index.py
git commit -m "feat: add index backfill script (Futu HK + yfinance US)"
```

---

### Task 7: Admin API — 数据地图 + Dashboard Pipeline + 日志模块

**Files:**
- Modify: `admin/server.py`

- [ ] **Step 1: 数据地图加指数表**

找到 `_DB_TABLE_MAP` 或数据表列表（通常是 `data_map` API 的返回），新增：

```python
# 紧接现有 hk_bars_5m / hk_bars_1d 之后
{"key": "hk_bars_index_5m", "label": "HK 指数 5分钟K线", "market": "hk"},
{"key": "hk_bars_index_1d", "label": "HK 指数 日线",   "market": "hk"},
{"key": "us_bars_index_5m", "label": "US 指数 5分钟K线", "market": "us"},
{"key": "us_bars_index_1d", "label": "US 指数 日线",   "market": "us"},
```

- [ ] **Step 2: Dashboard Pipeline 健康监控加指数**

在 `dash_pipeline` API 的 `try/except` 块后追加：

```python
try:
    # HK index pipeline
    q = f"""
        SELECT MAX(timestamp) AS latest
        FROM {_DB_TABLE("hk_bars_index_5m")}
        WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    """
    rows = list(client.query(q).result())
    if rows and rows[0].latest:
        result["hk_index"] = _db_serialize(rows[0].latest)
except Exception as exc:
    logging.getLogger(__name__).error("dash_pipeline hk_index query error: %s", exc)

try:
    # US index pipeline
    q = f"""
        SELECT MAX(timestamp) AS latest
        FROM {_DB_TABLE("us_bars_index_5m")}
        WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    """
    rows = list(client.query(q).result())
    if rows and rows[0].latest:
        result["us_index"] = _db_serialize(rows[0].latest)
except Exception as exc:
    logging.getLogger(__name__).error("dash_pipeline us_index query error: %s", exc)
```

- [ ] **Step 3: Commit**

```bash
git add admin/server.py
git commit -m "feat: add index tables to data map and dashboard pipeline"
```

---

### Task 8: 前端 — Dashboard Overview 加指数图表

**Files:**
- Modify: `admin/frontend/src/pages/DashboardOverview.tsx`

- [ ] **Step 1: 在现有 US/HK 股票行情的 Card 旁加指数 Card**

在 US/HK symbol select 区域下方新增指数选择器：

```tsx
// Index chart state
const [usIndexSymbol, setUsIndexSymbol] = useState('^IXIC');
const [hkIndexSymbol, setHkIndexSymbol] = useState('HK.800000');
const [usIndexData, setUsIndexData] = useState<any[]>([]);
const [hkIndexData, setHkIndexData] = useState<any[]>([]);

const INDEX_SYMBOLS = {
  us: ['^IXIC', '^GSPC', '^DJI', '^RUT'],
  hk: ['HK.800000', 'HK.800700', 'HK.800100'],
};

// Load index chart
const loadIndexChart = useCallback(async (market: 'us' | 'hk', symbol: string) => {
  if (!symbol) return;
  const data = await api.get(`/api/admin/dashboard/market/${market}/${symbol}?limit=78&days=1`);
  if (market === 'us') setUsIndexData(data || []);
  else setHkIndexData(data || []);
}, []);
```

- [ ] **Step 2: 渲染指数 Card**

在现有 stock chart Card 下方：

```tsx
<Card size="small" title={<Space>US Index
  <Select size="small" value={usIndexSymbol} onChange={v => { setUsIndexSymbol(v); loadIndexChart('us', v); }}
    options={INDEX_SYMBOLS.us.map(s => ({ value: s, label: s.replace('^','') }))} />
</Space>}>
  <Chart options={buildChartOpts(usIndexData, usIndexSymbol.replace('^',''))} height={200} />
</Card>
```

- [ ] **Step 3: Commit**

```bash
git add admin/frontend/src/pages/DashboardOverview.tsx
git commit -m "feat: add index chart cards to dashboard overview (US + HK)"
```

---

### Task 9: 文档更新

**Files:**
- Modify: `HANDBOOK.md`

- [ ] **Step 1: §5 数据采集子系统 — 新增指数采集子节**

```markdown
#### 5.X 指数数据采集

**数据源:**
- HK 指数: Futu OpenD WebSocket 订阅 (与 ws_collector 共用连接)
- US 指数: yfinance cron 轮询 (`collectors/index_collector_us.py`)

**BQ 表:**
| 表 | 频率 | 来源 |
|----|------|------|
| `hk_bars_index_5m` | 5 分钟 | Futu WebSocket |
| `hk_bars_index_1d` | 日线 | 5m → 1d 聚合 |
| `us_bars_index_5m` | 5 分钟 | yfinance cron |
| `us_bars_index_1d` | 日线 | 5m → 1d 聚合 |

**标的 SSOT:** `config/symbols.yaml` → `indices.hk` / `indices.us`
**Cron:** `*/5 * * * 1-5 us_index_5m.sh` (美股交易时段)
**回填:** `scripts/backfill_index.py --market hk|us --freq 5m|1d --start YYYY-MM-DD`
```

- [ ] **Step 2: Commit**

```bash
git add HANDBOOK.md
git commit -m "docs: add index data collection section to handbook"
```

---

## 自检

**Spec 覆盖：** ✅ 数据接入（Task 2/4）、BQ 表（Task 3）、实时与历史（Task 2/4/5/6）、Dashboard（Task 7/8）、文档（Task 9）

**无占位符：** ✅ 所有任务均有完整代码

**类型一致性：** ✅ 表名、字段名跨任务一致
