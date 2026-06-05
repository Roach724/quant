# Dashboard 整合 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate Dashboard into Admin Platform — backend proxies Dashboard APIs directly from BQ, frontend React rewrites all Dashboard pages. Fix experiment detail data loading.

**Architecture:** New `/api/admin/dashboard/*` routes in admin/server.py → BQ direct queries → React Dashboard Tab with 7 sub-tabs → experiment detail uses new APIs.

**Tech Stack:** React 18, TypeScript, Ant Design Pro, Plotly.js, FastAPI, BigQuery

**Spec:** `docs/superpowers/specs/2026-06-05-dashboard-integration-design.md`

---

## Phase 0: Backend — Dashboard API Migration

### Task 0.1: Migrate core experiment APIs

**Files:**
- Modify: `admin/server.py`
- Reference: `dashboard/server.py`

- [ ] **Step 1: Copy and adapt experiment APIs**

Read the following functions from `dashboard/server.py` and copy them into `admin/server.py`. Prefix all routes with `/admin/dashboard`. Remove dependency on Dashboard-specific helpers (`_get_bq()`, `_table()`, `_serialize()`, `_row_to_dict()`).

Functions to migrate (with their route paths):

```python
# ── Dashboard: Experiment APIs ──

# 1. /api/experiments → /api/admin/dashboard/experiments
# Latest equity snapshot per experiment, with optional type filter
@app.get("/api/admin/dashboard/experiments")
def dashboard_experiments(type: str = ""):
    ...

# 2. /api/experiments/meta → /api/admin/dashboard/experiments/meta
# Experiment metadata from tracker JSON files
@app.get("/api/admin/dashboard/experiments/meta")
def dashboard_experiments_meta():
    ...

# 3. /api/equity/{exp_id} → /api/admin/dashboard/equity/{exp_id}
# Equity curve, defaults to latest run_id
@app.get("/api/admin/dashboard/equity/{exp_id}")
def dashboard_equity(exp_id: str, run_id: str = ""):
    ...

# 4. /api/trades/{exp_id} → /api/admin/dashboard/trades/{exp_id}
# Trade records, defaults to latest run_id
@app.get("/api/admin/dashboard/trades/{exp_id}")
def dashboard_trades(exp_id: str, limit: int = 200, run_id: str = ""):
    ...

# 5. /api/experiments/{exp_id}/positions → /api/admin/dashboard/experiments/{exp_id}/positions
# Current positions computed from trades (FIFO)
@app.get("/api/admin/dashboard/experiments/{exp_id}/positions")
def dashboard_positions(exp_id: str):
    ...

# 6. /api/experiments/{exp_id}/runs → /api/admin/dashboard/experiments/{exp_id}/runs
# Run history from experiment_runs table
@app.get("/api/admin/dashboard/experiments/{exp_id}/runs")
def dashboard_runs(exp_id: str):
    ...

# 7. /api/paper-runs → /api/admin/dashboard/paper-runs
# Paper run list
@app.get("/api/admin/dashboard/paper-runs")
def dashboard_paper_runs(limit: int = 50, status: str = None):
    ...

# 8. /api/paper-runs/{run_id} → /api/admin/dashboard/paper-runs/{run_id}
# Paper run detail (metadata + metrics + equity + trades)
@app.get("/api/admin/dashboard/paper-runs/{run_id}")
def dashboard_paper_run_detail(run_id: str):
    ...
```

**Implementation note:** Copy the function body from `dashboard/server.py`, but:
- Replace `_get_bq()` with `bigquery.Client(project="deductive-notch-495015-c2")`
- Replace `_table(name)` with `f"deductive-notch-495015-c2.quant.{name}"`
- Replace `_serialize(x)` with `x.isoformat() if hasattr(x, 'isoformat') else str(x)`
- Replace `_row_to_dict(r, names)` with `{n: getattr(r, n) for n in names}`

- [ ] **Step 2: Copy and adapt market APIs**

```python
# 9. /api/pipeline → /api/admin/dashboard/pipeline
# Data freshness check
@app.get("/api/admin/dashboard/pipeline")
def dashboard_pipeline():
    ...

# 10. /api/market/{market}/{symbol} → /api/admin/dashboard/market/{market}/{symbol}
# K-line chart data with timezone correction + dedup
@app.get("/api/admin/dashboard/market/{market}/{symbol}")
def dashboard_market_bars(market: str, symbol: str, limit: int = 78):
    ...

# 11. /api/market/symbols/{market} → /api/admin/dashboard/market/symbols/{market}
# Symbol list from config/symbols.yaml
@app.get("/api/admin/dashboard/market/symbols/{market}")
def dashboard_market_symbols(market: str):
    ...
```

- [ ] **Step 3: Verify all APIs work**

Start admin server, test each endpoint:
```bash
cd /opt/quant-dev && PYTHONPATH=/opt/quant-dev .venv/bin/python3 -m uvicorn admin.server:app --port 8092 &
sleep 2

# Test core APIs
curl -s http://localhost:8092/api/admin/dashboard/experiments | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} experiments')"
curl -s http://localhost:8092/api/admin/dashboard/experiments/meta | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} meta')"
curl -s http://localhost:8092/api/admin/dashboard/equity/live_us_ml_v2 | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} bars')"
curl -s http://localhost:8092/api/admin/dashboard/trades/live_us_ml_v2 | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} trades')"
curl -s http://localhost:8092/api/admin/dashboard/experiments/live_us_ml_v2/positions | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} positions')"
curl -s http://localhost:8092/api/admin/dashboard/experiments/live_us_ml_v2/runs | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} runs')"
curl -s http://localhost:8092/api/admin/dashboard/pipeline | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'us={d.get(\"us\",\"\")[:19]} hk={d.get(\"hk\",\"\")[:19]}')"
curl -s http://localhost:8092/api/admin/dashboard/market/us/AAPL | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} bars')"
curl -s http://localhost:8092/api/admin/dashboard/market/symbols/us | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} symbols')"

kill %1
```

Expected: all return non-zero data.

- [ ] **Step 4: Commit**

```bash
cd /opt/quant-dev && git add admin/server.py && git commit -m "feat: migrate Dashboard APIs to admin server (/api/admin/dashboard/*)"
```

---

## Phase 1: Frontend — Dashboard Tab Framework

### Task 1.1: Create Dashboard main component with sub-tabs

**Files:**
- Create: `admin/frontend/src/pages/Dashboard.tsx`
- Modify: `admin/frontend/src/App.tsx`

- [ ] **Step 1: Create Dashboard.tsx with sub-tab structure**

```tsx
import { Tabs } from 'antd';
import { useState } from 'react';
import DashboardOverview from './DashboardOverview';
import DashboardLive from './DashboardLive';
import DashboardPaperRun from './DashboardPaperRun';
import DashboardProd from './DashboardProd';
import DashboardDebug from './DashboardDebug';
import DashboardPipeline from './DashboardPipeline';

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'live', label: 'Live' },
  { key: 'paper', label: 'Paper Run' },
  { key: 'prod', label: 'Prod' },
  { key: 'debug', label: 'Debug' },
  { key: 'pipeline', label: 'Pipeline' },
  { key: 'alerts', label: 'Alerts' },
];

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('overview');

  return (
    <Tabs activeKey={activeTab} onChange={setActiveTab} items={TABS.map(tab => ({
      key: tab.key,
      label: tab.label,
      children: tab.key === 'overview' ? <DashboardOverview /> :
                tab.key === 'live' ? <DashboardLive /> :
                tab.key === 'paper' ? <DashboardPaperRun /> :
                tab.key === 'prod' ? <DashboardProd /> :
                tab.key === 'debug' ? <DashboardDebug /> :
                tab.key === 'pipeline' ? <DashboardPipeline /> :
                <div style={{textAlign:'center',padding:40,color:'#999'}}>Alerts — coming soon</div>,
    }))} />
  );
}
```

- [ ] **Step 2: Add Dashboard to App.tsx menu**

In `admin/frontend/src/App.tsx`, add "Dashboard" as the first menu item:

```tsx
import { DashboardOutlined } from '@ant-design/icons';
import Dashboard from './pages/Dashboard';

// In menuData array, add at the beginning:
{ path: '/dashboard', name: 'Dashboard', icon: <DashboardOutlined /> },

// In the PageContainer, add:
{pathname === '/dashboard' && <Dashboard />}
```

- [ ] **Step 3: Create placeholder sub-pages**

Create these files (empty placeholder divs with page names for now):
- `admin/frontend/src/pages/DashboardOverview.tsx`
- `admin/frontend/src/pages/DashboardLive.tsx`
- `admin/frontend/src/pages/DashboardPaperRun.tsx`
- `admin/frontend/src/pages/DashboardProd.tsx`
- `admin/frontend/src/pages/DashboardDebug.tsx`
- `admin/frontend/src/pages/DashboardPipeline.tsx`

Each returns `<div>PageName — coming soon</div>`.

- [ ] **Step 4: Verify Dashboard tab renders**

```bash
cd /opt/quant-dev/admin/frontend && npm run build
```

No build errors. The Dashboard tab should appear in the sidebar with 7 sub-tabs.

- [ ] **Step 5: Commit**

```bash
cd /opt/quant-dev && git add -A && git commit -m "feat: Dashboard Tab framework with 7 sub-tabs"
```

---

## Phase 2: Overview Page

### Task 2.1: Experiment summary cards + K-line charts

**Files:**
- Modify: `admin/frontend/src/pages/DashboardOverview.tsx`
- Install: `npm install react-plotly.js plotly.js` (or use Plotly CDN)

- [ ] **Step 1: Build experiment cards**

`DashboardOverview.tsx` — fetch `/api/admin/dashboard/experiments` + `/meta`, merge data, render Ant Design Cards grid:

```tsx
import { Card, Tag, Row, Col, Switch } from 'antd';
import { useEffect, useState } from 'react';
import { api } from '../api';

export default function DashboardOverview() {
  const [experiments, setExperiments] = useState<any[]>([]);
  const [activeOnly, setActiveOnly] = useState(true);

  useEffect(() => { loadData(); }, []);
  const loadData = async () => {
    const [bqData, meta] = await Promise.all([
      api.get('/api/admin/dashboard/experiments'),
      api.get('/api/admin/dashboard/experiments/meta'),
    ]);
    const bqMap: any = {};
    if (bqData) bqData.forEach((e: any) => { bqMap[e.exp_id] = e; });
    if (meta) setExperiments(meta.map((m: any) => ({ ...m, ...(bqMap[m.exp_id] || { sleeping: true }) })));
  };

  const filtered = activeOnly
    ? experiments.filter(e => !e.sleeping)
    : experiments;

  return (
    <div>
      <div style={{marginBottom:16}}>
        <Switch checked={activeOnly} onChange={setActiveOnly} /> Active Only
        <span style={{marginLeft:8,color:'#999'}}>{filtered.length}/{experiments.length}</span>
      </div>
      <Row gutter={[12,12]}>
        {filtered.map(exp => (
          <Col key={exp.exp_id} xs={24} sm={12} md={8} lg={6}>
            <Card size="small" title={exp.exp_id}>
              <p>Status: <Tag>{exp.status || '?'}</Tag></p>
              <p>Bar: {exp.bar || 0}</p>
              <p>Equity: {exp.equity != null ? `$${Math.round(exp.equity).toLocaleString()}` : '—'}</p>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
}
```

- [ ] **Step 2: Add K-line charts**

Below the experiment cards, add two chart sections (US and HK). Use Plotly.js via CDN or react-plotly.js.

Add state for `usSymbol`, `hkSymbol`, `usSymbols`, `hkSymbols`, and `usChartData`, `hkChartData`.

On mount, fetch symbols: `api.get('/api/admin/dashboard/market/symbols/us')` and hk.

On symbol change, fetch chart data: `api.get('/api/admin/dashboard/market/us/' + symbol)`.

Render with Plotly.react() creating a candlestick chart.

- [ ] **Step 3: Commit**

```bash
cd /opt/quant-dev && git add -A && git commit -m "feat: Dashboard Overview — experiment cards + K-line charts"
```

---

## Phase 3: Live / Prod / Debug Pages (shared component)

### Task 3.1: Create shared ExperimentDetail component

**Files:**
- Create: `admin/frontend/src/components/ExperimentDetail.tsx`
- Modify: `admin/frontend/src/pages/DashboardLive.tsx`

- [ ] **Step 1: Build ExperimentDetail component**

A reusable component that takes `type` prop ("live" | "prod" | "debug") and renders:
1. Experiment selector dropdown (filtered by `experiments.filter(e => e.exp_id.startsWith(type + '_'))`)
2. Run selector dropdown (latest by default)
3. Metric cards: Bar, Equity, Day PnL, Drawdown
4. Equity chart (Plotly line chart)
5. Drawdown chart (Plotly area chart)
6. Positions table (from `/api/admin/dashboard/experiments/{id}/positions`)
7. Trades table (from `/api/admin/dashboard/trades/{id}`)

**Key props:**
```tsx
interface ExperimentDetailProps {
  type: 'live' | 'prod' | 'debug';
  readonly?: boolean;  // prod=true hides action buttons
}
```

**Data flow:**
```
select experiment → fetch equity, trades, positions, runs
→ render charts + tables
```

For charts, use Plotly.js CDN in index.html and `Plotly.react()` calls.

Equity chart: timestamp X-axis, equity Y-axis, line trace.
Drawdown chart: timestamp X-axis, drawdown * 100 Y-axis, area trace.

- [ ] **Step 2: Create DashboardLive, DashboardProd, DashboardDebug**

Each is a thin wrapper:

```tsx
// DashboardLive.tsx
import ExperimentDetail from '../components/ExperimentDetail';
export default function DashboardLive() {
  return <ExperimentDetail type="live" />;
}

// DashboardProd.tsx
export default function DashboardProd() {
  return <ExperimentDetail type="prod" readonly />;
}

// DashboardDebug.tsx
export default function DashboardDebug() {
  return <ExperimentDetail type="debug" />;
}
```

- [ ] **Step 3: Verify Live page works**

Select a live experiment → charts render → positions and trades tables show data.

- [ ] **Step 4: Commit**

```bash
cd /opt/quant-dev && git add -A && git commit -m "feat: ExperimentDetail component — Live/Prod/Debug pages"
```

---

## Phase 4: Paper Run Page

### Task 4.1: Paper Run list + detail

**Files:**
- Modify: `admin/frontend/src/pages/DashboardPaperRun.tsx`

- [ ] **Step 1: Build Paper Run page**

Two modes: list view (all paper runs) and detail view (selected run).

**List view:**
- ProTable from `/api/admin/dashboard/paper-runs`
- Columns: run_id, name, strategy, market, status, created_at
- Click row → detail view

**Detail view:**
- Metadata card (run_id, strategy, market, status)
- Metrics cards (sharpe, sortino, max_drawdown, calmar, cagr, win_rate, profit_factor)
- Equity chart
- Trades table
- Source: `/api/admin/dashboard/paper-runs/{run_id}`

- [ ] **Step 2: Commit**

```bash
cd /opt/quant-dev && git add -A && git commit -m "feat: Dashboard Paper Run page — list + detail with metrics"
```

---

## Phase 5: Pipeline + Alerts

### Task 5.1: Pipeline page

**Files:**
- Modify: `admin/frontend/src/pages/DashboardPipeline.tsx`

- [ ] **Step 1: Build Pipeline page**

Fetch `/api/admin/dashboard/pipeline` → display:
- US bars latest timestamp + status (if within 24h, green; else red)
- HK bars latest timestamp + status
- US market open/closed indicator
- HK market open/closed indicator
- Last checked time

Simple card layout with colored indicators.

### Task 5.2: Alerts placeholder

Already done — the Dashboard.tsx returns a placeholder div for alerts tab.

- [ ] **Step 3: Commit**

```bash
cd /opt/quant-dev && git add -A && git commit -m "feat: Dashboard Pipeline page"
```

---

## Phase 6: Experiment Detail Data Fix

### Task 6.1: Update experiment detail to use admin APIs

**Files:**
- Modify: `admin/frontend/src/pages/Experiments.tsx`

- [ ] **Step 1: Replace Dashboard API calls**

In the experiment detail Drawer of `Experiments.tsx`, change all API calls:

| Before | After |
|--------|-------|
| `api.get('/api/equity/' + id)` | `api.get('/api/admin/dashboard/equity/' + id)` |
| `api.get('/api/trades/' + id)` | `api.get('/api/admin/dashboard/trades/' + id)` |
| `api.get('/api/experiments/' + id + '/positions')` | `api.get('/api/admin/dashboard/experiments/' + id + '/positions')` |
| `api.get('/api/experiments/' + id + '/runs')` | `api.get('/api/admin/dashboard/experiments/' + id + '/runs')` |

- [ ] **Step 2: Verify experiment detail shows data**

Open an experiment → click detail → equity chart, positions, trades all load from admin platform APIs.

- [ ] **Step 3: Commit**

```bash
cd /opt/quant-dev && git add -A && git commit -m "fix: experiment detail uses admin Dashboard APIs (no dependency on :8090)"
```

---

## Phase 7: Build, Deploy, Test

### Task 7.1: Production build + deploy

- [ ] **Step 1: Build frontend**

```bash
cd /opt/quant-dev/admin/frontend && npm run build
```

- [ ] **Step 2: Deploy to prod**

```bash
cp -r /opt/quant-dev/admin/* /opt/quant-prod/admin/
sudo systemctl restart quant-admin
```

- [ ] **Step 3: Test all Dashboard sub-pages**

| Page | Check |
|------|-------|
| Overview | Experiment cards show + K-line charts render |
| Live | Experiment selector → charts → positions → trades |
| Paper Run | Run list → click → detail with metrics |
| Prod | Same as Live but read-only |
| Debug | Same as Live |
| Pipeline | US/HK timestamps + open/close status |
| Experiment detail | Charts load from admin APIs (not :8090) |

- [ ] **Step 4: Optionally stop Dashboard (:8090)**

```bash
# Once admin Dashboard is stable, Dashboard is no longer needed
sudo systemctl stop dashboard
```

- [ ] **Step 5: Commit + push**

```bash
cd /opt/quant-dev && git add -A && git commit -m "chore: Dashboard integration complete — build + deploy"
git push origin feature/admin-platform
```

---

## Summary

| Phase | Content | Tasks |
|-------|---------|-------|
| 0 | Backend — migrate 11 Dashboard APIs to admin server | 1 |
| 1 | Frontend — Dashboard Tab framework + 7 sub-pages | 1 |
| 2 | Overview page — experiment cards + K-line charts | 1 |
| 3 | Live/Prod/Debug — shared ExperimentDetail component | 1 |
| 4 | Paper Run page — list + detail | 1 |
| 5 | Pipeline page | 1 |
| 6 | Experiment detail data fix | 1 |
| 7 | Build + deploy + integration test | 1 |

**Total: 8 tasks**
