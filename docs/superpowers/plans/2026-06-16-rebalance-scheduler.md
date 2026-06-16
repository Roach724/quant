# RebalanceScheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone `RebalanceScheduler` pure state-machine that decides TRADE/SKIP/WAITING per bar, decoupling rebalance logic from K-line frequency.

**Architecture:** Single-file module `trading/scheduler.py` with `Decision` enum + `RebalanceScheduler` class. Integrated into runner.py call chain. Persists state independently to JSON. Pure logic, no external deps.

**Tech Stack:** Python 3.12, enum, json, pathlib

---

### File Structure

| File | Action | Purpose |
|------|--------|---------|
| `trading/scheduler.py` | **Create** | Decision enum + RebalanceScheduler class |
| `tests/test_scheduler.py` | **Create** | Unit tests for all scenarios |
| `trading/runner.py` | **Modify** | Accept scheduler, gate `_execute_signals` via `on_bar()` |
| `trading/run.py` | **Modify** | Parse `freq`/`lookback_bars` from YAML, create scheduler, pass to runner |

---

### Task 1: Create `trading/scheduler.py` — Decision enum and RebalanceScheduler class

**Files:**
- Create: `trading/scheduler.py`

- [ ] **Step 1: Write the Decision enum and RebalanceSkeleton class**

```python
"""RebalanceScheduler — strategy rebalancing scheduler.

Determines whether a strategy should trade on a given bar,
based on K-line frequency, lookback window, and rebalance interval.
Pure state machine — no external dependencies.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Decision(str, Enum):
    TRADE = "trade"        # A rebalance window has been reached — execute signals
    SKIP = "skip"          # Within a rebalance window — do nothing
    WAITING = "waiting"    # Lookback phase — not enough data yet


class RebalanceScheduler:
    """Per-strategy rebalance scheduler.

    Tracks the current bar count (excluding lookback) and the last bar
    on which a rebalance was executed.  Answers one question per bar:
    should the strategy generate and execute signals right now?

    Usage::

        sched = RebalanceScheduler(
            freq_minutes=5,
            lookback_bars=100,
            rebalance_every=5,
            state_path="/var/data/trading/state/strategy_1_scheduler.json",
        )

        for bar_data in live_bars:
            dec = sched.on_bar()
            if dec == Decision.TRADE:
                signals = strategy.on_bar(...)
                execute(signals)
                sched.write()  # persist after successful trade
            # bars accumulate regardless of decision
    """

    def __init__(
        self,
        freq_minutes: int,
        lookback_bars: int,
        rebalance_every: int,
        state_path: str,
    ):
        if freq_minutes < 1:
            raise ValueError(f"freq_minutes must be >= 1, got {freq_minutes}")
        if lookback_bars < 0:
            raise ValueError(f"lookback_bars must be >= 0, got {lookback_bars}")
        if rebalance_every < 1:
            raise ValueError(f"rebalance_every must be >= 1, got {rebalance_every}")

        self.freq_minutes = freq_minutes
        self.lookback_bars = lookback_bars
        self.rebalance_every = rebalance_every
        self.state_path = Path(state_path)

        # Derived
        self.rebalance_interval_minutes = freq_minutes * rebalance_every

        # State
        self.bar_count = 0       # live bar count (excludes lookback)
        self.last_rebalance_bar: Optional[int] = None  # bar index of last trade

        # Warn on short rebalance intervals
        if self.rebalance_interval_minutes < 60:
            logger.warning(
                "Rebalance interval is only %d minutes (freq=%dm × every=%d). "
                "This may trigger broker frequent-trading restrictions.",
                self.rebalance_interval_minutes,
                freq_minutes,
                rebalance_every,
            )

    # ── Core API ────────────────────────────────────────────────────────

    def on_bar(self) -> Decision:
        """Call once per bar. Returns whether to trade now.

        During lookback phase returns WAITING.
        After lookback completes, the first bar triggers TRADE.
        Subsequent bars use the rebalance_every gap from last_rebalance_bar.
        """
        self.bar_count += 1

        # ── Lookback phase: wait for enough data ──
        if self.bar_count < self.lookback_bars:
            logger.debug("bar %d < lookback %d → WAITING", self.bar_count, self.lookback_bars)
            return Decision.WAITING

        # ── First trade after lookback ──
        if self.last_rebalance_bar is None:
            self.last_rebalance_bar = self.bar_count
            logger.info("First trade at bar %d (lookback %d complete)", self.bar_count, self.lookback_bars)
            return Decision.TRADE

        # ── Normal interval check ──
        gap = self.bar_count - self.last_rebalance_bar
        if gap >= self.rebalance_every:
            self.last_rebalance_bar = self.bar_count
            logger.info(
                "Trade at bar %d (gap=%d >= every=%d)",
                self.bar_count, gap, self.rebalance_every,
            )
            return Decision.TRADE

        logger.debug("bar %d gap=%d < every=%d → SKIP", self.bar_count, gap, self.rebalance_every)
        return Decision.SKIP

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self) -> dict:
        """Return serializable state dict."""
        return {
            "bar_count": self.bar_count,
            "last_rebalance_bar": self.last_rebalance_bar,
        }

    def load_state(self, bar_count: int, last_rebalance_bar: int | None) -> None:
        """Restore state from persisted values.

        Call this after creating the scheduler to resume from a
        previous run.  After loading, the next call to ``on_bar()``
        will follow the restart logic.
        """
        self.bar_count = bar_count
        self.last_rebalance_bar = last_rebalance_bar
        logger.info(
            "Loaded state: bar_count=%d last_rebalance_bar=%s",
            self.bar_count, self.last_rebalance_bar,
        )

    def write(self) -> None:
        """Persist current state to disk."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.save(), indent=2))

    @classmethod
    def from_file(cls, file_path: str, **kwargs) -> "RebalanceScheduler":
        """Create scheduler from a saved state file.

        If the file exists, state is loaded.  Otherwise a fresh
        scheduler is returned.
        """
        path = Path(file_path)
        if path.exists():
            state = json.loads(path.read_text())
            sched = cls(state_path=file_path, **kwargs)
            sched.load_state(state["bar_count"], state["last_rebalance_bar"])
            return sched
        return cls(state_path=file_path, **kwargs)
```

- [ ] **Step 2: Verify imports and syntax**

```bash
cd /opt/quant && python3 -c "from trading.scheduler import RebalanceScheduler, Decision; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add trading/scheduler.py
git commit -m "feat: RebalanceScheduler — 纯状态机调仓决策器"
```

---

### Task 2: Write unit tests for all scenarios

**Files:**
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for RebalanceScheduler."""
import json
import tempfile
from pathlib import Path

import pytest

from trading.scheduler import RebalanceScheduler, Decision


def _make_sched(lookback=10, every=5, freq=5, path=None):
    if path is None:
        path = str(Path(tempfile.mkdtemp()) / "test_scheduler.json")
    return RebalanceScheduler(
        freq_minutes=freq,
        lookback_bars=lookback,
        rebalance_every=every,
        state_path=path,
    )


class TestLookbackPhase:

    def test_returns_waiting_during_lookback(self):
        sched = _make_sched(lookback=10, every=5)
        for _ in range(9):
            assert sched.on_bar() == Decision.WAITING

    def test_returns_trade_on_first_bar_after_lookback(self):
        sched = _make_sched(lookback=10, every=5)
        for _ in range(9):
            sched.on_bar()
        # 10th call → bar 10, which equals lookback → first TRADE
        assert sched.on_bar() == Decision.TRADE


class TestNormalInterval:

    def test_skips_within_rebalance_window(self):
        sched = _make_sched(lookback=10, every=5)
        # Pass lookback and get first trade
        for _ in range(10):
            sched.on_bar()
        # bar=10 → first TRADE done, last_rebalance=10
        for _ in range(4):
            assert sched.on_bar() == Decision.SKIP
        # bar=15 → TRADE
        assert sched.on_bar() == Decision.TRADE

    def test_trade_at_every_n_bars(self):
        sched = _make_sched(lookback=10, every=5)
        decisions = []
        for _ in range(25):
            decisions.append(sched.on_bar())
        # bar 0-9: 10 WAITING, bar 10: TRADE, bar 11-14: 4 SKIP,
        # bar 15: TRADE, bar 16-19: 4 SKIP, bar 20: TRADE
        expected = (
            [Decision.WAITING] * 10
            + [Decision.TRADE]
            + [Decision.SKIP] * 4
            + [Decision.TRADE]
            + [Decision.SKIP] * 4
            + [Decision.TRADE]
        )
        assert decisions[:25] == expected


class TestPersistence:

    def test_save_and_write(self):
        path = str(Path(tempfile.mkdtemp()) / "sched.json")
        sched = _make_sched(path=path)
        for _ in range(15):
            sched.on_bar()  # bar 10 → TRADE, bar 15 → TRADE
        sched.write()

        saved = json.loads(Path(path).read_text())
        assert saved["bar_count"] == 15

    def test_from_file_fresh(self):
        path = str(Path(tempfile.mkdtemp()) / "fresh.json")
        sched = RebalanceScheduler.from_file(
            file_path=path, freq_minutes=5, lookback_bars=10, rebalance_every=5,
        )
        # Fresh → no state loaded
        assert sched.bar_count == 0
        assert sched.last_rebalance_bar is None

    def test_from_file_restore(self):
        path = str(Path(tempfile.mkdtemp()) / "restore.json")
        # First create and save
        sched1 = _make_sched(path=path)
        for _ in range(12):
            sched1.on_bar()  # bar 10 → TRADE
        sched1.write()

        # Restore
        sched2 = RebalanceScheduler.from_file(
            file_path=path, freq_minutes=5, lookback_bars=10, rebalance_every=5,
        )
        assert sched2.bar_count == 12


class TestRestartScenarios:

    # Scenario A: gap < rebalance_every → SKIP
    def test_restart_short_gap_skips(self):
        sched = _make_sched()
        sched.load_state(bar_count=107, last_rebalance_bar=105)
        # 107 - 105 = 2 < 5 → SKIP
        assert sched.on_bar() == Decision.SKIP  # bar becomes 108
        assert sched.on_bar() == Decision.SKIP  # 109
        assert sched.on_bar() == Decision.TRADE  # 110
        assert sched.last_rebalance_bar == 110

    # Scenario B: gap >= rebalance_every → TRADE once, then normal
    def test_restart_long_gap_trades_once(self):
        sched = _make_sched()
        sched.load_state(bar_count=112, last_rebalance_bar=105)
        # 112 - 105 = 7 >= 5 → TRADE
        assert sched.on_bar() == Decision.TRADE  # bar becomes 113
        assert sched.last_rebalance_bar == 113
        # Then normal: bar 114-117 → SKIP, bar 118 → TRADE
        for _ in range(4):
            assert sched.on_bar() == Decision.SKIP
        assert sched.on_bar() == Decision.TRADE

    # Scenario C: gap == rebalance_every → TRADE once
    def test_restart_exact_window_equals(self):
        sched = _make_sched()
        sched.load_state(bar_count=110, last_rebalance_bar=105)
        # 110 - 105 = 5 >= 5 → TRADE
        assert sched.on_bar() == Decision.TRADE  # bar becomes 111
        assert sched.last_rebalance_bar == 111

    # Edge: load then continue from lookback
    def test_load_state_before_lookback(self):
        sched = _make_sched(lookback=10, every=5)
        sched.load_state(bar_count=5, last_rebalance_bar=None)
        # Still in lookback
        for _ in range(4):
            assert sched.on_bar() == Decision.WAITING
        # bar 10 → first TRADE
        assert sched.on_bar() == Decision.TRADE


class TestValidation:

    def test_negative_lookback_raises(self):
        with pytest.raises(ValueError):
            _make_sched(lookback=-1)

    def test_zero_rebalance_every_raises(self):
        with pytest.raises(ValueError):
            _make_sched(every=0)

    def test_logs_warning_for_short_interval(self, caplog):
        sched = _make_sched(freq=5, every=2)
        assert sched.rebalance_interval_minutes == 10
        # Warning should be logged on init
        assert any("only" in rec.message and "minutes" in rec.message for rec in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail (scheduler module doesn't exist yet or partial)**

```bash
cd /opt/quant && python3 -m pytest tests/test_scheduler.py -v
```

Expected: Should pass all tests since scheduler already exists from Task 1.

- [ ] **Step 3: Run tests and fix any failures**

```bash
cd /opt/quant && python3 -m pytest tests/test_scheduler.py -v -s
```

Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_scheduler.py
git commit -m "test: RebalanceScheduler 全覆盖单元测试"
```

---

### Task 3: Integrate scheduler into `trading/runner.py`

**Files:**
- Modify: `trading/runner.py`

- [ ] **Step 1: Add scheduler parameter to TradingRunner**

Replace the `__init__` signature and body to accept a scheduler:

```python
# In TradingRunner.__init__, add scheduler parameter
class TradingRunner:
    def __init__(
        self,
        broker,
        capital: CapitalManager,
        state: TradingStateManager,
        bridge: SignalBridge,
        strategies: list[TSModel],
        market: str = "us",
        bar_interval: int = 60,
        reconcile_every: int = 10,
        scheduler: RebalanceScheduler | None = None,  # ← NEW
    ):
        self.broker = broker
        self.capital = capital
        self.state = state
        self.bridge = bridge
        self.market = market
        self.bar_interval = bar_interval
        self.reconcile_every = reconcile_every
        self.scheduler = scheduler  # ← NEW
        self._strategies: dict[int, TSModel] = {s.id: s for s in strategies}
        self._adapters: dict[int, StrategyAdapter] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._stop_events: dict[int, threading.Event] = {}
        self._running = False
```

Add the import at the top:
```python
from trading.scheduler import RebalanceScheduler, Decision
```

- [ ] **Step 2: Gate signal execution via scheduler in `_run_single_day`**

Find this block in `_run_single_day`:
```python
                if adapter._strategy and _ctx["ctx"]:
                    _ctx["ctx"] = ctx
                    signals = adapter.generate_signals(ctx, _bar_count - 1, strategy_id)
                    if signals:
                        logger.info(
                            "Strategy %d: %d signals at bar %d",
                            strategy_id,
                            len(signals),
                            _bar_count,
                        )
                        self._execute_signals(signals, bar_data)
```

Replace with:
```python
                if adapter._strategy and _ctx["ctx"]:
                    _ctx["ctx"] = ctx
                    dec = self.scheduler.on_bar() if self.scheduler else Decision.TRADE
                    if dec == Decision.WAITING:
                        return
                    if dec == Decision.SKIP:
                        return
                    signals = adapter.generate_signals(ctx, _bar_count - 1, strategy_id)
                    if signals:
                        logger.info(
                            "Strategy %d: %d signals at bar %d",
                            strategy_id,
                            len(signals),
                            _bar_count,
                        )
                        self._execute_signals(signals, bar_data)
                        if self.scheduler:
                            self.scheduler.write()
```

- [ ] **Step 3: Apply same gating in `_run_multi_day`**

Find the same pattern in `_run_multi_day`'s `_on_bar` and apply the identical change.

- [ ] **Step 4: Verify no broken references**

```bash
cd /opt/quant && python3 -c "from trading.runner import TradingRunner; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add trading/runner.py
git commit -m "feat: runner 集成 RebalanceScheduler — gate 调仓决策"
```

---

### Task 4: Wire scheduler creation in `trading/run.py`

**Files:**
- Modify: `trading/run.py`

- [ ] **Step 1: Parse freq/lookback_bars from config YAML**

Find the config parsing section in `run_strategy()`:
```python
    cfg = yaml.safe_load(strat.config_yaml) or {}
    market = strat.market or cfg.get("live", {}).get("market", "us")
```

Add after:
```python
    live_cfg = cfg.get("live", {}) or {}
    freq_str = live_cfg.get("freq", "5m")   # "5m", "1m", "60m", "1d"
    lookback_bars = live_cfg.get("lookback_bars", 0)
    # Parse freq to minutes
    freq_minutes = _parse_freq(freq_str)
```

- [ ] **Step 2: Add freq parsing helper**

Add before `run_strategy()`:
```python
def _parse_freq(freq_str: str) -> int:
    """Parse frequency string like '5m', '1h', '1d' to minutes."""
    freq_str = freq_str.strip().lower()
    if freq_str.endswith("m") and not freq_str.endswith("hm"):
        return int(freq_str[:-1])
    if freq_str.endswith("h"):
        return int(freq_str[:-1]) * 60
    if freq_str.endswith("d"):
        return int(freq_str[:-1]) * 1440
    raise ValueError(f"Unsupported freq format: {freq_str}")
```

- [ ] **Step 3: Create scheduler and pass to runner**

Find this section:
```python
    bridge = SignalBridge(broker, capital, market=market)

    runner = TradingRunner(
        broker=broker,
        capital=capital,
        state=state_mgr,
        bridge=bridge,
        strategies=[strat],
        market=market,
    )
```

Replace with:
```python
    bridge = SignalBridge(broker, capital, market=market)

    # Create scheduler if lookback configured
    from trading.scheduler import RebalanceScheduler
    scheduler = None
    if lookback_bars > 0:
        scheduler = RebalanceScheduler.from_file(
            file_path=str(pid_dir.parent / "state" / f"strategy_{strategy_id}_scheduler.json"),
            freq_minutes=freq_minutes,
            lookback_bars=lookback_bars,
            rebalance_every=int(cfg.get("live", {}).get("rebalance_every", 1)),
        )

    runner = TradingRunner(
        broker=broker,
        capital=capital,
        state=state_mgr,
        bridge=bridge,
        strategies=[strat],
        market=market,
        scheduler=scheduler,
    )
```

- [ ] **Step 4: Verify import chain**

```bash
cd /opt/quant && python3 -c "from trading.run import _parse_freq; assert _parse_freq('5m') == 5; assert _parse_freq('1h') == 60; assert _parse_freq('1d') == 1440; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add trading/run.py
git commit -m "feat: run.py 解析 freq/lookback 并创建 scheduler"
```

---

### Task 5: Integration smoke test

**Files:**
- (no new files — manual verification)

- [ ] **Step 1: Verify full import chain**

```bash
cd /opt/quant && python3 -c "
from trading.scheduler import RebalanceScheduler, Decision
from trading.runner import TradingRunner
from trading.run import _parse_freq
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 2: Quick end-to-end scheduler scenario**

```bash
cd /opt/quant && python3 -c "
from trading.scheduler import RebalanceScheduler
import tempfile, os

p = os.path.join(tempfile.mkdtemp(), 's.json')
s = RebalanceScheduler(freq_minutes=5, lookback_bars=3, rebalance_every=2, state_path=p)
# 3 lookback bars → WAITING
assert s.on_bar() == 'waiting'
assert s.on_bar() == 'waiting'
assert s.on_bar() == 'waiting'
# bar 4 = first TRADE
assert s.on_bar() == 'trade'
s.write()
# skip bar 5
assert s.on_bar() == 'skip'
# bar 6 = TRADE
assert s.on_bar() == 'trade'
print('E2E smoke test PASSED')
"
```

Expected: `E2E smoke test PASSED`

- [ ] **Step 3: Commit (if any residual changes)**

```bash
git add -A && git diff --cached --stat
git commit -m "chore: 集成 smoketest 通过" --allow-empty
```
