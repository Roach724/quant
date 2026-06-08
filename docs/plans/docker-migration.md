# Docker 化完整方案

> 目标：quant 项目打包为一个 Docker 镜像 → 推送到 GCP Artifact Registry。
> 部署 = 拉新镜像重启。容器内代码只读。不能再 vim prod。

## 基础设施验证 ✅

```
Artifact Registry:  asia-east2-docker.pkg.dev/deductive-notch-495015-c2/quant
认证方式:           gcloud auth configure-docker asia-east2-docker.pkg.dev
Docker:             v29.1.3 ✅
推送测试:           alpine → push 成功 ✅
```

---

## 当前架构

```
宿主机 (quant-vm)
├── /opt/quant-prod/          ← 代码（可 vim 修改）
├── /opt/quant-dev/           ← 开发环境（dev 分支）
├── systemd services (7个):
│   ├── ws-collector
│   ├── quant-admin (:8091)
│   ├── quant-admin-worker
│   ├── mlflow-ui (:5000)
│   ├── cloudflared
│   ├── exp-live_hk_ml (transient)
│   └── exp-live_hk_mom (transient)
├── crontab (quant user, 39 jobs)
├── OpenD (:11111, 宿主机原生)
└── 持久化: /var/log/quant/ /var/quant/state/ /var/data/ /opt/quant-prod/output/
```

---

## Phase 1：基础服务容器化

### 目标

只打包 **ws_collector + admin + mlflow + worker**。实验和 cron 不搬。

```
宿主机
├── Docker 容器 (quant)
│   ├── supervisor 管理:
│   │   ├── ws_collector
│   │   ├── admin       (:8091)
│   │   ├── admin_worker
│   │   └── mlflow      (:5000)
│   └── 代码: /opt/quant (只读)
├── OpenD (:11111)          ← 不动
├── cloudflared             ← 不动
├── crontab (39 jobs)       ← 不动
└── systemd: exp-*          ← 不动
```

### 关键文件

**Dockerfile:**
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor curl cron && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /opt/quant/
RUN mkdir -p /var/log/quant /var/quant/state /var/quant/experiments /var/data
COPY docker/supervisord.conf /etc/supervisor/conf.d/quant.conf
ENV QUANT_HOME=/opt/quant PYTHONPATH=/opt/quant QUANT_ENV=prod
EXPOSE 8091 5000
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
```

**docker-compose.yml:**
```yaml
services:
  quant:
    image: asia-east2-docker.pkg.dev/deductive-notch-495015-c2/quant/quant:${TAG:-latest}
    container_name: quant
    network_mode: host
    restart: unless-stopped
    volumes:
      - /var/log/quant:/var/log/quant
      - /var/quant/state:/var/quant/state
      - /var/quant/experiments:/var/quant/experiments
      - /var/data:/var/data
      - /opt/quant-prod/output:/opt/quant/output
      - /opt/quant/configs/live:/opt/quant/live/configs
      - /opt/quant/configs/ml:/opt/quant/ml/configs
      - /opt/quant/configs/strategies:/opt/quant/configs/strategies
    environment:
      - FUTU_HOST=127.0.0.1
      - FUTU_PORT=11111
```

**CI/CD (GitHub Actions):**
```yaml
- name: Build & Push
  run: |
    docker build -t asia-east2-docker.pkg.dev/$PROJECT/quant/quant:${{ github.sha }} .
    docker push asia-east2-docker.pkg.dev/$PROJECT/quant/quant:${{ github.sha }}
    docker tag ... quant/quant:latest && docker push ... quant/quant:latest

- name: Deploy
  run: |
    gcloud compute ssh quant-vm --zone asia-east2-a -- "
      cd /opt/quant-prod &&
      TAG=${{ github.sha }} docker compose pull &&
      docker compose up -d"
```

### Phase 1 改造量

```
改代码: ~50 行（路径 /opt/quant-prod → /opt/quant）
新增:   Dockerfile, supervisord.conf, docker-compose.yml, requirements.txt
一次性: 迁移 mlflow.db → /var/data/，复制 configs 到 /opt/quant/configs/
```

---

## Phase 2：全量容器化

### 目标

实验管理和 cron 也搬进容器。宿主机只剩 OpenD + cloudflared。

```
宿主机
├── Docker 容器 (quant)
│   ├── supervisor 新增:
│   │   ├── cron daemon       ← 容器内 crontab
│   │   └── (实验进程，动态启停)
├── OpenD (:11111)  ← 不动
└── cloudflared     ← 不动
```

### 核心改造

**实验管理：** `systemd-run` → `admin/worker.py` 直接 subprocess 管进程

```python
class ExperimentRunner:
    def start(exp_id, cmd):
        proc = subprocess.Popen(cmd, start_new_session=True)
        # 后台线程等进程结束 → 自动标记 registry
    def stop(exp_id):
        os.killpg(pid, SIGTERM)  # 10s 超时 → SIGKILL
```

**Cron：** 容器内装 `cron` daemon，Admin cron API 一行不改

```ini
[program:cron]
command=cron -f
```

### Phase 2 改造量

```
改代码: ~150 行
新增:   ExperimentRunner 类, crontab 迁移脚本
```

---

## Phase 1 迁移过程 & 对 Admin 的影响

### 迁移步骤（耗时 ~5 分钟）

```
T+0     构建 Docker 镜像（不影响线上）
T+1     停 systemd: ws-collector quant-admin quant-admin-worker mlflow-ui
        留: exp-*, crontab, cloudflared, OpenD
T+2     docker compose up -d
T+3     健康检查 curl :8091/api/admin/health
T+5     完成
```

### 各模块影响

| 时间窗口 | Admin UI | ws_collector | 在跑实验 | 数据 |
|----------|----------|-------------|----------|------|
| T+0~T+1 | ✅ | ✅ | ✅ | 无损 |
| T+1~T+2 (~10s) | ❌ | ❌ | ✅ 继续跑 | 无损 |
| T+2~T+3 (~20s) | ❌ 启动中 | ❌ 启动中 | ✅ 继续跑 | 无损 |
| T+3 之后 | ✅ | ✅ | ✅ | 无损 |

**Admin UI 不可用窗口：~30 秒。ws_collector 丢 ≤1 根 5m K 线。**

### 回退

```bash
docker compose down
systemctl start ws-collector quant-admin quant-admin-worker mlflow-ui
# 30 秒回退，完全恢复原样
```

---

## Phase 2 迁移过程 & 对 Admin 的影响

### 迁移步骤（必须收盘后，耗时 ~15 分钟）

```
T+0     停所有实验: systemctl stop exp-live_hk_ml exp-live_hk_mom
T+1     标记 run → completed
T+2     导出 crontab: crontab -l > /var/data/crontab.txt
T+3     停旧 systemd exp-* units
T+4     构建新镜像（含 Phase 2 改造）
T+5     docker compose down && up -d
T+6     导入 crontab: cat crontab.txt | docker exec -i quant crontab -
T+7     通过 Admin UI 重新启动实验
```

### 关键影响

| 受影响 | 详情 |
|--------|------|
| 在跑实验 | ❌ 必须停。多日 checkpoint 保留，重启用 `--resume-run` 接续 |
| Cron | 需导出→导入。API 不变 |
| Admin 实验管理 | 后台 systemd-run → subprocess，**前端使用不变** |
| Admin 其他功能 | ✅ 全部不变 |
| 数据 | 无损（全部挂载卷） |

---

## Admin 功能变化总表

| 功能 | Phase 1 | Phase 2 | 用户感知 |
|------|---------|---------|----------|
| Dashboard (7子页) | ✅ | ✅ | 无变化 |
| 实验管理 → 配置模板 | ✅ | ✅ | 无变化 |
| 实验管理 → 启动/停止 | ✅ | ⚠️ 后台变 | 无变化 |
| 实验管理 → 详情 | ✅ | ✅ | 无变化 |
| 数据采集 → ws 启停 | ⚠️ supervisorctl | ⚠️ supervisorctl | 无变化 |
| 数据采集 → 回填/BQ | ✅ | ✅ | 无变化 |
| 日志浏览 | ✅ | ✅ | 无变化 |
| Cron 管理 | ✅ | ✅ | 无变化 |
| 模型&策略 | ✅ | ✅ | 无变化 |
| 因子管理 | ✅ | ✅ | 无变化 |

---

## 数据完整性

```
迁移前后数据在同一物理路径，容器通过 volume mount 访问：

/var/log/quant/          ← 同一目录
/var/quant/state/        ← 同一目录
/var/quant/experiments/  ← 同一目录
/var/data/               ← 同一目录
/opt/quant-prod/output/  → /opt/quant/output/ (路径变，数据在)
```

---

## 日常操作

```bash
# 看状态
docker compose exec quant supervisorctl status

# 看日志
docker compose logs -f quant

# 部署新版本
TAG=v1.2.3 docker compose pull && docker compose up -d

# 回滚
TAG=v1.2.2 docker compose up -d

# 进容器调试
docker compose exec quant bash
```

---

## 建议

1. **Phase 1 在非交易时段做**
2. **Phase 2 只在收盘后做，且先跑稳 Phase 1 至少 2 天**
3. **镜像地址**: `asia-east2-docker.pkg.dev/deductive-notch-495015-c2/quant/quant`
