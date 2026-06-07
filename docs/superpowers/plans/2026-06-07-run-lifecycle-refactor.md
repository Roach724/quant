# 实验室逻辑重构 — 状态下沉到 Run

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实验回归配置容器，Run 成为独立的生命周期+数据+状态单元。每个 Run 有自己的 state、checkpoint、output、报告，可独立启动/停止/清除/删除。

**Architecture:** State 从 `/var/quant/state/{strategy}/` → `/var/quant/state/{exp_id}/{run_id}/`。Runner state 路径由 run_id 驱动。前端 Runs 列表改为可展开行，内联 equity/positions，每个 run 有 7 个操作按钮。Dashboard Live/Paper 加 URL 参数支持直链。

**Tech Stack:** Python (FastAPI, ExperimentManager, StateManager, Runner), React + Ant Design Pro (Experiments.tsx, DashboardLive.tsx)

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `live/state.py` | 修改 | StateManager 不变，Runner 传参改 |
| `live/runner.py` | 修改 | state 路径用 `{exp_id}/{run_id}` 替代 `{strategy}`；加 run-level 启动 |
| `live/exp_cli.py` | 修改 | `cmd_start` 接受已有 run_id 恢复运行 |
| `live/experiment_manager.py` | 修改 | 加 `delete_run()` / `clear_run_state()` |
| `admin/server.py` | 修改 | 新增 6 个 run 级 API 端点；Dashboard Live 加 URL 参数 |
| `admin/frontend/src/pages/Experiments.tsx` | 重写 LabTab | Run 列表可展开行 + 7 按钮 |
| `admin/frontend/src/pages/DashboardLive.tsx` | 修改 | 支持 `?exp_id=xxx&run_id=yyy` 直链 |
| `admin/frontend/src/pages/DashboardPaperRun.tsx` | 修改 | 同上 |

---

## Task 1: StateManager — 状态下沉（Runner 改动）

**Files:**
- Modify: `live/runner.py`

**背景：** 当前 state 路径来自 config `state.dir`，默认 `output/live/state/`，目录名为 `{strategy}/`。需要改为 `{exp_id}/{run_id}/`。

- [ ] **Step 1: 修改 `_init_components()` 中 StateManager 初始化**

```python
# 原代码（约 line 240-243）:
state_cfg = self.config.get("state", {})
if state_cfg.get("enabled", True):
    state_dir = state_cfg.get("dir", "output/live/state/")
    self._state_manager = StateManager(state_dir)

# 改为:
exp_id = self.config.get("experiment", {}).get("id", "unknown")
run_id = self.config.get("_run_id", "unknown")
state_base = "/var/quant/state"
state_dir = f"{state_base}/{exp_id}/{run_id}"
self._state_manager = StateManager(state_dir)
```

- [ ] **Step 2: 验证 state 路径逻辑**

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 -c "
exp_id='live_us_mom'; run_id='20260607_test'
state_dir=f'/var/quant/state/{exp_id}/{run_id}'
print(f'State dir: {state_dir}')
from pathlib import Path; Path(state_dir).mkdir(parents=True, exist_ok=True)
print('Created OK')
"
```

- [ ] **Step 3: 清理旧 state 目录（手动，不在此 task）**

旧 state 在 `/var/quant/state/{strategy}/` 下不再使用。下次启动新 run 会自动写入新路径。

- [ ] **Step 4: Commit**

```bash
git add live/runner.py
git commit -m "refactor: bind state/checkpoint to run (exp_id/run_id path)"
```

---

## Task 2: ExperimentManager — 加 `delete_run()` / `clear_run_state()`

**Files:**
- Modify: `live/experiment_manager.py`

- [ ] **Step 1: 加 `delete_run(exp_id, run_id)` 方法**

```python
def delete_run(self, exp_id: str, run_id: str) -> dict:
    """Permanently delete a run and all associated data.
    
    Cascading: BQ (equity + trades) + state files + output dir + log file.
    Returns dict of what was deleted.
    """
    import shutil, glob as _glob
    from pathlib import Path as _Path

    entry = self._get_exp(exp_id)
    result: dict[str, str] = {}
    
    # 1. Remove from registry
    runs = entry.setdefault("runs", [])
    entry["runs"] = [r for r in runs if r.get("run_id") != run_id]
    if entry.get("current_run") == run_id:
        entry["current_run"] = None
    self._save()
    result["registry"] = "removed"

    # 2. Delete BQ data for this run
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=BQ_PROJECT)
        for table in ["experiment_equity", "experiment_trades"]:
            q = f"DELETE FROM {BQ_PROJECT}.{BQ_DATASET}.{table} WHERE run_id='{run_id}'"
            client.query(q).result()
            result[f"bq_{table}"] = "deleted"
    except Exception as e:
        result["bq_error"] = str(e)[:80]

    # 3. Delete state directory
    state_dir = _Path(f"/var/quant/state/{exp_id}/{run_id}")
    if state_dir.exists():
        shutil.rmtree(state_dir)
        result["state"] = "deleted"

    # 4. Delete output directory (contains equity_curve.csv, trades.csv, etc.)
    output_pattern = f"/opt/quant-prod/output/live/*_{exp_id}_{run_id}"
    for d in _glob.glob(output_pattern):
        if _Path(d).is_dir():
            shutil.rmtree(d)
            result["output"] = "deleted"

    # 5. Delete log file
    for module in ["live", "paper_run"]:
        log_file = _Path(f"/var/log/quant/prod/{module}/{exp_id}_{run_id}.log")
        if log_file.exists():
            log_file.unlink()
            result[f"log_{module}"] = "deleted"

    logger.info("Deleted run %s of %s: %s", run_id, exp_id, result)
    return result
```

- [ ] **Step 2: 加 `clear_run_state(exp_id, run_id)` 方法**

```python
def clear_run_state(self, exp_id: str, run_id: str) -> dict:
    """Clear state and checkpoint for a run (keeps BQ data and logs).
    Useful for resetting a run to re-run from scratch.
    """
    import shutil
    from pathlib import Path as _Path

    result: dict[str, str] = {}
    state_dir = _Path(f"/var/quant/state/{exp_id}/{run_id}")
    if state_dir.exists():
        shutil.rmtree(state_dir)
        result["state"] = "deleted"
    else:
        result["state"] = "not found"
    
    logger.info("Cleared state for run %s of %s", run_id, exp_id)
    return result
```

- [ ] **Step 3: Commit**

```bash
git add live/experiment_manager.py
git commit -m "feat: add delete_run() and clear_run_state() to ExperimentManager"
```

---

## Task 3: exp_cli.py — `cmd_start` 支持恢复已有 run

**Files:**
- Modify: `live/exp_cli.py`

当前 `cmd_start` 总是创建新 run。需要支持传入 `--run-id` 来恢复已有 run（不创建新 run，直接用已有 state checkpoint 启动 runner）。

- [ ] **Step 1: `cmd_start` 加 `--resume-run` 参数**

在 `build_parser()` 中：

```python
p_start.add_argument("--resume-run", type=str, default="",
                     help="Resume an existing run_id instead of creating a new one")
```

- [ ] **Step 2: `cmd_start` 分支逻辑**

```python
def cmd_start(mgr, args):
    exp_id = args.id
    
    if args.resume_run:
        # Resume existing run — don't create new run record
        run_id = args.resume_run
        exp = mgr.get(exp_id)
        # Verify run exists in registry
        run_found = any(r["run_id"] == run_id for r in exp.runs if hasattr(r, '__iter__'))
        if not run_found:
            print(f"Error: run {run_id} not found for {exp_id}", file=sys.stderr)
            sys.exit(1)
        print(f"Resuming run {run_id} for {exp_id}")
        # Don't call mgr.start() — reuse existing run_id
    else:
        # Normal start: create new run
        if exp.has_active_run:
            print(f"Error: already active run", file=sys.stderr)
            sys.exit(1)
        if exp.status != "idle":
            mgr._data[exp_id]["status"] = "idle"
            mgr._save()
        run_id = mgr.start(exp_id)
        print(f"Created run {run_id} for {exp_id}")
    
    # ... rest of systemd-run launch (use run_id as before)
    unit = _systemd_unit_name(exp_id)
    cmd = [
        "sudo", "systemd-run", "--unit", unit,
        "--uid", "quant", "--gid", "quant",
        "--working-directory", project_root,
        "--property=Restart=on-failure",
        "--property=RestartSec=15",
        f"{project_root}/.venv/bin/python3", f"{project_root}/live/run.py",
        "--config", config_path, "--run-id", run_id,
    ]
    subprocess.run(cmd, ...)
    pid = _unit_pid(exp_id)
    if pid:
        mgr.set_pid(exp_id, pid)
    print(f"Started run {run_id}")
```

- [ ] **Step 3: Commit**

```bash
git add live/exp_cli.py
git commit -m "feat: support --resume-run to restart existing run from state"
```

---

## Task 4: admin/server.py — Run 级 API 端点

**Files:**
- Modify: `admin/server.py`

新增 6 个端点，修改 Dashboard Live/Paper 支持 URL 参数。

- [ ] **Step 1: 加 run 启动端点**

```python
@app.post("/api/admin/experiments/{exp_id}/runs/{run_id}/start")
def admin_experiment_run_start(exp_id: str, run_id: str):
    """Start (or resume) a specific run via task queue."""
    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")
    
    if exp.has_active_run:
        raise HTTPException(status_code=409, detail="已有活跃 Run，请先停止")
    
    cmd = (
        f"cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod "
        f".venv/bin/python3 live/exp_cli.py start {exp_id} --resume-run {run_id}"
    )
    session = get_session()
    task = Task(type="shell", params={"cmd": cmd, "cron_command": cmd}, status="pending")
    session.add(task)
    session.commit()
    return {"task_id": task.id, "run_id": run_id}
```

- [ ] **Step 2: 加 run 删除端点**

```python
@app.delete("/api/admin/experiments/{exp_id}/runs/{run_id}")
def admin_experiment_run_delete(exp_id: str, run_id: str):
    """Permanently delete a run and all associated data."""
    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")
    
    if exp.has_active_run:
        raise HTTPException(status_code=409, detail="存在活跃 Run，无法删除")
    
    result = mgr.delete_run(exp_id, run_id)
    return {"status": "ok", "run_id": run_id, "details": result}
```

- [ ] **Step 3: 加 run 清除状态端点**

```python
@app.post("/api/admin/experiments/{exp_id}/runs/{run_id}/clear-state")
def admin_experiment_run_clear_state(exp_id: str, run_id: str):
    """Clear state & checkpoint for a run (keeps BQ/logs)."""
    mgr = ExperimentManager()
    try:
        exp = mgr.get(exp_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Experiment '{exp_id}' not found")
    
    if exp.has_active_run:
        raise HTTPException(status_code=409, detail="存在活跃 Run，无法清除状态")
    
    result = mgr.clear_run_state(exp_id, run_id)
    return {"status": "ok", "run_id": run_id, "details": result}
```

- [ ] **Step 4: 加 run 权益/持仓端点**

```python
@app.get("/api/admin/experiments/{exp_id}/runs/{run_id}/equity")
def admin_experiment_run_equity(exp_id: str, run_id: str):
    """Return equity curve for a specific run."""
    client = _DB_BQ()
    q = f"""
        SELECT bar, equity, cash, portfolio_value, daily_pnl
        FROM {_DB_TABLE("experiment_equity")}
        WHERE exp_id='{exp_id}' AND run_id='{run_id}'
        ORDER BY bar
    """
    rows = list(client.query(q).result())
    return [{"bar": r.bar, "equity": r.equity, "cash": r.cash,
             "portfolio_value": r.portfolio_value, "daily_pnl": r.daily_pnl} for r in rows]

@app.get("/api/admin/experiments/{exp_id}/runs/{run_id}/positions")
def admin_experiment_run_positions(exp_id: str, run_id: str):
    """Return current positions for a specific run."""
    client = _DB_BQ()
    q = f"""
        SELECT symbol, side, qty, price, timestamp
        FROM {_DB_TABLE("experiment_trades")}
        WHERE exp_id='{exp_id}' AND run_id='{run_id}'
        ORDER BY timestamp DESC
    """
    rows = list(client.query(q).result())
    # Aggregate to FIFO positions
    positions: dict[str, dict] = {}
    for r in rows:
        sym = r.symbol
        if sym not in positions:
            positions[sym] = {"symbol": sym, "qty": 0, "cost": 0.0, "trades": []}
        if r.side == "buy":
            positions[sym]["qty"] += r.qty
            positions[sym]["cost"] += r.qty * r.price
        else:
            positions[sym]["qty"] -= r.qty
            positions[sym]["cost"] -= r.qty * r.price
        positions[sym]["trades"].append({
            "side": r.side, "qty": r.qty, "price": r.price, "ts": str(r.timestamp)
        })
    return [p for p in positions.values() if p["qty"] > 0]
```

- [ ] **Step 5: Dashboard Live/Paper 加 URL 参数支持**

修改 `admin_server.py` 中 Dashboard Live/Paper 的 meta 查询，不做大改。前端通过 URL 参数传入 `exp_id` + `run_id` 即可筛选。

- [ ] **Step 6: Commit**

```bash
git add admin/server.py
git commit -m "feat: add run-level API endpoints (start/delete/clear/equity/positions)"
```

---

## Task 5: 前端 — Runs 列表可展开行 + 7 按钮

**Files:**
- Modify: `admin/frontend/src/pages/Experiments.tsx`

重构 LabTab 中 runs 部分：用 Ant Design Table 的 `expandable` 替代当前简单表格，每行可展开显示权益曲线 + 持仓。

- [ ] **Step 1: 加 `handleStartRun` / `handleDeleteRun` / `handleClearRunState`**

```tsx
const handleStartRun = async (expId: string, runId: string) => {
  try {
    const data = await api.post(`/api/admin/experiments/${expId}/runs/${runId}/start`);
    const hide = message.loading(`Starting run ${runId}...`, 0);
    try { await pollTask(data.task_id); hide(); message.success('Run started'); actionRef.current?.reload(); }
    catch (err: any) { hide(); message.error(`Start failed: ${err.message}`); }
  } catch (err: any) { message.error(`Start failed: ${err.message}`); }
};

const handleDeleteRun = async (expId: string, runId: string) => {
  try {
    await api.del(`/api/admin/experiments/${expId}/runs/${runId}`);
    message.success(`Run ${runId} deleted`);
    // Reload runs
    loadRuns(expId);
    actionRef.current?.reload();
  } catch (err: any) { message.error(`Delete failed: ${err.message}`); }
};

const handleClearRunState = async (expId: string, runId: string) => {
  try {
    await api.post(`/api/admin/experiments/${expId}/runs/${runId}/clear-state`);
    message.success(`State cleared for run ${runId}`);
  } catch (err: any) { message.error(`Clear state failed: ${err.message}`); }
};
```

- [ ] **Step 2: 加 `loadRunDetails` — 展开时加载 equity/positions**

```tsx
const [expandedRunKeys, setExpandedRunKeys] = useState<Record<string, any>>({});

const loadRunDetails = async (expId: string, runId: string) => {
  try {
    const [equity, positions] = await Promise.all([
      api.get(`/api/admin/experiments/${expId}/runs/${runId}/equity`),
      api.get(`/api/admin/experiments/${expId}/runs/${runId}/positions`),
    ]);
    setExpandedRunKeys(prev => ({
      ...prev,
      [runId]: { equity: equity || [], positions: positions || [], loading: false },
    }));
  } catch {
    setExpandedRunKeys(prev => ({
      ...prev,
      [runId]: { equity: [], positions: [], loading: false, error: true },
    }));
  }
};
```

- [ ] **Step 3: 重构 runColumns — 加 7 个操作按钮 + expandable**

```tsx
const runColumns: ColumnsType<RunRecord> = [
  { title: 'Run ID', dataIndex: 'run_id', key: 'run_id', width: 200 },
  { title: 'Status', dataIndex: 'status', width: 90,
    render: (_, r) => <Tag color={statusColor[r.status] || 'default'}>{r.status}</Tag> },
  { title: 'Started', dataIndex: 'started_at', width: 160, render: (_, r) => r.started_at?.slice(0,19) || '-' },
  { title: 'Ended', dataIndex: 'ended_at', width: 160, render: (_, r) => r.ended_at?.slice(0,19) || '-' },
  {
    title: '', key: 'actions', width: 340,
    render: (_, r) => (
      <Space size={4}>
        {/* Start — blocked if any run is active */}
        <Tooltip title={detailExp?.has_active_run ? '已有活跃 Run' : '启动此 Run'}>
          <Button size="small" icon={<PlayCircleOutlined />}
            disabled={detailExp?.has_active_run || r.status === 'running'}
            onClick={() => r.status !== 'running' && handleStartRun(detailExp!.exp_id, r.run_id)} />
        </Tooltip>
        {/* Stop — only for running */}
        {r.status === 'running' && (
          <Popconfirm title={`停止 Run ${r.run_id}？`}
            onConfirm={() => handleStopRun(detailExp!.exp_id, r.run_id)}>
            <Button size="small" danger icon={<PauseCircleOutlined />} />
          </Popconfirm>
        )}
        {/* Delete — blocked if running */}
        <Popconfirm title={`永久删除 Run ${r.run_id}？`} description="将删除所有关联数据"
          onConfirm={() => handleDeleteRun(detailExp!.exp_id, r.run_id)}
          disabled={r.status === 'running'} okButtonProps={{ danger: true }}>
          <Tooltip title={r.status === 'running' ? '活跃 Run 无法删除' : '删除'}>
            <Button size="small" danger icon={<DeleteOutlined />} disabled={r.status === 'running'} />
          </Tooltip>
        </Popconfirm>
        {/* Clear state — blocked if running */}
        <Popconfirm title={`清除 Run ${r.run_id} 状态？`} description="将删除 checkpoint/state，保留 BQ 数据"
          onConfirm={() => handleClearRunState(detailExp!.exp_id, r.run_id)}
          disabled={r.status === 'running'} okButtonProps={{ danger: true }}>
          <Tooltip title={r.status === 'running' ? '活跃 Run 无法清除' : '清除状态'}>
            <Button size="small" icon={<ClearOutlined />} disabled={r.status === 'running'} />
          </Tooltip>
        </Popconfirm>
        {/* Log */}
        <Tooltip title="查看日志">
          <Button size="small" icon={<FileTextOutlined />}
            onClick={() => handleViewRunLog(r.run_id)} />
        </Tooltip>
        {/* Detail — jump to Dashboard */}
        <Tooltip title="实验详情">
          <Button size="small" icon={<LinkOutlined />}
            onClick={() => navigate(`/dashboard?tab=live&exp_id=${detailExp!.exp_id}&run_id=${r.run_id}`)} />
        </Tooltip>
      </Space>
    ),
  },
];
```

- [ ] **Step 4: 替换 Simple Table 为可展开 Table**

```tsx
<Table<RunRecord>
  dataSource={runs}
  rowKey="run_id"
  loading={runsLoading}
  size="small"
  columns={runColumns}
  pagination={false}
  expandable={{
    expandedRowRender: (record) => {
      const details = expandedRunKeys[record.run_id];
      if (!details) {
        // Trigger load
        loadRunDetails(detailExp!.exp_id, record.run_id);
        return <Spin />;
      }
      return (
        <Row gutter={16}>
          <Col span={16}>
            <Text strong>权益曲线</Text>
            <ReactECharts option={buildEquityChart(details.equity)} style={{ height: 200 }} />
          </Col>
          <Col span={8}>
            <Text strong>当前持仓</Text>
            <Table size="small" dataSource={details.positions} columns={posColumns} pagination={false} />
          </Col>
        </Row>
      );
    },
    onExpand: (expanded, record) => {
      if (expanded) loadRunDetails(detailExp!.exp_id, record.run_id);
    },
  }}
/>
```

- [ ] **Step 5: 移除旧 Drawer 里的 equity/positions 区域**

在 `openDetail` 中移除 `setEquityLatest` / `setPositions` / `equityLoading` / `positionsLoading` 相关调用。Drawer 内只保留 Descriptions（元信息）+ Runs 列表。

- [ ] **Step 6: 加 `ClearOutlined`, `LinkOutlined` imports**

```tsx
import { ClearOutlined, LinkOutlined } from '@ant-design/icons';
```

- [ ] **Step 7: Commit**

```bash
git add admin/frontend/src/pages/Experiments.tsx
git commit -m "feat: expandable run rows with 7 action buttons, inline equity/positions"
```

---

## Task 6: 前端 — Dashboard Live/Paper 加 URL 直链

**Files:**
- Modify: `admin/frontend/src/pages/DashboardLive.tsx`
- Modify: `admin/frontend/src/pages/DashboardPaperRun.tsx`

- [ ] **Step 1: DashboardLive 加 `useSearchParams`**

```tsx
import { useSearchParams } from 'react-router-dom';

// In component:
const [searchParams] = useSearchParams();
const urlExpId = searchParams.get('exp_id') || '';
const urlRunId = searchParams.get('run_id') || '';

// If URL params present, auto-select experiment + run
useEffect(() => {
  if (urlExpId) {
    setSelectedExp(urlExpId);
    if (urlRunId) setSelectedRun(urlRunId);
  }
}, []);
```

- [ ] **Step 2: DashboardPaperRun 同样处理**

同上。

- [ ] **Step 3: Commit**

```bash
git add admin/frontend/src/pages/DashboardLive.tsx admin/frontend/src/pages/DashboardPaperRun.tsx
git commit -m "feat: support ?exp_id=xxx&run_id=yyy URL params in dashboard live/paper"
```

---

## Task 7: 构建部署 + 端到端验证

- [ ] **Step 1: 构建前端**

```bash
cd /opt/quant-dev/admin/frontend && npm run build
```

- [ ] **Step 2: 部署到 prod**

```bash
cp -r admin/frontend/dist/* /opt/quant-prod/admin/frontend/dist/
cp live/experiment_manager.py live/runner.py live/exp_cli.py admin/server.py /opt/quant-prod/
# restart quant-admin
```

- [ ] **Step 3: 验证**

```bash
# 1. 启动一个 live Run → 验证 state 写入新路径
# 2. 刷新前端 → 验证 Runs 列表可展开 + 7 按钮功能
# 3. 点击详情 → 验证跳转 Dashboard 并自动选中
# 4. 点击删除 → 验证级联删除
# 5. 点击清除状态 → 验证 checkpoint 删除
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: build and deploy run lifecycle refactor"
```

---

## 方案改动清单

| 维度 | 原来 | 重构后 |
|------|------|--------|
| State 路径 | `/var/quant/state/{strategy}/` | `/var/quant/state/{exp_id}/{run_id}/` |
| Run 启动 | 只能新 Run，不能恢复 | 新 Run + 恢复已有 Run（有 state 则恢复） |
| Run 删除 | 无 | 级联删 BQ + state + output + 日志 |
| Run 清除状态 | 无 | 删 checkpoint + state，保留 BQ |
| Equity/Positions | 实验级，Drawer 内 | Run 级，可展开内联 |
| Dashboard 跳转 | 手动选 | 一键直链 `?exp_id=xxx&run_id=yyy` |
| 按钮数量 | 2 (stop + log) | 7 (start/stop/delete/clear/log/detail/expand) |
