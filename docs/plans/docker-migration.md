## Phase 1 迁移过程 & 对 Admin 的影响

### 迁移步骤（耗时 ~5 分钟）

```
时间线:

T+0     构建 Docker 镜像（不影响线上）
T+1     停 systemd 服务
        ├── systemctl stop ws-collector quant-admin quant-admin-worker mlflow-ui
        ├── 留: exp-live_hk_ml, exp-live_hk_mom, crontab, cloudflared, OpenD
T+2     启动 Docker 容器
        └── docker compose up -d
T+3     等待 healthcheck
        └── curl http://localhost:8091/api/admin/health
T+4     验证 Admin 功能
        └── Dashboard / 配置模板 / 数据采集 / 日志浏览
T+5     完成
```

### 对各模块的影响

| 时间窗口 | Admin UI | ws_collector | 在跑实验 | 数据 |
|----------|----------|-------------|----------|------|
| **T+0 ~ T+1** | ✅ 正常 | ✅ 正常 | ✅ 正常 | 无损 |
| **T+1 ~ T+2 (~10s)** | ❌ 不可用 | ❌ 停 | ✅ 继续跑 | 无损 |
| **T+2 ~ T+3 (~20s)** | ❌ 启动中 | ❌ 启动中 | ✅ 继续跑 | 无损 |
| **T+3 之后** | ✅ 恢复 | ✅ 恢复 | ✅ 继续跑 | 无损 |

**Admin UI 不可用窗口：~30 秒。**

### 具体影响

**ws_collector 停机 ~30s**
- 丢 ≤ 1 根 5m K 线（30s 停机 < 5min bar 间隔）
- 对港股：当前 market open，影响 1 根 bar
- 建议：在非交易时段做，或接受丢 1 根

**Admin 平台**
- 停机 30s，用户刷新页面即可恢复
- 所有配置模板、策略文件内容不变（挂载同一目录）
- 日志文件不变（挂载同一目录）
- 实验注册表不变（registry.json 挂载卷）
- BQ 数据不变（不在容器里）

**在跑实验（exp-live_hk_ml / exp-live_hk_mom）**
- ✅ 完全不受影响：它们跑在宿主机 systemd 上，Phase 1 不动它们
- Admin 里它们仍显示 "idle"（本来就是，跟 Docker 无关）

**crontab（39 jobs）**
- ✅ 完全不受影响：仍在宿主机

**回退方案**
```bash
# 如果容器有问题，30 秒回退
docker compose down
systemctl start ws-collector quant-admin quant-admin-worker mlflow-ui
# 一切恢复原样
```

---

## Phase 2 迁移过程 & 对 Admin 的影响

### 迁移步骤（需要收盘后做，耗时 ~15 分钟）

```
前置条件: 港股和美股都收盘（00:00 UTC+8 之后）

T+0     停所有实验
        ├── systemctl stop exp-live_hk_ml exp-live_hk_mom
        └── 确认所有 exp-* unit 停了
T+1     实验状态写入 registry
        └── 手动标记所有 running run → completed
T+2     导出宿主机 crontab
        └── crontab -l -u quant > /var/data/crontab.txt
T+3     停旧 systemd 实验服务
        └── systemctl disable exp-live_hk_ml exp-live_hk_mom
T+4     构建新 Docker 镜像（含 Phase 2 改造）
T+5     docker compose down && docker compose up -d
T+6     导入 crontab 到容器
        └── cat /var/data/crontab.txt | docker exec -i quant crontab -
T+7     验证 Admin 所有功能
T+8     通过 Admin UI 重新启动实验
```

### 对各模块的影响

| 时间窗口 | Admin UI | 实验 | cron | 数据 |
|----------|----------|------|------|------|
| T+0 | ✅ | 停 ❌ | ✅ | 状态写入 |
| T+4 ~ T+5 (~30s) | ❌ | ❌ | ❌ | 无损 |
| T+5 之后 | ✅ | 需手动重启 | 需导入 | 无损 |

### 实验状态影响（关键）

**Phase 2 期间实验需要全部停止。**

当前实验状态：
```
live_hk_ml:  2 runs, current_run=20260608_080550_964305
live_hk_mom: 2 runs, current_run=20260608_080550_913192
```

停实验后：
- 当前 run 标记为 `completed`
- 多日模式的 checkpoint 状态保存在 `/var/quant/state/` → volume mount 迁移进容器
- 下次启动实验时 `--resume-run <run_id>` 可以接续

**但注意：** 如果实验在盘中停止：
- 当前日交易记录丢失（未收盘的 bar 没记录）
- 多日状态 checkpoint 保留（上次收盘时的持仓/资金）
- **建议：只在收盘后做 Phase 2**

### Admin 功能变化

| 功能 | Phase 1 后 | Phase 2 后 | 变化 |
|------|-----------|-----------|------|
| Dashboard | ✅ 不变 | ✅ 不变 | 无 |
| 实验管理 → 配置模板 | ✅ 不变 | ✅ 不变 | 无 |
| 实验管理 → 实验室（启动） | ✅ 不变 | ⚠️ 后台变 | 启动方式从 systemd-run 变 subprocess，**前端使用不变** |
| 实验管理 → 实验室（停止） | ✅ 不变 | ⚠️ 后台变 | 同上 |
| 实验管理 → 实验室（详情） | ✅ 不变 | ✅ 不变 | 无 |
| 数据采集 → ws_collector 启停 | ⚠️ supervisorctl | ⚠️ supervisorctl | API 从 `systemctl` 改为 `supervisorctl` |
| 数据采集 → 回填 | ✅ 不变 | ✅ 不变 | 无 |
| 数据采集 → BQ 地图 | ✅ 不变 | ✅ 不变 | 无 |
| 日志浏览 | ✅ 不变 | ✅ 不变 | 无 |
| Cron 管理 | ✅ 不变 | ✅ 不变 | crontab 在容器内，API 不变 |
| 模型&策略 → 策略编辑 | ✅ 不变 | ✅ 不变 | 挂载卷 |
| 模型&策略 → 模型中心 | ✅ 不变 | ✅ 不变 | 无 |
| 因子管理 | ✅ 不变 | ✅ 不变 | 无 |

### 数据完整性保证

```
迁移前后数据路径映射:

迁移前（宿主机）                    迁移后（容器内 volume）
/var/log/quant/          ←──→     /var/log/quant/          ✅ 同一目录
/var/quant/state/        ←──→     /var/quant/state/        ✅ 同一目录
/var/quant/experiments/  ←──→     /var/quant/experiments/  ✅ 同一目录
/var/data/               ←──→     /var/data/               ✅ 同一目录
/opt/quant-prod/output/  ←──→     /opt/quant/output/       ⚠️ 路径变了

⚠️ 注意: output 路径从 /opt/quant-prod/output → /opt/quant/output
需要确认 runner 和 Admin 的路径引用一致。
```

### 回退方案

```bash
# Phase 2 回退（回到 Phase 1 状态）
docker compose down
# 恢复 crontab
cat /var/data/crontab.txt | crontab -u quant -
# 恢复实验 systemd（需重新注册）
cd /opt/quant-prod && python live/exp_cli.py start live_hk_ml --resume-run <run_id>
```

---

## 关键建议

1. **Phase 1 在交易时段外做**（避免丢 bar）
2. **Phase 2 只在收盘后做**（实验可以优雅停止）
3. **先做 Phase 1，跑稳 2-3 天再 Phase 2**
4. **output 路径问题需要在 Phase 1 就解决**（`/opt/quant-prod/output` → `/opt/quant/output`）
