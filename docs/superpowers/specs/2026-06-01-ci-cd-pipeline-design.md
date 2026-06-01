# CI/CD Pipeline Design

> 日期: 2026-06-01  
> 状态: 设计完成，待审阅  
> 决策: 方案2 — PR merge → CI 全量测试 → 自动部署 + 自动回滚

---

## 1. 动机

当前 `/opt/quant` 同时承载开发和所有生产任务：

- 代码修改和部署混在同一个目录
- `deploy.yml` 直接在 VM 上 `git reset --hard origin/main`
- 无 staging 环境，无回滚能力
- 全局 pip 依赖，开发装包可能意外影响生产 cron

需要一套 CI/CD 流程将开发与生产隔离开。

---

## 2. 目录与分支架构

```
GitHub
├── stable          ← 生产分支（受保护，只能通过 PR merge 进入）
└── main            ← 集成分支（PR 目标，CI 在此运行）
    └── feature/*   ← 开发分支

VM 上
/opt/
├── quant-prod/     ← git clone --branch stable（只读，所有生产服务指向这里）
│   ├── .venv/       ← 生产虚拟环境
│   └── requirements.txt
│
└── quant-dev/      ← git clone --branch main（读写，日常开发和实验）
    ├── .venv/       ← 开发虚拟环境
    └── requirements.txt
```

| | prod | dev |
|------|------|-----|
| 分支 | `stable` | `main` / `feature/*` |
| 代码修改 | ❌ 禁止直接修改 | ✅ 正常开发 |
| 文件权限 | `quant:quant` 只读核心代码 | `DangXuan:DangXuan` 读写 |
| 谁用 | cron / systemd / 实盘 | 开发 + paper 实验 + notebook |
| Python | `.venv/bin/python` | `.venv/bin/python` |

两个目录完全独立，互不影响。

---

## 3. 生产 vs 实验划分

| 🛡️ 生产 (`quant-prod`) | 🧪 实验 (`quant-dev`) |
|------------------------|----------------------|
| ws_collector | Paper 交易 (Exp1/Exp2) |
| 所有 collector cron (US/HK/Crypto 1d) | 回测 |
| 所有 BQ loader cron | ML 训练/调参 |
| F10 采集 + 入库 | Notebooks |
| Quality checks | 策略研究 |
| Factor 例行计算 | |
| 实盘交易（真实下单） | |

实验可以随便改 `quant-dev` 代码，不影响生产数据链路。

---

## 4. CI Pipeline（PR → main）

每个 PR 合并到 `main` 之前触发：

```
PR opened/updated
  │
  ├── lint       → ruff check + format --check（全项目）
  ├── typecheck  → mypy --strict（核心模块，忽略 mlflow/futu 等无 stub 包）
  ├── unit-test  → pytest（全量: engine/ live/ strategies/ ml/ factors/ collectors/ quality/）
  └── security   → 检查 PR 是否改了 deploy.yml / systemd/ / scripts/cron_wrapper.sh
                    → 改动时在 PR 自动评论 ⚠️ 标注
```

**CI 用 `requirements.txt` 安装依赖**，不再逐个 `pip install`。

**当前 CI 只测 `collectors/` 和 `quality/`。改进后覆盖所有核心模块。**

---

## 5. CD Pipeline（merge → stable → 自动部署）

```
PR merged to stable
  │
  ├── ① 备份当前生产状态
  │     └── 记录当前 commit hash → /opt/quant-prod/.deploy_history
  │
  ├── ② Git fetch + checkout
  │     └── cd /opt/quant-prod && git fetch origin stable && git reset --hard origin/stable
  │
  ├── ③ 依赖同步
  │     └── .venv/bin/pip install -r requirements.txt
  │
  ├── ④ Smoke test
  │     ├── .venv/bin/python -c "import engine, live, strategies, factors, ml"
  │     ├── .venv/bin/python -c "from live.config import load_config; load_config('exp1_ml_us')"
  │     └── .venv/bin/python -c "import quality; ..."
  │     │
  │     └── ❌ 任一失败 → 自动回滚到备份 commit → 发告警
  │
  ├── ⑤ 重启受影响的生产服务
  │     ├── sudo systemctl restart ws-collector
  │     └── 如果实盘交易系统在跑，一并重启
  │
  ├── ⑥ 部署后验证
  │     ├── sudo systemctl is-active ws-collector → 确认 running
  │     └── tail -20 /home/quant/logs/ws-collector.log → 无 ERROR
  │     │
  │     └── ❌ 服务未成功启动 → 自动回滚 → 发告警
  │
  └── ⑦ 记录部署结果
        └── /opt/quant-prod/.deploy_history: timestamp, commit, status, 触发人
```

**部署脚本:** `scripts/deploy.sh` 封装备份→checkout→smoke→restart→verify 全流程。

**SSH 重试:** 连 VM 失败时重试 3 次（间隔 30s），全失败则标记 CI failed。

**并发控制:** `concurrency: cancel-in-progress` 防止多个 merge 同时部署。

---

## 6. 回滚机制

| 触发条件 | 回滚方式 | 说明 |
|----------|----------|------|
| Smoke test 不通过 | **自动** | 代码 import 不了，立即回退 |
| 服务重启后挂掉 | **自动** | ws_collector 没起来，采集全断 |
| 部署后数据断档（quality 检测到） | **告警 → 手动** | 可能是市场休市，不能自动判死刑 |
| 逻辑 bug（代码能跑但结果错） | **手动** | 自动系统无法判断业务正确性 |

**自动回滚流程:**
```bash
# 从 .deploy_history 读取上次成功 commit
OLD_COMMIT=$(jq -r 'last(.[] | select(.status=="success")) | .commit' /opt/quant-prod/.deploy_history)
cd /opt/quant-prod && git checkout $OLD_COMMIT
.venv/bin/pip install -r requirements.txt
sudo systemctl restart ws-collector
# 验证 → 写回滚记录到 .deploy_history
```

**手动回滚:**
```bash
cd /opt/quant-prod && ./scripts/rollback.sh
```

回滚历史格式:
```json
[
  {"time": "2026-06-01T11:00Z", "commit": "abc1234", "status": "success", "trigger": "github"},
  {"time": "2026-06-01T12:00Z", "commit": "def5678", "status": "failed", "trigger": "github"},
  {"time": "2026-06-01T12:01Z", "commit": "abc1234", "status": "rolled_back", "trigger": "auto"}
]
```

---

## 7. 依赖管理

每边独立 venv，共享 `requirements.txt` 作为 lockfile：

```
/opt/
├── quant-prod/.venv/   ← 生产，部署时 pip install -r requirements.txt
└── quant-dev/.venv/    ← 开发，可以试新版包
```

**依赖更新流程:**
1. 开发时在 `quant-dev/.venv` 里 `pip install whatever`
2. 需要固化: `.venv/bin/pip freeze > requirements.txt`
3. PR 改了 `requirements.txt` → CI 用这个文件装依赖跑测试
4. merge 到 stable → 部署脚本在 `quant-prod` 里跑 `.venv/bin/pip install -r requirements.txt`

**需要改的地方:**

| 地方 | 改动 |
|------|------|
| cron | `python3.12` → `/opt/quant-prod/.venv/bin/python3.12` |
| systemd service | `ExecStart` 指向 `.venv/bin/python3.12` |
| deploy.yml | 新增 `.venv/bin/pip install -r requirements.txt` |
| CI jobs | 用 `requirements.txt` 装依赖 |

---

## 8. 迁移计划（分4步，预计停机 < 5分钟）

迁移在港股收盘后（UTC 8:30+），美股开盘前（UTC 13:30）之间执行。

### 第1步: 创建 stable 分支 + quant-prod（不影响现状）

```bash
git push origin main:stable                              # 当前 main 作为 stable 起点
git clone --branch stable /opt/quant /opt/quant-prod     # 克隆生产目录
python3.12 -m venv /opt/quant-prod/.venv                 # 创建生产 venv
/opt/quant-prod/.venv/bin/pip install -r requirements.txt
```

### 第2步: 迁移 cron + systemd 到 quant-prod

```bash
# 停当前 cron
crontab -u quant -l > /tmp/cron_backup.txt
crontab -u quant -r

# 批量替换路径 /opt/quant/ → /opt/quant-prod/
# 批量替换 python3.12 → /opt/quant-prod/.venv/bin/python3.12
# 重新加载
crontab -u quant < /tmp/cron_updated.txt

# 迁移 ws_collector
sudo systemctl stop ws-collector
# 改 systemd 配置: WorkingDirectory → /opt/quant-prod, ExecStart → .venv/bin/python
sudo systemctl daemon-reload
sudo systemctl start ws-collector

# 验证
sudo systemctl is-active ws-collector
tail /home/quant/logs/ws-collector.log
```

### 第3步: /opt/quant → quant-dev

```bash
mv /opt/quant /opt/quant-dev
chown -R DangXuan:DangXuan /opt/quant-dev
python3.12 -m venv /opt/quant-dev/.venv
/opt/quant-dev/.venv/bin/pip install -r requirements.txt
```

### 第4步: 验证 + 更新 CI/CD

```bash
# 确认 ws_collector 从 quant-prod 运行
ps aux | grep ws_collector | grep quant-prod

# 确认 cron 日志正常
tail /home/quant/logs/bq_loader_us_1d.log

# 更新 deploy.yml：路径指向 quant-prod + .venv
# 更新 CI：指向 quant-dev + .venv
```

---

## 9. 边界情况

| 场景 | 处理 |
|------|------|
| 部署时有 paper 实验在跑 | 不受影响，实验跑在 `quant-dev` |
| 部署时实盘交易在跑 | 跳过交易服务的重启，等下次 rebalance 自然切换 |
| GitHub Actions 连不上 VM | 重试3次，全失败则标记 CI failed，不部署 |
| 多个人同时 merge PR | `concurrency: cancel-in-progress`，只部署最后一个 |
| cron 执行中遇到部署 | cron_wrapper 正常，cron 读的是磁盘文件，部署是原子 git checkout |
| 部署后 quality 发现数据断档 | 告警推送，手动判断是否需要回滚 |

---

## 10. 新增/修改文件清单

### 新增
- `scripts/deploy.sh` — 部署主脚本
- `scripts/rollback.sh` — 手动回滚脚本
- `requirements.txt` — 全量依赖 lockfile

### 修改
- `.github/workflows/ci.yml` — 扩展 lint/typecheck/test 范围，增加 security check，使用 requirements.txt
- `.github/workflows/deploy.yml` — 从简单 git reset 改为调用 deploy.sh
- `systemd/ws-collector.service` — 路径指向 `/opt/quant-prod/.venv/bin/python3.12`
- quant 用户 crontab — 路径批量替换为 `/opt/quant-prod/`
- `pyproject.toml` — 更新 ruff/mypy/pytest 配置覆盖全项目

---

## 11. 后续可扩展

- **告警推送:** quality check 发现数据断档后通过 OpenClaw 推送到微信
- **部署通知:** 部署成功/失败/回滚推送到微信
- **GitHub Branch Protection:** stable 分支禁止直接 push，强制 PR
- **预提交 hook:** pre-commit 跑 ruff + mypy 本地快速检查
