# Admin 前端开发历程与知识库

> 创建: 2026-06-05 | 记录: Jarvis
>
> 记录 Admin 管理平台从 0 到上线的完整历程：需求对齐、踩坑、设计方案、调试方法。

---

## 目录

1. [需求对齐与设计演化](#1-需求对齐与设计演化)
2. [踩坑记录（16 坑）](#2-踩坑记录16-坑)
3. [交互设计理念](#3-交互设计理念)
4. [Debug 思路与方法](#4-debug-思路与方法)

---

## 1. 需求对齐与设计演化

### 1.1 从 Dashboard 到 Admin 的演进

**原始状态**：Dashboard 是 Vue 3 单文件 SPA (:8090)，功能有限：
- 5 个 Tab：Overview / Live / Paper Run / Pipeline / Alerts
- 只能看，不能操作
- 图表使用 Chart.js + Plotly.js CDN

**迁移过程**：
- 用户要求"不能 SSH 进服务器操作"
- 所有运维操作需要 Web UI
- → 创建 Admin 平台 (:8091)，React + Ant Design Pro 全功能后台

### 1.2 重大设计决定

#### 决定 1：Dashboard 整合到 Admin

**方案 A**：Admin 和 Dashboard 各自独立，Admin 嵌入式显示 Dashboard
**方案 B**：Admin 整并 Dashboard 所有 API，彻底替代

**选择 B**。原因：
- 减少端口数量（仅 :8091）
- 统一认证和部署
- 前端 React 生态支持更好的图表库（ECharts）
- Dashboard API 迁移到 `/api/admin/dashboard/*` 前缀

#### 决定 2：实验管理拆分为"配置模板"和"实验室"

**背景**：原设计将所有实验操作混在一个页面上，包括创建、配置、运行。

**拆分后**：
- **实验配置 Tab**：管理 YAML 模板文件（CRUD + 从模板创建实验）
- **实验室 Tab**：管理运行中的实验实例（start/stop/restart/clear/delete/详情）
- 各有 Live/Paper/Prod 子 Tab，过滤不同类型

**设计理念**："模板 vs 实例分离"——YAML 配置文件是蓝图，运行中的实验是实例。修改模板不影响已运行的实验。

#### 决定 3：ML 子系统升级为三 Tab 体系

**原始**：模型 & 策略 Tab 功能混杂。

**升级**：
- **数据集 Tab**：管理训练数据集（BQ ML 宽表生成/查看/删除）
- **ML 配置 Tab**：管理 ML 配置 YAML（与实验配置同理念——模板）
- **模型中心 Tab**：管理已训练模型（与实验室同理念——实例）

**命名统一**：数据集（Dataset）→ ML 配置（Config Template）→ 模型中心（Model Center）形成完整 ML 流水线

#### 决定 4：Dashboard 子 Tab 从 5 扩展为 7

原 Dashboard 5 Tab → 新增 Prod 和 Debug：
- Overview / Live / Paper Run / **Prod** / Pipeline / Alerts / **Debug**
- Prod 只读，Debug 隔离开发实验

#### 决定 5：任务队列架构

**为什么不直接调用 subprocess？**
- 前端 HTTP 请求有超时（30s），训练/回填可能跑 30 分钟
- 需要异步执行 + 状态跟踪 + 日志记录

**方案**：SQLite Task 表 + worker 进程
- 前端 POST → server 创建 Task(pending)
- Worker 轮询 pending → subprocess.run → done/failed
- 前端轮询 GET /api/tasks/{id} 获取状态

**优势**：
- 按钮状态持久化（页面刷新后仍可见任务状态）
- 支持日志记录（worker 管道写到 log 文件）
- 解耦前端和后端执行

### 1.3 技术选型

| 层次 | 技术 | 原因 |
|------|------|------|
| 前端框架 | React 18 + TypeScript | 强类型、生态丰富、组件化 |
| UI 库 | Ant Design Pro | 中文友好、Table/Form/Modal 一应俱全 |
| 图表 | ECharts (Apache) | 比 Chart.js 更强（双轴、缩放、大数据量） |
| 构建 | Vite | 秒级 HMR、TypeScript 原生支持 |
| 后端 | FastAPI | 异步、自动文档、Pydantic 验证 |
| ORM | SQLAlchemy | Task 表 + MlDataset/MlConfig 实体 |
| DB | SQLite `/var/quant/admin.db` | 零配置、进程内锁、数据量小 |
| 任务队列 | 自建 worker 轮询 | 简单可靠、无外部依赖 |

---

## 2. 踩坑记录（16 坑）

### 坑 1：exp_cli.py status bug — restart 后状态写死 paused

**现象**：Admin 点 Restart → 实验卡在 `paused` 状态，不再 `running`

**根因**：`live/exp_cli.py` 的 `restart()` 函数先 `stop()` 再 `start()`。`stop()` 调用 `_set_status("paused")`。`start()` 成功启动了进程，但没有把状态改回 `running`。

**修复**：在 `start()` 成功 fork 进程后，调用 `_set_status("running")`

**教训**：状态机每个 transition 都要检查终态是否正确。`restart = stop + start` 不等于 `running`，需要显式设置。

---

### 坑 2：路由顺序问题 — /delete 被 /{action} 拦截

**现象**：`POST /api/admin/experiments/live_us_ml_v2/delete` 返回 400 "Unknown action: delete"

**根因**：FastAPI 路由注册顺序问题
```python
# ❌ 错误：/{action} 在前面，{action} 匹配 "delete"
@app.post("/api/admin/experiments/{exp_id}/{action}")
@app.delete("/api/admin/experiments/{exp_id}/delete")

# ✅ 正确：具体路由在前
@app.delete("/api/admin/experiments/{exp_id}/delete")
@app.post("/api/admin/experiments/{exp_id}/clear")
@app.post("/api/admin/experiments/{exp_id}/{action}")
```

**修复**：将 `/delete` 和 `/clear` 路由移到 `/{action}` 之前注册。

**教训**：**FastAPI 按注册顺序匹配路由**。通配路由（`{action}`）会吞掉所有请求。具体路由必须在通配路由之前。

---

### 坑 3：Path 导入缺失导致 500 错误

**现象**：`GET /api/admin/experiments/configs` 返回 500 Internal Server Error

**根因**：server.py 中使用了 `Path()` 但没有导入
```python
# ❌ 缺少导入
config_dir = Path("/opt/quant-prod/live/configs")  # NameError

# ✅ 需要
from pathlib import Path
```

**教训**：IDE 的自动补全不会提醒 `pathlib.Path` 未导入。Python 在不同函数内重复 `Path()` 时容易遗漏。建议统一在文件顶部导入。

---

### 坑 4：deploy.sh npm build 静默失败 — pipe 吞错

**现象**：修改前端代码后 `deploy.sh` 执行成功，但 Admin 页面没变化。

**根因**：`deploy.sh` 中 `npm run build` 的输出被 pipe 到 `/dev/null` 吞掉了错误：
```bash
npm run build > /dev/null 2>&1 && cp -r dist/* ...  # 构建失败但被吞
```
构建失败时 `npm` 返回非零 exit code，但 pipe 吞了 stderr，无法看到具体错误。

**修复**：保留 `npm run build` 的输出和错误信息，构建失败立即退出：
```bash
npm run build || { echo "BUILD FAILED"; exit 1; }
```

**教训**：CI/CD 脚本不要吞掉构建输出。每次构建都应该可见、可审计。

---

### 坑 5：cloudflared quick tunnel 过期/重启不稳定

**现象**：Admin :8091 公网不可访问，"tunnel unreachable"

**根因**：`cloudflared tunnel --url localhost:8091` (quick tunnel) 在 session 断开后过期。重启 VM 后没有自动恢复。

**修复**：使用 systemd 管理 cloudflared（`cloudflared-tunnel.service`），每次 VM 启动自动创建新 tunnel。

**教训**：quick tunnel 不适合生产使用。应该用 Named Tunnel + systemd 持久化。

---

### 坑 6：状态保存权限问题 — state dir 属于 DangXuan

**现象**：quant 用户运行实验时写 `/var/quant/state/` 失败，"Permission denied"

**根因**：`/var/quant/state/` 目录是 `DangXuan` 用户创建的，owner 是 `DangXuan`（不是 `quant`）。

**修复**：
```bash
sudo mkdir -p /var/quant/state/
sudo chown quant:quant /var/quant/state/
```

**教训**：所有 `/var/quant/` 子目录都应该属于 `quant` 用户。部署脚本应该自动设置权限。

---

### 坑 7：registry.json 被外部清空导致实验残留

**现象**：Admin 显示实验列表为空，但 BQ 中实验数据仍存在。

**根因**：`/var/quant/experiments/registry.json` 被某个脚本清空（可能是手动编辑或部署脚本覆盖）。ExperimentManager 加载空 registry → 显示 0 个实验。

**修复**：增加 registry 保护 — 加载空 registry 时检查 BQ 中是否有活跃实验数据。Admin clear/delete 操作显式调用 `mgr.delete()` 而不是手动编辑 registry。

**教训**：JSON 文件作为注册表非常脆弱。关键元数据应该存在 DB（SQLite）中，而不是裸 JSON 文件被各种脚本随意读写。

---

### 坑 8：experiment.id 没注入 YAML 导致日志名和 auto-register 都出问题

**现象**：从模板创建实验后，日志文件名为 `live_.log`（缺少 exp_id），auto-register 失败。

**根因**：实验配置 YAML 中的 `experiment.id` 字段用于：
1. LiveRunner 的日志文件命名（`live_{exp_id}.log`）
2. DashboardObserver 自动注册实验到 registry

从模板创建实验时，只是 `shutil.copy2(template, new_path)`，没有替换 YAML 中的 `id`。

**修复**：`create-from-config` API 在复制模板后解析 YAML，注入 `experiment.id = new_id`：
```python
cfg = yaml.safe_load(new_path.read_text())
cfg.setdefault("experiment", {})["id"] = new_id
new_path.write_text(yaml.dump(cfg))
```

**教训**：配置模板中的动态字段必须在"实例化"时注入。不能假设模板已经包含正确值。

---

### 坑 9：record_experiment 只在 shutdown 时调用，崩溃就丢失 meta

**现象**：实验进程被 kill 或崩溃后，`output/live/experiments/` 中没有 meta 文件，Admin Dashboard 看不到该实验。

**根因**：`live/config.py` 的 `record_experiment()` 只在 `atexit` 和 `LiveRunner.shutdown()` 中调用。如果进程被 `SIGKILL` 或异常崩溃，不会触发 atexit。

**修复**：在实验启动时（`start()` 成功后）立即写一份初始 meta 文件：
```python
# 在 _spawn() 成功后立即 record
record_experiment(exp_id, config, status="running")
```

**教训**：元数据写入不能只依赖优雅退出。应该在每个关键生命周期节点（启动、暂停、停止）都持久化。

---

### 坑 10：MLflow 3.x API POST to GET 不兼容

**现象**：Admin 模型中心"Promote to Production" 返回 405 Method Not Allowed

**根因**：MLflow 2.x 的 stage transition 是 `POST /model-versions/transition-stage`，MLflow 3.x 改成了 `GET`。

**修复**：先尝试 POST，失败后 fallback 到 GET：
```python
r = requests.post(f"{MLFLOW_API}/model-versions/transition-stage", json=...)
if r.status_code != 200:
    r2 = requests.get(f"{MLFLOW_API}/model-versions/transition-stage", params=...)
```

**教训**：API 兼容性要同时考虑上游版本差异。对外部服务（MLflow）的调用要有降级方案。

---

### 坑 11：数据集生成 SQL 用 timestamp 而非 date 列名

**现象**：数据集生成按钮执行后 BQ 报错 "Unrecognized name: timestamp"

**根因**：生成数据集的 SQL 中用了 `timestamp` 作为列名，但 `factor_values` 表的日期列是 `date`（DATE 类型，不是 TIMESTAMP）。

**修复**：将 SQL 中的 `timestamp` 改为 `date`，并在 WHERE 条件和 SELECT 中都使用 `date`。

**教训**：SQL 列名必须查 `INFORMATION_SCHEMA.COLUMNS` 确认，不能猜！同样的坑还有 `fwd_ret_5d` vs `us_ret_5d`。

---

### 坑 12：标签列 fwd_ret_5d vs us_ret_5d 映射

**现象**：数据集生成后 `fwd_ret_5d` 列全为 NULL

**根因**：数据集的 `label` 字段是 `fwd_ret_5d`，但在 `factor_values` 表中，这个因子存储为 `{market}_fwd_ret_5d`（如 `us_fwd_ret_5d`）。

修复：在 SQL PIVOT 中将 label 映射到 BQ 中的 factor_id：
```python
label_factor_id = market_prefix + ds.label.replace("fwd_", "")  # "us_fwd_ret_5d" → "us_ret_5d"
# ❌ 实际应该是 "us_fwd_ret_5d" → "us_fwd_ret_5d"（不要去掉 fwd_）
```

最终修复：`label_factor_id = market_prefix + ds.label` → 直接用原始 label + 前缀。

**教训**：列名映射规则必须在数据生成代码中明确文档化。`label` 字段和 BQ factor_id 的对应关系不是"一看就懂"的。

---

### 坑 13：Pipeline logger 没有 handler，python -c 时无输出

**现象**：Admin 触发训练后日志文件为空，worker 执行也无输出

**根因**：训练脚本通过 `python -c "..."` 内联执行时，`logging.basicConfig()` 没有被调用，因为：
- `ml/pipeline.py` 中使用了 `logging.getLogger(__name__)`
- 但没有配置 handler（没有 `logging.basicConfig()` 或 `FileHandler`）
- 在模块导入场景下，root logger 没有 handler → 所有日志被丢弃

**修复**：在 `python -c` 命令中显式配置 logging：
```python
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')
```

**教训**：`python -c` 执行的代码不经过 `__main__` 入口，`logging.basicConfig()` 必须显式调用。所有 task queue 命令模板都加上了 logging 初始化。

---

### 坑 14：worker timeout 300s 杀掉了长时间训练

**现象**：训练任务跑了 5 分钟后被 worker 杀掉，状态显示 "failed"。

**根因**：worker.py 中 `subprocess.run(cmd, timeout=300)` —— 300 秒超时。LightGBM + Optuna 调参可能需要 10-60 分钟。

**修复**：将默认 timeout 提升到 3600s（1 小时），并允许 task params 中自定义 timeout。

**教训**：subprocess.run 的 timeout 必须足够大。模型训练、因子批量计算、数据回填都是长耗时任务。timeout 应该根据 task type 动态设置。

---

### 坑 15：实验删除后 output/live/experiments/ 残留 meta

**现象**：Admin 删除实验后，"Dashboard Overview" 仍然显示该实验（因为 meta API 从 `output/live/experiments/` 读）。

**根因**：Admin 的 delete API 清理了 BQ + state + logs + registry，但遗漏了 `output/live/experiments/{exp_id}/` 目录。

**修复**：在 delete API 中添加清理 output 目录的逻辑：
```python
exp_meta = output_base / "experiments" / exp_id
if exp_meta.exists():
    shutil.rmtree(exp_meta)
```

**教训**：删除操作的数据残留检查清单：
1. BQ 表（experiment_equity, experiment_trades, experiment_runs）
2. /var/quant/state/（持仓状态）
3. /var/log/quant/（实验日志）
4. output/live/（CSV 权益文件）
5. output/live/experiments/（Dashboard meta）
6. registry.json（实验注册表）
7. Config YAML 文件

---

### 坑 16：cp 文件到 prod 绕过 git flow 导致前后端不同步

**现象**：修了一个前端 bug，手动 `cp dist/* /opt/quant-prod/admin/frontend/dist/` 后 Admin 页面还是旧的。

**根因**：
1. `cp -r dist/*` 可能不覆盖已有文件（尤其当 dist 有不同 hash 时）
2. 后端 server.py 的修改没有同时 cp 过去
3. 前端 dist 和后端代码版本不一致

**后果**：前端的 API 调用可能指向了新接口，但后端 server.py 还是旧版本的 → 404/500

**修复**：严格走 git flow：
```bash
# 禁止
cp -r dist/* /opt/quant-prod/admin/frontend/dist/  # ❌ 禁止
cp server.py /opt/quant-prod/admin/                # ❌ 禁止

# 正确
cd /opt/quant-dev && git add -A && git commit -m "..."
git push → PR → CI → merge stable → CD 自动部署
```

**教训**：**永远不要手动 cp 文件到 prod。** 即使"只是一行代码"也走 CI/CD。手动 cp 会导致：
- 前后端不同步 → 诡异的 404/500
- dist hash 不匹配 → 浏览器缓存旧 JS
- 没有部署记录 → 出问题时无法回滚

---

## 3. 交互设计理念

### 3.1 配置模板 vs 实例分离

**核心原则**：模板是蓝图，实例是产物。

| 概念 | 实验配置 | ML 配置 |
|------|---------|---------|
| 模板（蓝图） | 实验配置 Tab → YAML 文件 | ML 配置 Tab → YAML 文件 |
| 实例（产物） | 实验室 Tab → 运行中的实验 | 模型中心 Tab → 已训练的模型 |

**实现**：
- 模板可以被多次复制创建不同实例
- 模板修改不影响已存在的实例
- 实例删除后模板保留

### 3.2 所有按钮左对齐、刷新按钮右对齐

**原则**：操作按钮在内容区左侧，刷新/辅助按钮在右侧。

```
[Start] [Stop] [Restart] [Clear]                        [🔄 Refresh]
═══════════════════════════════════════════════════════════════════════
实验表格
```

**原因**：符合阅读习惯（左→右），操作优先于辅助。

### 3.3 二次确认在所有破坏性操作

**触发条件**：stop, clear, delete, restart, archive

**实现**：Ant Design `Modal.confirm()` + 明确说明影响：
```
确认删除实验 live_us_ml_v2？
此操作将清理：
• BQ 所有权益/交易数据
• 状态文件 /var/quant/state/
• 实验日志 /var/log/quant/
• 注册表记录

[取消]  [确认删除]
```

### 3.4 按钮状态持久化（SQLite task queue）

**原则**：页面刷新后按钮状态不丢失

**实现**：
- 每个操作 → Task 记录（SQLite）
- 前端不维护按钮状态（如 "running" / "paused"）
- 从 SQLite Task 表读取
- 按钮 disable 逻辑由后端实验状态决定

### 3.5 日志驱动（任务走 worker 管道写日志 → 日志中心查看）

**原则**：所有后台任务的输出都写入日志文件，通过日志中心查看。

**实现**：
```
前端点击训练
→ POST /api/admin/ml/train → Task(pending)
→ worker 执行:
   python -c "..." 2>&1 | while read l; do echo "$(date) $l"; done | tee -a /var/log/quant/prod/train/{name}.log
→ 用户切到"日志浏览"Tab → 选择模块 train → 实时查看训练输出
```

**好处**：
- 不需要实现复杂的 progress callback
- 利用现有日志系统
- 用户可以在日志中心集中查看所有任务输出
- WebSocket 实时 tail

---

## 4. Debug 思路与方法

### 4.1 调试检查清单

当 Admin 页面功能异常时，按以下顺序排查：

```
第 1 步：检查后端 API 返回（curl 验证）
    ↓
第 2 步：检查前端 JS 加载情况（dist/ hash）
    ↓
第 3 步：检查 worker/journalctl 日志
    ↓
第 4 步：检查 sqlite 中 task 状态
    ↓
第 5 步：用 Python 脚本直接模拟后端逻辑快排
```

### 4.2 第 1 步：检查后端 API 返回

```bash
# 直接 curl 后端，看返回内容
curl -s http://localhost:8091/api/admin/experiments | python3 -m json.tool

# 检查 HTTP 状态码
curl -s -o /dev/null -w "%{http_code}" http://localhost:8091/api/admin/ml/datasets

# 看详细响应头
curl -sv http://localhost:8091/api/admin/factors 2>&1 | head -40
```

**常见问题**：
- `404` → 路由未注册或路径错误
- `500` → 后端 Python 异常（查 `journalctl -u quant-admin -f`）
- `[]` 空返回 → BQ 表中无数据或查询条件过滤掉了所有行

### 4.3 第 2 步：检查前端 JS 加载情况

```bash
# 检查 dist 文件是否最新
ls -la /opt/quant-prod/admin/frontend/dist/assets/index-*.js
md5sum /opt/quant-prod/admin/frontend/dist/assets/index-*.js
md5sum /opt/quant-dev/admin/frontend/dist/assets/index-*.js

# HTTP 访问看 200 还是 304
curl -sI http://localhost:8091/assets/index-*.js | head -5
```

**常见问题**：
- dist hash 不匹配 → 浏览器缓存旧 JS → Ctrl+F5 硬刷新
- index.html 引用的 JS hash 在 dist/ 中不存在 → 构建不完整
- StaticFiles 返回 HTML 而不是 JS → 路由不匹配

### 4.4 第 3 步：检查 worker/journalctl 日志

```bash
# Admin server 日志
sudo journalctl -u quant-admin -n 50 --no-pager

# Worker 日志
sudo journalctl -u quant-admin-worker -n 50 --no-pager

# 应用日志（如果配置了文件 handler）
tail -f /var/log/quant/prod/train/*.log
```

**常见问题**：
- `ModuleNotFoundError` → worker 的 PYTHONPATH 不对
- `Permission denied` → worker 以错误用户运行
- `subprocess.TimeoutExpired` → timeout 太小

### 4.5 第 4 步：检查 sqlite 中 task 状态

```bash
sqlite3 /var/quant/admin.db "SELECT id, type, status, params, substr(result,0,200) FROM task ORDER BY id DESC LIMIT 10;"
```

**分析**：
- `status=pending` 很久 → worker 没有在运行
- `status=failed, result=...` → 查看错误信息
- `status=running` 很久 → task 卡住了

### 4.6 第 5 步：用 Python 脚本直接模拟后端逻辑快排

```bash
cd /opt/quant-prod && PYTHONPATH=/opt/quant-prod .venv/bin/python3 << 'EOF'
# 模拟 ExperimentManager
from live.experiment_manager import ExperimentManager
mgr = ExperimentManager()
print("Experiments:", len(mgr._data))
for eid, e in mgr._data.items():
    print(f"  {eid}: status={e.get('status')}, pid={mgr.get_pid(eid)}")

# 模拟 BQ 查询
from google.cloud import bigquery
client = bigquery.Client(project="deductive-notch-495015-c2")
rows = client.query("SELECT table_name FROM quant.INFORMATION_SCHEMA.TABLES").result()
print("Tables:", [r.table_name for r in rows])

# 模拟 MLflow API
import requests
r = requests.get("http://localhost:5000/api/2.0/mlflow/registered-models/search", timeout=5)
print("MLflow models:", [m["name"] for m in r.json().get("registered_models", [])])
EOF
```

**优势**：绕过前端、FastAPI、worker 三层，直接在 Python 环境中验证数据。如果这里也失败 → 问题在底层（BQ/MLflow/文件权限）。

### 4.7 快速验证模式

当需要快速测试一个 API 改动时，推荐用 `uvicorn` reload 模式在 dev 环境启动：

```bash
cd /opt/quant-dev && PYTHONPATH=/opt/quant-dev .venv/bin/python3 -m uvicorn admin.server:app --host 0.0.0.0 --port 8092 --reload
```

**注意**：
- Dev 环境用 8092 端口，不干扰 prod 的 8091
- `--reload` 自动检测代码变化重启
- `PYTHONPATH` 设为 dev 目录（不是 prod）
- BQ 查询量可能大（dev 和 prod 共享同一个 project），避免在生产高峰期测试

---

## 5. 附录：关键代码片段

### 5.1 Worker 主循环（简化版）

```python
import subprocess, time
from admin.models import init_db, get_session, Task

init_db()
while True:
    session = get_session()
    task = session.query(Task).filter(Task.status == "pending").order_by(Task.created_at).first()
    if task:
        task.status = "running"
        session.commit()
        try:
            cmd = task.params.get("cmd", "")
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3600)
            task.result = r.stdout[-5000:] + "\n" + r.stderr[-2000:]
            task.status = "done" if r.returncode == 0 else "failed"
        except Exception as e:
            task.result = str(e)[:500]
            task.status = "failed"
        session.commit()
    else:
        time.sleep(2)
```

### 5.2 dataset 生成 SQL 模板

```sql
CREATE TABLE deductive-notch-495015-c2.ml_dataset.{table_name} AS
WITH raw AS (
    SELECT symbol, date, factor_id, value,
           CASE
               WHEN date BETWEEN '{train_start}' AND '{train_end}' THEN 'train'
               WHEN date BETWEEN '{val_start}' AND '{val_end}' THEN 'val'
               WHEN date BETWEEN '{test_start}' AND '{test_end}' THEN 'test'
           END AS split
    FROM deductive-notch-495015-c2.quant.factor_values
    WHERE factor_id IN UNNEST(@factor_ids)
      AND date BETWEEN '{train_start}' AND '{test_end}'
)
SELECT symbol, date, split,
       {PIVOT_COLS},
       MAX(CASE WHEN factor_id = '{label_factor_id}' THEN value END) AS `{label_col}`
FROM raw
WHERE split IS NOT NULL
GROUP BY symbol, date, split
```
