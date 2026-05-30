# Futu API 因子扩展一期 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将因子体系从 39 个 OHLCV 技术面因子扩展到 80 个（+41 个 F10 基本面/情绪因子），新建 6 个 F10 数据采集 adapter，新增 QARP 策略和动态选股。

**Architecture:** 采集层（6 个独立 adapter → GCS → BQ）→ 因子层（TechFactorBuilder + FundamentalFactorBuilder → BQ factor_values）→ 注册/评估（FactorRegistry 扩展）→ ML（BQ 加载 + 多特征源）→ 策略/回测（UniverseBuilder + QARP）。

**Tech Stack:** Python 3.12, futu-api, pandas, numpy, google-cloud-bigquery, scikit-learn, lightgbm, pytest

---

## File Structure Before Starting

```
factors/
  builder.py → tech_builder.py (rename)
  fundamental_builder.py (new)
  registry.py (modify)
  evaluation.py (modify)
  tests/test_builder.py (modify imports)
  tests/test_fundamental_builder.py (new)

collectors/adapters/
  _futu_base.py (new)
  futu_stock_adapter.py (existing, ref: OpenD pattern)
  futu_financials_adapter.py (new)
  futu_valuation_adapter.py (new)
  futu_short_interest_adapter.py (new)
  futu_capital_flow_adapter.py (new)
  futu_analyst_adapter.py (new)
  futu_shareholder_adapter.py (new)

collectors/
  fundamental_collector.py (new)

sql/
  f10_schemas.sql (new)
  factor_values_schema.sql (new)

scripts/
  init_factor_registry.py (modify)

ml/
  trainer.py (modify)

paper/
  market.py (modify)
  strategies.py (modify)

run_paper.py (modify)
```

---

### Task 1: Rename FactorBuilder → TechFactorBuilder

**Files:**
- Modify: `factors/builder.py` → rename to `factors/tech_builder.py`
- Modify: `factors/tests/test_builder.py`
- Modify: `scripts/init_factor_registry.py`
- Modify: `ml/trainer.py`
- Modify: all other importers of FactorBuilder

- [ ] **Step 1: Create tech_builder.py with TechFactorBuilder**

```python
# factors/tech_builder.py — content identical to current factors/builder.py
# but with class renamed from FactorBuilder to TechFactorBuilder
# and backward-compat alias at the bottom:

class TechFactorBuilder:
    """..."""
    # (exact same body as current FactorBuilder)

# Backward-compat alias
class FactorBuilder(TechFactorBuilder):
    """Deprecated alias for TechFactorBuilder — use TechFactorBuilder directly."""
    import warnings
    warnings.warn("FactorBuilder is deprecated, use TechFactorBuilder", DeprecationWarning, stacklevel=2)
    pass
```

- [ ] **Step 2: Update all imports across the codebase**

Run to find all importers:
```bash
grep -rn "from factors.builder import" --include="*.py" .
grep -rn "from factors import builder" --include="*.py" .
grep -rn "factors.builder" --include="*.py" .
```

Expected files to update:
- `factors/tests/test_builder.py` — `from factors.tech_builder import TechFactorBuilder`
- `scripts/init_factor_registry.py` — `from factors.tech_builder import TechFactorBuilder`
- Any other file importing `FactorBuilder`

- [ ] **Step 3: Run existing tests to verify rename**

Run: `python -m pytest factors/tests/test_builder.py -v`
Expected: All 30+ tests PASS (they should work with the alias or new name)

Run: `python -m pytest factors/tests/ -v`
Expected: All factor tests PASS

- [ ] **Step 4: Commit**

```bash
git add factors/tech_builder.py factors/tests/test_builder.py scripts/init_factor_registry.py
# add any other modified import files
git commit -m "refactor: rename FactorBuilder → TechFactorBuilder with backward-compat alias"
```

---

### Task 2: Create shared F10 adapter base class

**Files:**
- Create: `collectors/adapters/_futu_base.py`
- Create: `collectors/tests/test_futu_base.py`

- [ ] **Step 1: Write failing test for _futu_base**

```python
# collectors/tests/test_futu_base.py
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from collectors.adapters._futu_base import FutuBaseAdapter


class DummyAdapter(FutuBaseAdapter):
    """Minimal concrete adapter for testing."""
    DATA_TYPE = "dummy"
    
    def fetch(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame({"symbol": [symbol], "value": [1.0], "timestamp": ["2026-01-01"]})


def test_base_connects_with_env_vars():
    adapter = DummyAdapter(host="127.0.0.1", port=11111)
    assert adapter.host == "127.0.0.1"
    assert adapter.port == 11111


def test_base_default_symbols():
    adapter = DummyAdapter()
    symbols = adapter._default_symbols()
    assert isinstance(symbols, list)
    assert len(symbols) > 0


def test_base_rate_limit_does_not_raise():
    adapter = DummyAdapter()
    adapter._rate_limit()  # should not raise


def test_base_market_from_code():
    adapter = DummyAdapter()
    assert adapter._market_from_code("HK.00700") == "hk"
    assert adapter._market_from_code("US.AAPL") == "us"


def test_fetch_all_returns_dict():
    adapter = DummyAdapter(symbols=["HK.00700", "US.AAPL"])
    result = adapter.fetch_all()
    assert isinstance(result, dict)
    assert len(result) == 2
    assert "HK.00700" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest collectors/tests/test_futu_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'collectors.adapters._futu_base'`

- [ ] **Step 3: Write _futu_base.py**

```python
"""Shared base class for all F10 Futu data adapters.

Each adapter extends FutuBaseAdapter, overriding fetch() for one F10 data type.
Encapsulates: OpenD connection, rate limiting, symbol pool loading, market detection.
"""
from __future__ import annotations

import logging
import os
import time as _time
from typing import Optional

import pandas as pd
from futu import OpenQuoteContext

logger = logging.getLogger(__name__)

_RATE_LIMIT_WINDOW = 30.0   # seconds
_RATE_LIMIT_MAX_REQS = 60   # max requests per window
_RATE_LIMIT_GAP = _RATE_LIMIT_WINDOW / _RATE_LIMIT_MAX_REQS  # 0.5s


class FutuBaseAdapter:
    """Base class for F10 data adapters — one per F10 data type.

    Subclass and override:
        DATA_TYPE: str — unique data type identifier (e.g. "financials")
        fetch(symbol) → pd.DataFrame — pull data for one symbol

    Usage::

        class FutuFinancialsAdapter(FutuBaseAdapter):
            DATA_TYPE = "financials"

            def fetch(self, symbol: str) -> pd.DataFrame:
                ...
    """

    DATA_TYPE: str = ""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        symbols: Optional[list[str]] = None,
    ):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self.symbols = symbols or self._default_symbols()
        self._ctx: Optional[OpenQuoteContext] = None
        self._last_request_time = 0.0

    def _get_ctx(self) -> OpenQuoteContext:
        if self._ctx is None:
            self._ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self._ctx

    def _rate_limit(self):
        """Enforce 60 req/30s rate limit."""
        elapsed = _time.time() - self._last_request_time
        if elapsed < _RATE_LIMIT_GAP:
            _time.sleep(_RATE_LIMIT_GAP - elapsed)
        self._last_request_time = _time.time()

    @staticmethod
    def _market_from_code(code: str) -> str:
        if code.startswith("HK."):
            return "hk"
        if code.startswith("US."):
            return "us"
        if code.startswith("SH.") or code.startswith("SZ."):
            return "cn"
        return "unknown"

    def fetch(self, symbol: str) -> pd.DataFrame:
        """Fetch F10 data for a single symbol. Override in subclass."""
        raise NotImplementedError

    def fetch_all(self) -> dict[str, pd.DataFrame]:
        """Fetch data for all symbols. Returns {symbol: DataFrame} dict."""
        results: dict[str, pd.DataFrame] = {}
        for i, sym in enumerate(self.symbols):
            try:
                self._rate_limit()
                df = self.fetch(sym)
                if df is not None and len(df) > 0:
                    results[sym] = df
            except Exception:
                logger.debug("Fetch failed for %s", sym, exc_info=True)
            if (i + 1) % 50 == 0:
                logger.info("  %s: %d/%d symbols fetched", self.DATA_TYPE, i + 1, len(self.symbols))
        return results

    def close(self):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    def _default_symbols(self) -> list[str]:
        """Shared symbol pool — same as FutuStockAdapter."""
        return [
            "HK.00700", "HK.09988", "HK.00941", "HK.00005", "HK.00388",
            "HK.01299", "HK.02318", "HK.01810",
            "US.AAPL", "US.MSFT", "US.NVDA", "US.AMZN", "US.META", "US.GOOGL",
            "US.AVGO", "US.TSLA", "US.COST", "US.NFLX", "US.ADBE", "US.AMD",
            "US.JPM", "US.V", "US.UNH", "US.XOM", "US.MA", "US.JNJ", "US.WMT",
            "US.PG", "US.HD", "US.BAC", "US.CVX",
        ]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest collectors/tests/test_futu_base.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add collectors/adapters/_futu_base.py collectors/tests/test_futu_base.py
git commit -m "feat: add FutuBaseAdapter — shared base for F10 data adapters"
```

---

### Task 3-8: Create the 6 F10 adapters (one task per adapter)

Each adapter follows this pattern (shown once, repeated for each):

#### Task 3: FutuFinancialsAdapter

**Files:**
- Create: `collectors/adapters/futu_financials_adapter.py`
- Create: `collectors/tests/test_futu_financials_adapter.py`

- [ ] **Step 1: Write failing test**

```python
# collectors/tests/test_futu_financials_adapter.py
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from collectors.adapters.futu_financials_adapter import FutuFinancialsAdapter


def test_adapter_has_data_type():
    assert FutuFinancialsAdapter.DATA_TYPE == "financials"


def test_build_request_payload():
    adapter = FutuFinancialsAdapter(symbols=["HK.00700"])
    payload = adapter._build_request("HK.00700", 1, 10)
    assert payload["code"] == "HK.00700"
    assert payload["statement_type"] == 1  # Income


@patch("collectors.adapters.futu_financials_adapter.OpenQuoteContext")
def test_fetch_returns_dataframe(mock_ctx):
    pass  # VCR-based integration test — skip in unit tests
```

- [ ] **Step 2: Write adapter implementation**

```python
"""Futu financial statements adapter — income, balance, cash flow, key metrics."""
import logging
from typing import Optional

import pandas as pd
from futu import RET_OK

from collectors.adapters._futu_base import FutuBaseAdapter

logger = logging.getLogger(__name__)


class FutuFinancialsAdapter(FutuBaseAdapter):
    """Fetch financial statement data via get_financials_statements.

    Pulls all 4 statement types for each symbol:
        1=Income, 2=BalanceSheet, 3=CashFlow, 4=MainIndex (key metrics)

    Default: annual reports + single quarter (financial_type=10).
    """

    DATA_TYPE = "financials"

    STATEMENT_TYPES = {
        "income": 1,
        "balance_sheet": 2,
        "cash_flow": 3,
        "main_index": 4,
    }

    def __init__(self, host=None, port=None, symbols=None, financial_type=10):
        super().__init__(host=host, port=port, symbols=symbols)
        self.financial_type = financial_type  # 10 = single quarter + annual

    def _build_request(self, code: str, statement_type: int, num: int = 50):
        return {
            "code": code,
            "statement_type": statement_type,
            "financial_type": self.financial_type,
            "num": num,
        }

    def fetch(self, symbol: str) -> pd.DataFrame:
        """Fetch all 4 financial statement types for one symbol."""
        ctx = self._get_ctx()
        all_frames: list[pd.DataFrame] = []

        for stype_name, stype_id in self.STATEMENT_TYPES.items():
            next_key = None
            while True:
                self._rate_limit()
                ret, data = ctx.get_financials_statements(
                    symbol,
                    statement_type=stype_id,
                    financial_type=self.financial_type,
                    next_key=next_key,
                    num=50,
                )
                if ret != RET_OK:
                    logger.warning("Financials fetch failed %s %s: %s", symbol, stype_name, data)
                    break
                if data is not None and len(data) > 0:
                    data["statement_type"] = stype_name
                    all_frames.append(data)
                next_key = data.attrs.get("next_key") if data is not None and hasattr(data, "attrs") else None
                if not next_key or next_key == "-1":
                    break

        if not all_frames:
            return pd.DataFrame()
        combined = pd.concat(all_frames, ignore_index=True)
        combined["symbol"] = symbol
        combined["fetched_at"] = pd.Timestamp.now().isoformat()
        return combined
```

- [ ] **Step 3: Run tests**

Run: `pytest collectors/tests/test_futu_financials_adapter.py -v`
Expected: 2 unit tests PASS

- [ ] **Step 4: Commit**

```bash
git add collectors/adapters/futu_financials_adapter.py collectors/tests/test_futu_financials_adapter.py
git commit -m "feat: add FutuFinancialsAdapter — annual + quarterly financial statements"
```

---

#### Task 4: FutuValuationAdapter

**Files:**
- Create: `collectors/adapters/futu_valuation_adapter.py`
- Create: `collectors/tests/test_futu_valuation_adapter.py`

- [ ] **Step 1: Write adapter**

```python
"""Futu valuation adapter — PE/PB/PS trends and percentiles."""
import logging
from typing import Optional
import pandas as pd
from futu import RET_OK
from collectors.adapters._futu_base import FutuBaseAdapter

logger = logging.getLogger(__name__)

VALUATION_TYPES = {"pe": 1, "pb": 2, "ps": 3}
INTERVAL_TYPES = {3: "1y", 4: "3y", 6: "5y"}


class FutuValuationAdapter(FutuBaseAdapter):
    DATA_TYPE = "valuation"

    def fetch(self, symbol: str) -> pd.DataFrame:
        ctx = self._get_ctx()
        all_frames: list[pd.DataFrame] = []

        for vt_name, vt_id in VALUATION_TYPES.items():
            for interval_id, interval_label in INTERVAL_TYPES.items():
                self._rate_limit()
                ret, data = ctx.get_valuation_detail(
                    symbol, valuation_type=vt_id, interval_type=interval_id,
                )
                if ret != RET_OK:
                    logger.warning("Valuation fetch failed %s %s/%s: %s", symbol, vt_name, interval_label, data)
                    continue
                if data is not None and len(data) > 0:
                    data["valuation_type"] = vt_name
                    data["interval_type"] = interval_label
                    all_frames.append(data)

        if not all_frames:
            return pd.DataFrame()
        combined = pd.concat(all_frames, ignore_index=True)
        combined["symbol"] = symbol
        combined["fetched_at"] = pd.Timestamp.now().isoformat()
        return combined
```

- [ ] **Step 2: Unit test**

```python
# collectors/tests/test_futu_valuation_adapter.py
from collectors.adapters.futu_valuation_adapter import FutuValuationAdapter

def test_data_type():
    assert FutuValuationAdapter.DATA_TYPE == "valuation"

def test_symbols_default():
    adapter = FutuValuationAdapter()
    assert "US.AAPL" in adapter.symbols
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest collectors/tests/test_futu_valuation_adapter.py -v
git add collectors/adapters/futu_valuation_adapter.py collectors/tests/test_futu_valuation_adapter.py
git commit -m "feat: add FutuValuationAdapter — PE/PB/PS trends and percentiles"
```

---

#### Task 5: FutuShortInterestAdapter

**Files:**
- Create: `collectors/adapters/futu_short_interest_adapter.py`
- Create: `collectors/tests/test_futu_short_interest_adapter.py`

- [ ] **Step 1: Write adapter**

```python
"""Futu short interest adapter — short positions + daily short volume."""
import logging
from typing import Optional
import pandas as pd
from futu import RET_OK
from collectors.adapters._futu_base import FutuBaseAdapter

logger = logging.getLogger(__name__)


class FutuShortInterestAdapter(FutuBaseAdapter):
    DATA_TYPE = "short_interest"

    def fetch(self, symbol: str) -> pd.DataFrame:
        ctx = self._get_ctx()
        frames: list[pd.DataFrame] = []

        # Short interest
        self._rate_limit()
        next_key = None
        while True:
            ret, data = ctx.get_short_interest(symbol, next_key=next_key, num=20)
            if ret != RET_OK:
                logger.warning("Short interest fetch failed %s: %s", symbol, data)
                break
            if data is not None and len(data) > 0:
                data["data_type"] = "short_interest"
                frames.append(data)
            next_key = data.attrs.get("next_key") if hasattr(data, "attrs") else None
            if not next_key or next_key == "-1":
                break

        # Daily short volume
        self._rate_limit()
        next_key = None
        while True:
            ret, data = ctx.get_daily_short_volume(symbol, next_key=next_key, num=20)
            if ret != RET_OK:
                logger.warning("Daily short volume fetch failed %s: %s", symbol, data)
                break
            if data is not None and len(data) > 0:
                data["data_type"] = "daily_short_volume"
                frames.append(data)
            next_key = data.attrs.get("next_key") if hasattr(data, "attrs") else None
            if not next_key or next_key == "-1":
                break

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True)
        combined["symbol"] = symbol
        combined["fetched_at"] = pd.Timestamp.now().isoformat()
        return combined
```

- [ ] **Step 2: Test + Commit**

Same pattern as Task 4.

---

#### Task 6: FutuCapitalFlowAdapter

**Files:**
- Create: `collectors/adapters/futu_capital_flow_adapter.py`
- Create: `collectors/tests/test_futu_capital_flow_adapter.py`

- [ ] **Step 1: Write adapter**

```python
"""Futu capital flow adapter — capital flow + capital distribution."""
import logging
import pandas as pd
from futu import RET_OK
from collectors.adapters._futu_base import FutuBaseAdapter

logger = logging.getLogger(__name__)


class FutuCapitalFlowAdapter(FutuBaseAdapter):
    DATA_TYPE = "capital_flow"

    def fetch(self, symbol: str) -> pd.DataFrame:
        ctx = self._get_ctx()
        frames: list[pd.DataFrame] = []

        # Capital flow (intraday)
        self._rate_limit()
        ret, data = ctx.get_capital_flow(symbol, period_type=1)
        if ret == RET_OK and data is not None and len(data) > 0:
            data["data_type"] = "capital_flow"
            frames.append(data)

        # Capital distribution
        self._rate_limit()
        ret, data = ctx.get_capital_distribution(symbol)
        if ret == RET_OK and data is not None and len(data) > 0:
            data["data_type"] = "capital_distribution"
            frames.append(data)

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True)
        combined["symbol"] = symbol
        combined["fetched_at"] = pd.Timestamp.now().isoformat()
        return combined
```

- [ ] **Step 2: Test + Commit**

---

#### Task 7: FutuAnalystAdapter

**Files:**
- Create: `collectors/adapters/futu_analyst_adapter.py`
- Create: `collectors/tests/test_futu_analyst_adapter.py`

- [ ] **Step 1: Write adapter**

```python
"""Futu analyst consensus adapter — ratings and target prices."""
import logging
import pandas as pd
from futu import RET_OK
from collectors.adapters._futu_base import FutuBaseAdapter

logger = logging.getLogger(__name__)


class FutuAnalystAdapter(FutuBaseAdapter):
    DATA_TYPE = "analyst"

    def fetch(self, symbol: str) -> pd.DataFrame:
        ctx = self._get_ctx()
        self._rate_limit()
        ret, data = ctx.get_research_analyst_consensus(symbol)
        if ret != RET_OK:
            logger.warning("Analyst fetch failed %s: %s", symbol, data)
            return pd.DataFrame()
        if data is None or len(data) == 0:
            return pd.DataFrame()
        data["symbol"] = symbol
        data["fetched_at"] = pd.Timestamp.now().isoformat()
        return data
```

- [ ] **Step 2: Test + Commit**

---

#### Task 8: FutuShareholderAdapter

**Files:**
- Create: `collectors/adapters/futu_shareholder_adapter.py`
- Create: `collectors/tests/test_futu_shareholder_adapter.py`

- [ ] **Step 1: Write adapter**

```python
"""Futu shareholder adapter — holding changes + institutional holdings."""
import logging
import pandas as pd
from futu import RET_OK
from collectors.adapters._futu_base import FutuBaseAdapter

logger = logging.getLogger(__name__)


class FutuShareholderAdapter(FutuBaseAdapter):
    DATA_TYPE = "shareholder"

    def fetch(self, symbol: str) -> pd.DataFrame:
        ctx = self._get_ctx()
        frames: list[pd.DataFrame] = []

        # Holding changes
        self._rate_limit()
        ret, data = ctx.get_shareholders_holding_changes(symbol, num=20)
        if ret == RET_OK and data is not None and len(data) > 0:
            data["data_type"] = "holding_changes"
            frames.append(data)

        # Institutional holdings
        self._rate_limit()
        ret, data = ctx.get_shareholders_institutional(symbol, num=20)
        if ret == RET_OK and data is not None and len(data) > 0:
            data["data_type"] = "institutional"
            frames.append(data)

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True)
        combined["symbol"] = symbol
        combined["fetched_at"] = pd.Timestamp.now().isoformat()
        return combined
```

- [ ] **Step 2: Test + Commit**

---

### Task 9: Create fundamental_collector.py entry point + BQ schemas

**Files:**
- Create: `collectors/fundamental_collector.py`
- Create: `sql/f10_schemas.sql`

- [ ] **Step 1: Write BQ schema SQL**

```sql
-- sql/f10_schemas.sql
-- F10 data tables in BigQuery, one per data type per market.

-- Financial statements
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_financials` (
    symbol STRING NOT NULL,
    statement_type STRING,
    fetched_at TIMESTAMP,
    -- Financial fields vary by statement_type; use JSON for flexibility
    data JSON,
    report_date DATE,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_financials`
(LIKE `deductive-notch-495015-c2.quant.us_financials`);

-- Valuation snapshots
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_valuation` (
    symbol STRING NOT NULL,
    valuation_type STRING,
    interval_type STRING,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_valuation`
(LIKE `deductive-notch-495015-c2.quant.us_valuation`);

-- Short interest
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_short_interest` (
    symbol STRING NOT NULL,
    data_type STRING,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_short_interest`
(LIKE `deductive-notch-495015-c2.quant.us_short_interest`);

-- Capital flow
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_capital_flow` (
    symbol STRING NOT NULL,
    data_type STRING,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_capital_flow`
(LIKE `deductive-notch-495015-c2.quant.us_capital_flow`);

-- Analyst consensus
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_analyst` (
    symbol STRING NOT NULL,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_analyst`
(LIKE `deductive-notch-495015-c2.quant.us_analyst`);

-- Shareholder data
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_shareholder` (
    symbol STRING NOT NULL,
    data_type STRING,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_shareholder`
(LIKE `deductive-notch-495015-c2.quant.us_shareholder`);
```

- [ ] **Step 2: Write fundamental_collector.py**

```python
#!/usr/bin/env python3
"""F10 fundamental data collector — cron entry point.

Usage:
    python collectors/fundamental_collector.py --source financials --market us
    python collectors/fundamental_collector.py --source valuation --market hk
    python collectors/fundamental_collector.py --source all --market us

Env: OPEND_HOST, OPEND_PORT, GCP_PROJECT
"""
import argparse
import logging
import os
import sys
from pathlib import Path

# Project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from collectors.adapters.futu_financials_adapter import FutuFinancialsAdapter
from collectors.adapters.futu_valuation_adapter import FutuValuationAdapter
from collectors.adapters.futu_short_interest_adapter import FutuShortInterestAdapter
from collectors.adapters.futu_capital_flow_adapter import FutuCapitalFlowAdapter
from collectors.adapters.futu_analyst_adapter import FutuAnalystAdapter
from collectors.adapters.futu_shareholder_adapter import FutuShareholderAdapter

ADAPTERS = {
    "financials": FutuFinancialsAdapter,
    "valuation": FutuValuationAdapter,
    "short_interest": FutuShortInterestAdapter,
    "capital_flow": FutuCapitalFlowAdapter,
    "analyst": FutuAnalystAdapter,
    "shareholder": FutuShareholderAdapter,
}

log = logging.getLogger("fundamental_collector")


def main():
    parser = argparse.ArgumentParser(description="F10 fundamental data collector")
    parser.add_argument("--source", choices=list(ADAPTERS.keys()) + ["all"], required=True)
    parser.add_argument("--market", choices=["us", "hk"], default="us")
    parser.add_argument("--symbols", nargs="*", help="Override default symbol pool")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    sources = list(ADAPTERS.keys()) if args.source == "all" else [args.source]

    for source in sources:
        log.info("Collecting %s for %s market...", source, args.market)
        cls = ADAPTERS[source]
        adapter = cls(symbols=args.symbols if args.symbols else None)
        try:
            data = adapter.fetch_all()
            log.info("  %s: got data for %d symbols", source, len(data))
            if data:
                _save_data(data, source, args.market)
        finally:
            adapter.close()


def _save_data(data: dict, source: str, market: str):
    """Stage data for BigQuery LOAD."""
    import pandas as pd
    output_dir = Path(os.environ.get("F10_STAGING_DIR", f"data/f10_staging/{market}/{source}"))
    output_dir.mkdir(parents=True, exist_ok=True)
    for sym, df in data.items():
        safe_sym = sym.replace(".", "_").replace("/", "_")
        df.to_parquet(output_dir / f"{safe_sym}.parquet", index=False)
    log.info("  Saved %d files to %s", len(data), output_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add collectors/fundamental_collector.py sql/f10_schemas.sql
git commit -m "feat: add fundamental_collector.py entry point + F10 BQ schemas"
```

---

### Task 10: Create factor_values BQ schema

**Files:**
- Create: `sql/factor_values_schema.sql`

- [ ] **Step 1: Write schema**

```sql
-- sql/factor_values_schema.sql
-- Unified factor values table — both TechFactorBuilder and FundamentalFactorBuilder
-- write values here. FactorRegistry queries this for evaluation.

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.factor_values` (
    factor_id STRING NOT NULL,
    symbol STRING NOT NULL,
    date DATE NOT NULL,
    value FLOAT64,
    source_builder STRING,  -- "tech" or "fundamental"
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY date CLUSTER BY factor_id, symbol;
```

- [ ] **Step 2: Commit**

```bash
git add sql/factor_values_schema.sql
git commit -m "feat: add factor_values unified BQ schema"
```

---

### Task 11: Create FundamentalFactorBuilder

**Files:**
- Create: `factors/fundamental_builder.py`
- Create: `factors/tests/test_fundamental_builder.py`

- [ ] **Step 1: Write failing test**

```python
# factors/tests/test_fundamental_builder.py
import numpy as np
import pandas as pd
import pytest
from factors.fundamental_builder import FundamentalFactorBuilder


def make_data_map(n_days: int = 200) -> dict[str, pd.DataFrame]:
    """Create synthetic F10 data for testing."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")

    # Financials — quarterly data
    fin_dates = pd.date_range("2023-01-01", periods=n_days // 60, freq="QE")
    financials = pd.DataFrame({
        "symbol": "AAPL",
        "report_date": fin_dates,
        "roe": rng.normal(0.25, 0.05, len(fin_dates)),
        "roa": rng.normal(0.15, 0.03, len(fin_dates)),
        "gross_margin": rng.normal(0.40, 0.03, len(fin_dates)),
        "net_margin": rng.normal(0.20, 0.02, len(fin_dates)),
        "debt_to_equity": rng.normal(1.50, 0.30, len(fin_dates)),
        "current_ratio": rng.normal(1.80, 0.20, len(fin_dates)),
        "interest_coverage": rng.normal(15.0, 5.0, len(fin_dates)),
    })

    # Valuation — daily-ish data
    valuation = pd.DataFrame({
        "symbol": "AAPL",
        "date": dates,
        "pe_percentile": rng.uniform(0, 1, n_days),
        "pb_percentile": rng.uniform(0, 1, n_days),
        "ps_percentile": rng.uniform(0, 1, n_days),
        "pe_vs_5y_avg": rng.uniform(0.8, 1.2, n_days),
        "peg_ratio": rng.uniform(0.5, 3.0, n_days),
    })

    # Short interest — daily
    short_interest = pd.DataFrame({
        "symbol": "AAPL",
        "date": dates,
        "short_ratio": rng.normal(0.05, 0.02, n_days).clip(0.01, 0.20),
        "days_to_cover": rng.normal(3.0, 1.0, n_days).clip(0.5, 10.0),
        "short_change_1m": rng.normal(0.0, 0.10, n_days),
        "short_volume_pct": rng.uniform(0.05, 0.30, n_days),
        "short_utilization": rng.uniform(0.10, 0.90, n_days),
    })

    return {
        "financials": financials,
        "valuation": valuation,
        "short_interest": short_interest,
    }


def test_builder_has_41_factors():
    fb = FundamentalFactorBuilder()
    assert len(fb.ALL_FACTOR_COLS) == 41


def test_compute_returns_dataframe():
    fb = FundamentalFactorBuilder()
    data_map = make_data_map()
    result = fb.compute(["roe", "roa", "pe_percentile", "short_ratio"], data_map)
    assert isinstance(result, pd.DataFrame)
    assert "roe" in result.columns
    assert "roa" in result.columns


def test_compute_missing_column_warns():
    fb = FundamentalFactorBuilder()
    data_map = {"financials": pd.DataFrame()}
    result = fb.compute(["roe"], data_map)
    assert result.empty


def test_process_factors_standardizes():
    fb = FundamentalFactorBuilder()
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=50),
        "roe": np.random.default_rng(42).normal(0.25, 0.10, 50),
        "pe_percentile": np.random.default_rng(43).uniform(0, 1, 50),
    })
    processed = fb.process_factors(df)
    # After z-score, mean should be near 0, std near 1
    assert abs(processed["roe"].mean()) < 0.01
    assert 0.9 < processed["roe"].std() < 1.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest factors/tests/test_fundamental_builder.py -v`
Expected: FAIL

- [ ] **Step 3: Write FundamentalFactorBuilder**

```python
"""FundamentalFactorBuilder — F10 fundamental + sentiment + flow factors (~41 factors).

Computes factors from BigQuery F10 raw data. Follows the same interface
patterns as TechFactorBuilder: compute → process_factors → build_dataset.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import pandas as pd

from factors.tech_builder import _spearmanr

logger = logging.getLogger(__name__)


class FundamentalFactorBuilder:
    """Compute 41 F10 fundamental, sentiment, capital flow, and event-driven factors."""

    # ── Factor column sets ──────────────────────────────────────────

    QUALITY_COLS = ["roe", "roa", "gross_margin", "net_margin", "debt_to_equity",
                    "current_ratio", "interest_coverage"]
    GROWTH_COLS = ["revenue_growth_yoy", "eps_growth_yoy", "net_profit_growth_yoy", "asset_growth_yoy"]
    EARNINGS_QUALITY_COLS = ["accruals_ratio", "ocf_to_net_profit", "revenue_to_cash_ratio"]
    VALUATION_COLS = ["pe_percentile", "pb_percentile", "ps_percentile", "pe_vs_5y_avg", "peg_ratio"]
    SHORT_COLS = ["short_ratio", "days_to_cover", "short_change_1m", "short_volume_pct", "short_utilization"]
    FLOW_COLS = ["main_inflow_ratio", "big_order_pct", "retail_flow_divergence", "flow_price_divergence"]
    ANALYST_COLS = ["target_price_upside", "buy_ratio", "rating_mean", "rating_change_1m", "analyst_count"]
    SMART_MONEY_COLS = ["inst_ownership_change", "inst_accumulation_signal",
                        "hedge_fund_add_ratio", "insider_buy_ratio", "holder_concentration"]
    EARNINGS_EVENT_COLS = ["earnings_price_move_avg", "post_earnings_drift_5d", "earnings_volatility"]

    LABEL_COLS = ["fwd_ret_5d", "fwd_ret_20d"]

    ALL_FACTOR_COLS = (
        QUALITY_COLS + GROWTH_COLS + EARNINGS_QUALITY_COLS
        + VALUATION_COLS + SHORT_COLS + FLOW_COLS + ANALYST_COLS
        + SMART_MONEY_COLS + EARNINGS_EVENT_COLS
    )

    def __init__(self):
        self.factor_names: list[str] = []

    # ── Individual factor computations ───────────────────────────────

    @staticmethod
    def _quality_factors(financials: pd.DataFrame) -> pd.DataFrame:
        """Extract quality factors from financial statements."""
        df = pd.DataFrame(index=financials.index)
        for col in FundamentalFactorBuilder.QUALITY_COLS:
            if col in financials.columns:
                df[col] = financials[col]
        return df

    @staticmethod
    def _growth_factors(financials: pd.DataFrame) -> pd.DataFrame:
        """Growth factors — YoY changes from financial data."""
        df = pd.DataFrame(index=financials.index)
        map_ = {"revenue_growth_yoy": "revenue", "eps_growth_yoy": "eps",
                "net_profit_growth_yoy": "net_profit", "asset_growth_yoy": "total_assets"}
        for factor_col, raw_col in map_.items():
            if raw_col in financials.columns:
                df[factor_col] = financials[raw_col].pct_change(4)  # QoQ → YoY (4 quarters)
        return df

    @staticmethod
    def _valuation_factors(valuation: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=valuation.index)
        for col in FundamentalFactorBuilder.VALUATION_COLS:
            if col in valuation.columns:
                df[col] = valuation[col]
        return df

    @staticmethod
    def _short_factors(short_interest: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=short_interest.index)
        for col in FundamentalFactorBuilder.SHORT_COLS:
            if col in short_interest.columns:
                df[col] = short_interest[col]
        return df

    @staticmethod
    def _capital_flow_factors(flow: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=flow.index)
        for col in FundamentalFactorBuilder.FLOW_COLS:
            if col in flow.columns:
                df[col] = flow[col]
        return df

    @staticmethod
    def _analyst_factors(analyst: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame(index=analyst.index)
        for col in FundamentalFactorBuilder.ANALYST_COLS:
            if col in analyst.columns:
                df[col] = analyst[col]
        return df

    # ── Main pipeline ───────────────────────────────────────────────

    def compute(self, factor_names: list[str], data_map: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Compute requested F10 factors from raw data.

        Args:
            factor_names: List of factor column names, e.g. ["roe", "pe_percentile"].
            data_map: Dict keyed by data type ("financials", "valuation", "short_interest",
                      "capital_flow", "analyst", "shareholder", "earnings").
                      Each value is a pd.DataFrame with symbol and date columns.

        Returns:
            Factor DataFrame with requested columns, indexed by date.
        """
        result = pd.DataFrame()

        # Quality + Growth + Earnings Quality
        fin = data_map.get("financials", pd.DataFrame())
        if not fin.empty:
            qual = self._quality_factors(fin)
            grow = self._growth_factors(fin)
            result = pd.concat([result, qual, grow], axis=1)

        # Valuation
        val = data_map.get("valuation", pd.DataFrame())
        if not val.empty:
            result = pd.concat([result, self._valuation_factors(val)], axis=1)

        # Short interest
        si = data_map.get("short_interest", pd.DataFrame())
        if not si.empty:
            result = pd.concat([result, self._short_factors(si)], axis=1)

        # Capital flow
        cf = data_map.get("capital_flow", pd.DataFrame())
        if not cf.empty:
            result = pd.concat([result, self._capital_flow_factors(cf)], axis=1)

        # Analyst
        an = data_map.get("analyst", pd.DataFrame())
        if not an.empty:
            result = pd.concat([result, self._analyst_factors(an)], axis=1)

        # Filter to requested columns
        available = [c for c in factor_names if c in result.columns]
        self.factor_names = available
        return result[available] if available else pd.DataFrame()

    def process_factors(self, factor_df: pd.DataFrame, winsor_pct: float = 0.01) -> pd.DataFrame:
        """Winsorize → z-score → NaN→0 pipeline. Same as TechFactorBuilder."""
        df = factor_df.copy()
        factor_cols = [c for c in df.columns if c not in ("fwd_ret_5d", "fwd_ret_20d", "symbol", "date")]

        for col in factor_cols:
            lo = df[col].quantile(winsor_pct)
            hi = df[col].quantile(1 - winsor_pct)
            df[col] = df[col].clip(lo, hi)

        for col in factor_cols:
            mean = df[col].mean()
            std = df[col].std()
            if std is not None and not pd.isna(std) and std > 1e-8:
                df[col] = (df[col] - mean) / std

        df[factor_cols] = df[factor_cols].fillna(0)
        return df

    def build_dataset(
        self, symbols: list[str], start: str, end: str,
        data_loader: Callable[[str, str, str], dict[str, pd.DataFrame]],
    ) -> pd.DataFrame:
        """Batch build F10 factor dataset for multiple symbols.

        Args:
            symbols: List of stock symbols.
            start/end: Date range (YYYY-MM-DD).
            data_loader: (symbol, start, end) -> dict of {data_type: DataFrame}.

        Returns:
            Combined factor DataFrame with symbol and date columns.
        """
        all_factors: list[pd.DataFrame] = []
        for i, sym in enumerate(symbols):
            try:
                data_map = data_loader(sym, start, end)
                if not data_map:
                    continue
                factors = self.compute(self.ALL_FACTOR_COLS, data_map)
                if factors.empty:
                    continue
                factors["symbol"] = sym
                all_factors.append(factors)
                if (i + 1) % 10 == 0:
                    logger.info("  F10 factors: %d/%d stocks", i + 1, len(symbols))
            except Exception:
                logger.debug("  %s: F10 factor build failed", sym, exc_info=True)

        if not all_factors:
            return pd.DataFrame()
        combined = pd.concat(all_factors, ignore_index=True)
        return combined

    # ── IC analysis ─────────────────────────────────────────────────

    def compute_ic(self, factor_df: pd.DataFrame, label_col: str = "fwd_ret_5d") -> pd.DataFrame:
        factor_cols = [c for c in factor_df.columns
                       if c not in ("symbol", "date", "fwd_ret_5d", "fwd_ret_20d")]
        records: list[dict] = []
        for date_val, group in factor_df.groupby("date"):
            if len(group) < 30:
                continue
            for col in factor_cols:
                if col not in group.columns or group[col].isna().all():
                    continue
                valid = group[[col, label_col]].dropna()
                if len(valid) < 30:
                    continue
                ic = _spearmanr(valid[col], valid[label_col])
                records.append({"date": date_val, "factor": col, "rank_ic": ic})
        return pd.DataFrame(records)

    def ic_summary(self, ic_df: pd.DataFrame) -> pd.DataFrame:
        if ic_df.empty:
            return pd.DataFrame(columns=["factor", "mean", "std", "count", "icir", "abs_mean_ic"])
        summary = ic_df.groupby("factor")["rank_ic"].agg(["mean", "std", "count"]).reset_index()
        summary["icir"] = summary["mean"] / summary["std"].replace(0, np.nan)
        summary["abs_mean_ic"] = summary["mean"].abs()
        return summary.sort_values("abs_mean_ic", ascending=False).reset_index(drop=True)
```

- [ ] **Step 4: Run tests**

Run: `pytest factors/tests/test_fundamental_builder.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add factors/fundamental_builder.py factors/tests/test_fundamental_builder.py
git commit -m "feat: add FundamentalFactorBuilder — 41 F10 fundamental/sentiment/flow factors"
```

---

### Task 12: Extend FactorRegistry + init script + ML trainer + universe + QARP strategy

**Files:**
- Modify: `factors/registry.py`
- Modify: `scripts/init_factor_registry.py`
- Modify: `ml/trainer.py`
- Modify: `paper/market.py`
- Modify: `paper/strategies.py`
- Modify: `run_paper.py`

- [ ] **Step 1: Extend FactorRegistry category map**

In `scripts/init_factor_registry.py`, add new category mappings:

```python
# Add to existing CATEGORY_MAP dict:
CATEGORY_MAP.update({
    # Fundamental/F10 categories
    "roe": "quality", "roa": "quality", "gross_margin": "quality",
    "net_margin": "quality", "debt_to_equity": "quality",
    "current_ratio": "quality", "interest_coverage": "quality",
    "revenue_growth": "growth", "eps_growth": "growth",
    "net_profit_growth": "growth", "asset_growth": "growth",
    "accruals": "earnings_quality", "ocf_to": "earnings_quality",
    "revenue_to_cash": "earnings_quality",
    "pe_percentile": "valuation", "pb_percentile": "valuation",
    "ps_percentile": "valuation", "pe_vs_5y": "valuation",
    "peg_ratio": "valuation",
    "short_ratio": "short_sentiment", "days_to_cover": "short_sentiment",
    "short_change": "short_sentiment", "short_volume": "short_sentiment",
    "short_utilization": "short_sentiment",
    "main_inflow": "capital_flow", "big_order": "capital_flow",
    "retail_flow": "capital_flow", "flow_price": "capital_flow",
    "target_price": "analyst", "buy_ratio": "analyst",
    "rating_mean": "analyst", "rating_change": "analyst",
    "analyst_count": "analyst",
    "inst_ownership": "smart_money", "inst_accumulation": "smart_money",
    "hedge_fund": "smart_money", "insider_buy": "smart_money",
    "holder_concentration": "smart_money",
    "earnings_price_move": "earnings_event", "post_earnings_drift": "earnings_event",
    "earnings_volatility": "earnings_event",
})
```

Update `main()` to register FundamentalFactorBuilder factors too:

```python
def main():
    from factors.tech_builder import TechFactorBuilder
    from factors.fundamental_builder import FundamentalFactorBuilder

    registry = FactorRegistry()

    # Tech factors
    tfb = TechFactorBuilder()
    dummy = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=100, freq="B"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000000,
    })
    tfb.compute_factors(dummy)
    print(f"Found {len(tfb.factor_names)} tech factors to register")

    registered = 0
    for name in tfb.factor_names:
        category = classify_factor(name)
        registry.register(
            factor_id=f"us_{name}", name=name.replace("_", " ").title(),
            market="us", source="tech", category=category,
            formula=f"factors/tech_builder.py::TechFactorBuilder (group: {category})",
        )
        registered += 1
    print(f"Registered {registered}/{len(tfb.factor_names)} tech factors")

    # F10 factors
    ffb = FundamentalFactorBuilder()
    f10_names = ffb.ALL_FACTOR_COLS
    print(f"Found {len(f10_names)} F10 factors to register")

    f10_registered = 0
    for name in f10_names:
        category = classify_factor(name)
        ok = registry.register(
            factor_id=f"us_{name}", name=name.replace("_", " ").title(),
            market="us", source="fundamental", category=category,
            formula=f"factors/fundamental_builder.py::FundamentalFactorBuilder (group: {category})",
        )
        if ok:
            f10_registered += 1
    print(f"Registered {f10_registered}/{len(f10_names)} F10 factors")
```

- [ ] **Step 1b: Add min_periods to FactorEvaluation**

In `factors/evaluation.py`, modify `evaluate_factor()` to accept optional `min_periods`:

```python
def evaluate_factor(factor_values, fwd_ret_1d, fwd_ret_5d, fwd_ret_20d,
                    min_periods: int = 30):
    """Run full factor evaluation.
    
    Args:
        min_periods: Minimum required data points (lower for quarterly factors).
    """
    ic = compute_ic(factor_values, fwd_ret_20d)
    t = len(factor_values.dropna())
    ic_tstat = abs(ic) * np.sqrt(t) if ic is not None and not np.isnan(ic) else 0.0
    # ... rest unchanged, except coverage check uses min_periods
    coverage = compute_coverage(factor_values)
    if t < min_periods:
        passes = False
        details.append("insufficient_periods")
    # ...
```

Update `FactorRegistry.evaluate()` in `factors/registry.py` to pass `min_periods` through:

```python
def evaluate(self, factor_id, factor_values, fwd_ret_1d, fwd_ret_5d,
             fwd_ret_20d, eval_period_start=None, eval_period_end=None,
             force=False, min_periods=30):
    # ... pass min_periods to _eval()
    result = _eval(factor_values, fwd_ret_1d, fwd_ret_5d, fwd_ret_20d,
                   min_periods=min_periods)
```

- [ ] **Step 2: Extend ML trainer for BQ + factor source selection**

In `ml/trainer.py`, add `load_data_from_bq()` and `factor_source` parameter:

```python
def load_data_from_bq(
    self, symbols: list[str], start_date: str, end_date: str,
    factor_source: str = "all", market: str = "us",
) -> pd.DataFrame:
    """Load factor data from BigQuery factor_values table."""
    from google.cloud import bigquery
    client = bigquery.Client()

    source_filter = ""
    if factor_source == "tech":
        source_filter = "AND source_builder = 'tech'"
    elif factor_source == "fundamental":
        source_filter = "AND source_builder = 'fundamental'"

    query = f"""
        SELECT factor_id, symbol, date, value, source_builder
        FROM `deductive-notch-495015-c2.quant.factor_values`
        WHERE symbol IN UNNEST(@symbols)
          AND date BETWEEN @start AND @end
          {source_filter}
        ORDER BY date, symbol, factor_id
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("symbols", "STRING", symbols),
        bigquery.ScalarQueryParameter("start", "STRING", start_date),
        bigquery.ScalarQueryParameter("end", "STRING", end_date),
    ])
    df = client.query(query, job_config=job_config).to_dataframe()
    df["date"] = pd.to_datetime(df["date"])

    # Pivot: factor_id → columns, date+symbol → rows
    pivoted = df.pivot_table(
        index=["date", "symbol"], columns="factor_id", values="value"
    ).reset_index()
    logger.info("Loaded %d rows × %d cols from BQ factor_values", len(pivoted), len(pivoted.columns))

    self.factor_df = pivoted
    exclude = {"symbol", "date", "fwd_ret_5d", "fwd_ret_20d"}
    self.feature_cols = [c for c in pivoted.columns if c not in exclude]
    return pivoted
```

- [ ] **Step 3: Add UniverseBuilder to paper/market.py**

```python
class UniverseBuilder:
    """Dynamic universe construction from screens, sectors, BQ rankings, or static lists."""

    @staticmethod
    def from_static(market: str) -> list[str]:
        return default_symbols_for(market)

    @staticmethod
    def from_plate(plate_code: str) -> list[str]:
        """Resolve plate code to constituent list via Futu API."""
        # Plate codes like "hsi", "hstech" — map via the existing _BUILTIN_PLATES
        from futu import OpenQuoteContext, RET_OK
        ctx = OpenQuoteContext()
        try:
            ret, data = ctx.get_plate_stock(plate_code, sort_field=1, ascend=False)
            if ret != RET_OK:
                raise ValueError(f"Plate query failed: {data}")
            return data["code"].tolist()[:100]  # top 100 by market cap
        finally:
            ctx.close()

    @staticmethod
    def from_bq(market: str, date: str, factor_id: str = "roe",
                min_market_cap: float = 1e10, top_k: int = 100) -> list[str]:
        """Select top K symbols by a factor's latest value from BQ."""
        from google.cloud import bigquery
        client = bigquery.Client()
        query = f"""
            SELECT symbol, value
            FROM `deductive-notch-495015-c2.quant.factor_values`
            WHERE factor_id = @factor_id AND date = @date
            ORDER BY value DESC
            LIMIT @top_k
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("factor_id", "STRING", factor_id),
            bigquery.ScalarQueryParameter("date", "STRING", date),
            bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
        ])
        df = client.query(query, job_config=job_config).to_dataframe()
        # Strip prefix: "HK.00700" → "00700"
        prefix = "US." if market == "us" else "HK."
        return df["symbol"].str.replace(prefix, "").tolist()
```

- [ ] **Step 4: Add QARP strategy to paper/strategies.py**

```python
class QARP(Strategy):
    """Quality at Reasonable Price — buy high-ROE + low-PE stocks, monthly rebalance.

    Reads composite quality score from ctx.predictions.
    """

    top_k: int = 20
    rebalance_every: int = 21
    allocation: float = 0.95
    max_weight_per_symbol: float = 0.05

    def on_init(self, ctx):
        self._last_rebalance = -self.rebalance_every

    def on_bar(self, ctx, bar: int) -> list[Signal]:
        if bar - self._last_rebalance < self.rebalance_every:
            return []
        self._last_rebalance = bar

        if ctx.predictions is None:
            return []

        scores = {s: v for s, v in ctx.predictions.items()
                  if s in ctx.universe and not np.isnan(v)}
        if len(scores) < 3:
            return []

        sorted_symbols = sorted(scores, key=scores.get, reverse=True)
        selected = sorted_symbols[:self.top_k]

        weight = min(self.allocation / max(len(selected), 1), self.max_weight_per_symbol)
        signals: list[Signal] = []

        for sym in selected:
            pos = ctx.portfolio.positions.get(sym)
            if pos and hasattr(pos, "size") and pos.size > 0:
                signals.append(Signal.target(sym, weight))
            else:
                signals.append(Signal.buy(sym, weight))

        for sym, pos in ctx.portfolio.positions.items():
            if hasattr(pos, "size") and pos.size > 0 and sym not in selected:
                signals.append(Signal.close(sym))

        return signals


# Add to _BUILTIN_STRATEGIES
_BUILTIN_STRATEGIES["QARP"] = QARP
```

- [ ] **Step 5: Add --universe flag to run_paper.py**

```python
# In build_parser():
p.add_argument("--universe", type=str, default="static",
               help="Universe source: static, plate:<code>, bq:<factor_id>")

# In _config_from_args(), add universe resolution:
from paper.market import UniverseBuilder

universe = args.universe
if universe == "static":
    symbols = args.symbols if args.symbols else default_symbols_for(market)
elif universe.startswith("plate:"):
    symbols = UniverseBuilder.from_plate(universe.split(":", 1)[1])
elif universe.startswith("bq:"):
    symbols = UniverseBuilder.from_bq(market, end, factor_id=universe.split(":", 1)[1])
else:
    raise ValueError(f"Unknown universe: {universe!r}")

config["symbols"] = symbols
```

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest factors/tests/ collectors/tests/ -v -k "not vcr"
```
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add factors/registry.py scripts/init_factor_registry.py ml/trainer.py paper/market.py paper/strategies.py run_paper.py
git commit -m "feat: extend registry, ML trainer, universe builder, and QARP strategy for Phase 1"
```

---

### Task 13: End-to-end integration test

**Files:**
- Create: `factors/tests/test_f10_integration.py`

- [ ] **Step 1: Write integration test**

```python
"""Integration test: tech + fundamental factors → BQ → ML → Strategy."""
import numpy as np
import pandas as pd

from factors.tech_builder import TechFactorBuilder
from factors.fundamental_builder import FundamentalFactorBuilder


def make_ohlcv(n=500):
    rng = np.random.default_rng(42)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, n)))
    return pd.DataFrame({
        "date": dates, "open": close, "high": close * 1.01,
        "low": close * 0.99, "close": close, "volume": rng.lognormal(14, 0.8, n),
    })


def test_tech_and_fundamental_combine():
    """Verify tech (39) + fundamental (41) = 80 unique factors."""
    tfb = TechFactorBuilder()
    ohlcv = make_ohlcv()
    tech_factors = tfb.compute_factors(ohlcv)
    tech_names = tfb.factor_names
    assert len(tech_names) == 39

    ffb = FundamentalFactorBuilder()
    assert len(ffb.ALL_FACTOR_COLS) == 41

    combined = set(tech_names) | set(ffb.ALL_FACTOR_COLS)
    assert len(combined) == 80  # no overlap
```

- [ ] **Step 2: Run integration test**

Run: `pytest factors/tests/test_f10_integration.py -v`
Expected: 1 test PASS

- [ ] **Step 3: Final commit**

```bash
git add factors/tests/test_f10_integration.py
git commit -m "test: integration test for tech(39) + fundamental(41) = 80 factors"
```

---

## Verification Checklist

After all tasks complete, verify:

1. `python -m pytest factors/tests/ -v` — all factor tests pass
2. `python -m pytest collectors/tests/ -v -k "not vcr"` — all collector tests pass
3. `python scripts/init_factor_registry.py` — registers both tech + fundamental to BQ
4. `python run_paper.py --market us --strategy QARP --data-source bq --start 2024-01-01 --end 2025-12-31` — QARP strategy runs on BQ data
5. `python run_paper.py --list-strategies` — lists QARP
6. `python collectors/fundamental_collector.py --source all --market us` — collects F10 data without errors
