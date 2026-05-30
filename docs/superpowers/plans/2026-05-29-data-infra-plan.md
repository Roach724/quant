# Data Infrastructure Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time 5m K-line WebSocket collection via OpenD, execute historical backfill, and harden the data pipeline with fallback, monitoring, and log rotation.

**Architecture:** New `collectors/ws_collector.py` (systemd daemon) subscribes to OpenD `SubType.K_5M` via `CurKlineHandlerBase`, buffers completed bars, and flushes to GCS via existing `storage.write_bars_to_gcs()`. Historical data uses existing `backfill.py` unchanged. Cron collector gets Futu→yfinance fallback. Quality check and logrotate are deployed as systemd timer/config.

**Tech Stack:** Python 3.12, futu-api, google-cloud-storage, systemd, crontab, logrotate

---

### Task 1: ws_collector.py — WebSocket 5m K-line Collector

**Files:**
- Create: `collectors/ws_collector.py`

- [ ] **Step 1: Create the collector file**

Write `/opt/quant/collectors/ws_collector.py`:

```python
#!/usr/bin/env python3
"""WebSocket 5m K-line collector — systemd daemon.

Subscribes to OpenD SubType.K_5M for HK/US/Crypto across market hours,
buffers completed bars, and flushes to GCS via existing storage.py.

Env vars:
    GCS_BUCKET: GCS bucket name (required)
    OPEND_HOST: OpenD host (default: 127.0.0.1)
    OPEND_PORT: OpenD port (default: 11111)
    FLUSH_INTERVAL_SEC: seconds between flushes (default: 300)
    BUFFER_MAX: max records before flush (default: 50)
    HEARTBEAT_INTERVAL_SEC: seconds between heartbeat logs (default: 1800)
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

from futu import (
    CurKlineHandlerBase, OpenQuoteContext, RET_OK, SubType, Session,
)

# Add parent to path so we can import storage
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from storage import write_bars_to_gcs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ws_collector: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ws_collector")

# ── Config from env ──
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
OPEND_HOST = os.environ.get("OPEND_HOST", "127.0.0.1")
OPEND_PORT = int(os.environ.get("OPEND_PORT", "11111"))
FLUSH_INTERVAL_SEC = int(os.environ.get("FLUSH_INTERVAL_SEC", "300"))
BUFFER_MAX = int(os.environ.get("BUFFER_MAX", "50"))
HEARTBEAT_INTERVAL_SEC = int(os.environ.get("HEARTBEAT_INTERVAL_SEC", "1800"))

# ── Symbol pools ──
HK_SYMBOLS = [
    "HK.00700", "HK.09988", "HK.00941", "HK.00005", "HK.00388",
    "HK.01299", "HK.02318", "HK.01810", "HK.00883", "HK.02382",
    "HK.01093", "HK.03968", "HK.02269", "HK.03690", "HK.09633",
]

US_SYMBOLS = [
    "US.AAPL", "US.MSFT", "US.NVDA", "US.AMZN", "US.META", "US.GOOGL",
    "US.AVGO", "US.TSLA", "US.COST", "US.NFLX", "US.ADBE", "US.AMD",
    "US.PEP", "US.CSCO", "US.INTU", "US.QCOM", "US.TXN", "US.AMGN",
    "US.ISRG", "US.AMAT", "US.CMCSA", "US.HON", "US.BKNG", "US.GILD",
    "US.MU", "US.LRCX", "US.ADI", "US.VRTX", "US.SBUX", "US.MDLZ",
    "US.INTC", "US.KLAC", "US.REGN", "US.SNPS", "US.ADP", "US.PANW",
    "US.CDNS", "US.MELI", "US.ABNB", "US.ADSK", "US.CRWD", "US.FTNT",
    "US.MAR", "US.CTAS", "US.ORLY", "US.CSX", "US.MRVL", "US.NXPI",
    "US.WDAY", "US.ROP",
]

CRYPTO_SYMBOLS = [
    "CC.BTC", "CC.ETH", "CC.SOL", "CC.LTC",
    "CC.XRP", "CC.DOT", "CC.ADA", "CC.AVAX", "CC.LINK", "CC.UNI",
]

# ── Market hour helpers (simplified, UTC-based) ──

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hk_is_open() -> bool:
    """HK market: Mon-Fri, UTC 01:30-04:00 and 05:00-08:00 (lunch break 04:00-05:00)."""
    dt = _now_utc()
    if dt.weekday() >= 5:
        return False
    t = dt.hour * 60 + dt.minute
    return (90 <= t < 240) or (300 <= t < 480)  # 01:30-04:00, 05:00-08:00


def _us_is_open() -> bool:
    """US market: Mon-Fri, UTC 13:30-20:00 (ET 09:30-16:00)."""
    dt = _now_utc()
    if dt.weekday() >= 5:
        return False
    t = dt.hour * 60 + dt.minute
    return 810 <= t <= 1200  # 13:30-20:00


def _crypto_is_open() -> bool:
    """Crypto: always open."""
    return True


# ── K-line handler ──

class BarHandler(CurKlineHandlerBase):
    """Receives completed 5m K-line bars and appends to a shared buffer."""

    def __init__(self, buffer: list, label: str = ""):
        super().__init__()
        self.buffer = buffer
        self.label = label
        self.bar_count = 0

    def on_recv_rsp(self, rsp_pb):
        ret_code, data = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            logger.warning("[%s] Handler error: %s", self.label, data)
            return ret_code, data

        for _, row in data.iterrows():
            self.buffer.append({
                "symbol": row.get("code", ""),
                "timestamp": row["time_key"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(float(row["volume"])),
            })
        self.bar_count += len(data)
        return ret_code, data


# ── Main loop ──

_shutdown = False


def _on_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %d, shutting down...", signum)
    _shutdown = True


def main():
    global _shutdown

    if not GCS_BUCKET:
        logger.critical("GCS_BUCKET env var not set — cannot write data")
        sys.exit(1)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    buffer: list[dict] = []
    handler = BarHandler(buffer, label="5m")

    # Track what we're subscribed to
    current_subscriptions: set[str] = set()
    last_heartbeat = 0.0
    last_market_check = 0.0
    reconnect_backoff = 1

    ctx: OpenQuoteContext | None = None

    logger.info("ws_collector starting: bucket=%s opend=%s:%d flush=%ds buffer_max=%d",
                GCS_BUCKET, OPEND_HOST, OPEND_PORT, FLUSH_INTERVAL_SEC, BUFFER_MAX)

    while not _shutdown:
        now_ts = time.time()

        # ── Reconnect / maintain OpenD connection ──
        if ctx is None:
            try:
                ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
                ctx.set_handler(handler)
                logger.info("Connected to OpenD %s:%d", OPEND_HOST, OPEND_PORT)
                reconnect_backoff = 1
                current_subscriptions.clear()
            except Exception as e:
                logger.error("OpenD connection failed: %s (retry in %ds)", e, reconnect_backoff)
                time.sleep(reconnect_backoff)
                reconnect_backoff = min(reconnect_backoff * 2, 60)
                continue

        # ── Market-hour subscription management (every 60s) ──
        if now_ts - last_market_check > 60:
            last_market_check = now_ts
            desired: set[str] = set()

            if _hk_is_open():
                desired.update(HK_SYMBOLS)
            if _us_is_open():
                desired.update(US_SYMBOLS)
            if _crypto_is_open():
                desired.update(CRYPTO_SYMBOLS)

            # Subscribe new symbols
            to_sub = desired - current_subscriptions
            to_unsub = current_subscriptions - desired

            if to_sub:
                try:
                    ret, msg = ctx.subscribe(
                        list(to_sub), [SubType.K_5M],
                        subscribe_push=True,
                    )
                    if ret == RET_OK:
                        logger.info("Subscribed %d symbols (+)", len(to_sub))
                        current_subscriptions.update(to_sub)
                    else:
                        logger.warning("Subscribe failed: %s", msg)
                except Exception as e:
                    logger.error("Subscribe error: %s — reconnecting", e)
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    ctx = None
                    current_subscriptions.clear()
                    continue

            if to_unsub:
                try:
                    ctx.unsubscribe(list(to_unsub), [SubType.K_5M])
                    logger.info("Unsubscribed %d symbols (-)", len(to_unsub))
                    current_subscriptions.difference_update(to_unsub)
                except Exception:
                    pass

        # ── Flush buffer ──
        if len(buffer) >= BUFFER_MAX or (buffer and now_ts - last_heartbeat > FLUSH_INTERVAL_SEC):
            _flush_buffer(buffer, handler.label)

        # ── Heartbeat ──
        if now_ts - last_heartbeat > HEARTBEAT_INTERVAL_SEC:
            last_heartbeat = now_ts
            logger.info(
                "[HEARTBEAT] subscriptions=%d buffer=%d bars_received=%d",
                len(current_subscriptions), len(buffer), handler.bar_count,
            )

        time.sleep(5)

    # ── Shutdown ──
    logger.info("Shutting down — final flush...")
    _flush_buffer(buffer, handler.label)
    if ctx is not None:
        try:
            ctx.close()
        except Exception:
            pass
    logger.info("ws_collector stopped (bars_total=%d)", handler.bar_count)


def _flush_buffer(buffer: list, label: str):
    """Write buffered bars to GCS via storage.write_bars_to_gcs()."""
    if not buffer:
        return

    import pandas as pd

    df = pd.DataFrame(buffer)
    df["market"] = df["symbol"].apply(
        lambda s: "HK" if s.startswith("HK.") else ("US" if s.startswith("US.") else "CRYPTO")
    )
    df["frequency"] = "5m"

    # Split by market for correct GCS path
    for market in ("HK", "US", "CRYPTO"):
        mkt_df = df[df["market"] == market]
        if mkt_df.empty:
            continue
        try:
            paths = write_bars_to_gcs(
                mkt_df, GCS_BUCKET, market=market.lower(), frequency="5m",
            )
            logger.info("Flushed %d bars (market=%s) → %d GCS paths",
                        len(mkt_df), market, len(paths))
        except Exception as e:
            logger.error("GCS write failed for %s: %s", market, e)
            # Keep data in buffer for retry
            return

    buffer.clear()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
sudo -u quant bash -c 'cd /opt/quant && python3.12 -c "import ast; ast.parse(open(\"collectors/ws_collector.py\").read()); print(\"Syntax OK\")"'
```

Expected: `Syntax OK`

---

### Task 2: systemd Unit for ws_collector

**Files:**
- Create: `/etc/systemd/system/ws-collector.service`

- [ ] **Step 1: Create the unit file**

```bash
sudo tee /etc/systemd/system/ws-collector.service << 'EOF'
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
ExecStart=/usr/bin/python3.12 /opt/quant/collectors/ws_collector.py
Restart=always
RestartSec=10
StandardOutput=append:/home/quant/logs/ws_collector.log
StandardError=append:/home/quant/logs/ws_collector.log

[Install]
WantedBy=multi-user.target
EOF
```

- [ ] **Step 2: Enable and start**

```bash
sudo systemctl daemon-reload
sudo systemctl enable ws-collector
```

**Do NOT start yet** — OpenD needs to be logged in first (Task 9).

---

### Task 3: Futu→yfinance Fallback in main.py

**Files:**
- Modify: `collectors/main.py` (get_adapter function)

- [ ] **Step 1: Add connection check + fallback**

In `collectors/main.py`, replace the `if source == "futu_stock":` block in `get_adapter()`:

Current code (approx lines 43-44):
```python
    if source == "futu_stock":
        return FutuStockAdapter()
```

Replace with:
```python
    if source == "futu_stock":
        try:
            adapter = FutuStockAdapter()
            adapter._get_ctx()  # verify OpenD is reachable and logged in
            return adapter
        except Exception as e:
            logger.warning("FutuStock unavailable (%s), falling back to yfinance", e)
            if frequency == "1d":
                return YFinanceUSAdapter(fallback_adapter=AkshareUSAdapter())
            return YFinanceUSAdapter()
```

- [ ] **Step 2: Run existing tests**

```bash
sudo -u quant bash -c 'cd /opt/quant && python -m pytest collectors/tests/ -v -k "not vcr" 2>&1 | tail -20'
```

Expected: All collector tests pass (8 tests).

---

### Task 4: Quality Check systemd Timer

**Files:**
- Create: `/etc/systemd/system/quality-check.service`
- Create: `/etc/systemd/system/quality-check.timer`

- [ ] **Step 1: Create service unit**

```bash
sudo tee /etc/systemd/system/quality-check.service << 'EOF'
[Unit]
Description=Quant Data Quality Check

[Service]
Type=oneshot
User=quant
WorkingDirectory=/opt/quant/quality
Environment=GCS_BUCKET=deductive-notch-495015-c2-quant-data
ExecStart=/usr/bin/python3.12 /opt/quant/quality/main.py
StandardOutput=append:/home/quant/logs/quality.log
StandardError=append:/home/quant/logs/quality.log
EOF
```

- [ ] **Step 2: Create timer unit**

```bash
sudo tee /etc/systemd/system/quality-check.timer << 'EOF'
[Unit]
Description=Daily quant data quality check

[Timer]
OnCalendar=*-*-* 06:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
```

- [ ] **Step 3: Enable timer**

```bash
sudo systemctl daemon-reload
sudo systemctl enable quality-check.timer
sudo systemctl start quality-check.timer
```

- [ ] **Step 4: Verify timer is scheduled**

```bash
systemctl list-timers --all | grep quality
```

Expected: Shows quality-check.timer with next trigger time.

---

### Task 5: Logrotate Configuration

**Files:**
- Create: `/etc/logrotate.d/quant`

- [ ] **Step 1: Create logrotate config**

```bash
sudo tee /etc/logrotate.d/quant << 'EOF'
/home/quant/logs/*.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
    copytruncate
    dateext
}
EOF
```

- [ ] **Step 2: Test logrotate**

```bash
sudo logrotate -d /etc/logrotate.d/quant 2>&1 | head -10
```

Expected: Shows dry-run output without errors.

---

### Task 6: Extend scripts/status.sh

**Files:**
- Modify: `scripts/status.sh`

- [ ] **Step 1: Add ws_collector and quality timer status**

Add after the OpenD section in `scripts/status.sh`:

```bash
echo ""
echo "--- ws_collector ---"
systemctl is-active ws-collector 2>/dev/null || echo "  NOT RUNNING"
if systemctl is-active --quiet ws-collector 2>/dev/null; then
    tail -3 /home/quant/logs/ws_collector.log 2>/dev/null | while read line; do
        echo "  $line"
    done
fi

echo ""
echo "--- Quality Timer ---"
systemctl list-timers quality-check.timer --no-pager 2>/dev/null | tail -3

echo ""
echo "--- Logs disk usage ---"
du -sh /home/quant/logs/ 2>/dev/null || echo "  (no logs dir)"
```

- [ ] **Step 2: Verify status.sh is executable**

```bash
chmod +x /opt/quant/scripts/status.sh
```

---

### Task 7: Historical Backfill — Daily Bars

**Files:** None (uses existing `backfill.py`)

> ⚠️ **Prerequisite:** OpenD must be logged in. The backfill runs via Futu for US/HK.

- [ ] **Step 1: Crypto daily backfill (fastest, ~30min)**

```bash
sudo -u quant bash -c 'cd /opt/quant && PYTHONPATH=/opt/quant/collectors nohup python3.12 collectors/backfill.py \
  --start 2020-01-01 --end 2026-05-28 \
  --source cryptobinance --all \
  --frequency 1d \
  --gcs-bucket deductive-notch-495015-c2-quant-data \
  --chunk-days 365 \
  > /home/quant/logs/backfill_crypto_1d.log 2>&1 &'
echo "Crypto 1d backfill PID: $!"
```

- [ ] **Step 2: US daily backfill (~2-3h)**

```bash
sudo -u quant bash -c 'cd /opt/quant && PYTHONPATH=/opt/quant/collectors nohup python3.12 collectors/backfill.py \
  --start 2020-01-01 --end 2026-05-28 \
  --source futu_stock --all \
  --frequency 1d \
  --gcs-bucket deductive-notch-495015-c2-quant-data \
  --chunk-days 365 \
  > /home/quant/logs/backfill_us_1d.log 2>&1 &'
echo "US 1d backfill PID: $!"
```

- [ ] **Step 3: HK daily backfill (~4-6h, start LAST)**

```bash
# HK has 475 symbols — use the full pool
sudo -u quant bash -c 'cd /opt/quant && PYTHONPATH=/opt/quant/collectors nohup python3.12 collectors/backfill.py \
  --start 2020-01-01 --end 2026-05-28 \
  --source futu_stock --all \
  --frequency 1d \
  --gcs-bucket deductive-notch-495015-c2-quant-data \
  --chunk-days 365 \
  > /home/quant/logs/backfill_hk_1d.log 2>&1 &'
echo "HK 1d backfill PID: $!"
```

- [ ] **Step 4: Monitor progress**

```bash
# Check status of all three backfills
for log in /home/quant/logs/backfill_*_1d.log; do
  echo "=== $(basename $log) ==="
  tail -3 "$log" 2>/dev/null
  echo ""
done
```

---

### Task 8: Historical Backfill — 5m Bars (Recent)

**Files:** None

> ⚠️ **Prerequisite:** Task 7 daily backfills must be completed. 5m backfill uses Futu quota.

- [ ] **Step 1: Crypto 5m backfill (~1h)**

```bash
sudo -u quant bash -c 'cd /opt/quant && PYTHONPATH=/opt/quant/collectors nohup python3.12 collectors/backfill.py \
  --start 2026-04-28 --end 2026-05-28 \
  --source cryptobinance --all \
  --frequency 5m \
  --gcs-bucket deductive-notch-495015-c2-quant-data \
  --chunk-days 7 \
  > /home/quant/logs/backfill_crypto_5m.log 2>&1 &'
```

- [ ] **Step 2: US 5m backfill — top 50 symbols only (~2-3h)**

```bash
sudo -u quant bash -c 'cd /opt/quant && PYTHONPATH=/opt/quant/collectors nohup python3.12 collectors/backfill.py \
  --start 2026-04-28 --end 2026-05-28 \
  --source futu_stock \
  --symbols AAPL,MSFT,NVDA,AMZN,META,GOOGL,AVGO,TSLA,COST,NFLX,ADBE,AMD,CSCO,INTU,QCOM,TXN,AMGN,ISRG,AMAT,HON,BKNG,GILD,MU,LRCX,ADI,VRTX,SBUX,INTC,KLAC,REGN,SNPS,ADP,PANW,CDNS,MELI,ABNB,ADSK,CRWD,FTNT,MAR,CTAS,ORLY,CSX,MRVL,NXPI,WDAY,ROP,JPM,V \
  --frequency 5m \
  --gcs-bucket deductive-notch-495015-c2-quant-data \
  --chunk-days 7 \
  > /home/quant/logs/backfill_us_5m.log 2>&1 &'
```

---

### Task 9: OpenD Login + ws_collector Startup

**Files:** None (manual action + systemctl)

- [ ] **Step 1: Verify OpenD login status**

```bash
sudo -u quant python3.12 -c "
from futu import OpenQuoteContext, RET_OK
ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
ret, data = ctx.get_global_state()
print('OpenD state:', data)
if ret == RET_OK:
    print('Connected OK')
else:
    print('NEED LOGIN — OpenD GUI requires phone verification')
ctx.close()
"
```

If "NEED LOGIN": User must RDP into the VM, open the OpenD GUI, and complete phone verification.

- [ ] **Step 2: Start ws_collector (after OpenD login confirmed)**

```bash
sudo systemctl start ws-collector
sleep 10
sudo systemctl status ws-collector --no-pager
```

- [ ] **Step 3: Verify bars are flowing**

```bash
# Wait 10 minutes, then check
sleep 600
tail -20 /home/quant/logs/ws_collector.log
```

Expected: Heartbeat logs, subscription counts, and "Flushed X bars" messages.

---

### Task 10: End-to-End Verification

- [ ] **Step 1: Verify real-time data in GCS**

```bash
TODAY=$(date -u +%d)
for mkt in us hk crypto; do
  count=$(gsutil ls "gs://deductive-notch-495015-c2-quant-data/raw/${mkt}/bars/freq=5m/year=2026/month=05/day=${TODAY}/" 2>/dev/null | grep -c ".parquet")
  echo "${mkt}: ${count} parquet files today"
done
```

Expected: All three markets have files for today.

- [ ] **Step 2: Verify Query API serves new data**

```bash
curl -s "http://localhost:8080/api/v1/bars?market=us&symbols=AAPL&start=2026-05-29T00:00:00Z&end=2026-05-30T00:00:00Z&frequency=5m" | python3 -c "
import sys,json
d=json.load(sys.stdin)
bars=[b for b in d.get('bars',[]) if not b.get('symbol','').endswith('.json')]
print(f'Todays bars: {len(bars)}')
if bars:
  print(f'Latest: {bars[-1][\"timestamp\"]} close={bars[-1][\"close\"]}')
"
```

Expected: Returns bars for today with valid OHLCV data.

- [ ] **Step 3: Verify SDK direct read**

```bash
sudo -u quant python3.12 -c "
from quant.direct import bars_direct
df = bars_direct('AAPL', '2026-05-27', '2026-05-30', market='us', frequency='5m')
print(f'Bars: {len(df)}')
print(df.tail(3))
"
```

Expected: Returns bars from GCS with timestamp, open, high, low, close, volume.

- [ ] **Step 4: Run status.sh**

```bash
/opt/quant/scripts/status.sh
```

Expected: OpenD RUNNING, Query API active, ws_collector active, quality timer scheduled.

- [ ] **Step 5: Commit all changes**

```bash
cd /opt/quant
git add collectors/ws_collector.py collectors/main.py scripts/status.sh \
  docs/superpowers/specs/2026-05-29-data-infra-design.md \
  docs/superpowers/plans/2026-05-29-data-infra-plan.md
git commit -m "feat: real-time 5m K-line collector + data pipeline hardening

- ws_collector.py: systemd daemon, OpenD WebSocket SubType.K_5M
- main.py: Futu→yfinance fallback on connection failure
- status.sh: ws_collector + quality timer status
- systemd: ws-collector.service, quality-check.timer
- logrotate: /etc/logrotate.d/quant
- docs: spec + implementation plan"
```
