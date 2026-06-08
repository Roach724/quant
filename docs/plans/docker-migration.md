# Docker 化完整方案

> 目标：quant 项目打包为一个 Docker 镜像。部署 = 换镜像。
> 容器内代码只读。不能再 vim prod。

---

## 当前架构

```
宿主机 (quant-vm)
├── /opt/quant-prod/          ← 代码（可 vim 修改）
│   ├── .venv/                ← 1.1GB venv
│   ├── collectors/           ← ws_collector
│   ├── admin/                ← FastAPI :8091 + React frontend
│   ├── live/                 ← runner, configs, experiment_manager
│   ├── strategies/           ← 策略源码
│   ├── ml/                   ← 训练 pipeline
│   └── ...
├── /opt/quant-dev/           ← 开发环境（dev 分支）
├── systemd services (7个):
│   ├── ws-collector
│   ├── quant-admin
│   ├── quant-admin-worker
│   ├── mlflow-ui (:5000)
│   ├── cloudflared (tunnel → admin.aiworxpace.xyz)
│   ├── exp-live_hk_ml (transient, 按需)
│   └── exp-live_hk_mom (transient, 按需)
├── crontab (quant user, 39 jobs)
├── OpenD (:11111, 宿主机原生)
└── 持久化:
    ├── /var/log/quant/
    ├── /var/quant/state/
    ├── /var/quant/experiments/registry.json
    ├── /var/quant/admin.db
    ├── /opt/quant-prod/output/live/
    └── /home/DangXuan/.mlflow/mlflow.db
```

---

## Phase 1：基础服务容器化

### 目标

只打包 **ws_collector + admin + mlflow + worker**。实验和 cron 不搬。

```diff
宿主机 (quant-vm)
+ ├── Docker 容器 (quant)
+ │   ├── supervisor 管理:
+ │   │   ├── ws_collector        ← 搬进去
+ │   │   ├── admin (:8091)        ← 搬进去
+ │   │   ├── admin_worker         ← 搬进去
+ │   │   └── mlflow (:5000)       ← 搬进去
+ │   └── 代码: /opt/quant (只读)
  ├── OpenD (:11111)               ← 不动
  ├── cloudflared                  ← 不动
  ├── crontab (39 jobs)            ← 不动
- ├── systemd: ws-collector        ← 删
- ├── systemd: quant-admin         ← 删
- ├── systemd: quant-admin-worker  ← 删
- ├── systemd: mlflow-ui           ← 删
  ├── systemd: exp-live_hk_ml      ← 不动
  ├── systemd: exp-live_hk_mom     ← 不动
```

### 文件

#### Dockerfile

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor curl cron && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖（从 requirements.txt）
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 代码（只读）
COPY . /opt/quant/

# 创建可写目录
RUN mkdir -p /var/log/quant /var/quant/state /var/quant/experiments /var/data

# supervisor 配置
COPY docker/supervisord.conf /etc/supervisor/conf.d/quant.conf

ENV QUANT_HOME=/opt/quant \
    PYTHONPATH=/opt/quant \
    QUANT_ENV=prod

EXPOSE 8091 5000
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
```

#### docker/supervisord.conf

```ini
[supervisord]
nodaemon=true
logfile=/var/log/quant/supervisord.log
pidfile=/var/run/supervisord.pid

[program:ws_collector]
command=python /opt/quant/collectors/ws_collector.py
directory=/opt/quant
autorestart=true
startretries=3
stdout_logfile=/var/log/quant/collector/ws_collector.log
stdout_logfile_maxbytes=50MB
redirect_stderr=true

[program:admin]
command=python /opt/quant/admin/server.py
directory=/opt/quant
autorestart=true
startretries=3
stdout_logfile=/var/log/quant/admin/server.log
redirect_stderr=true

[program:admin_worker]
command=python /opt/quant/admin/worker.py
directory=/opt/quant
autorestart=true
startretries=3
stdout_logfile=/var/log/quant/admin/worker.log
redirect_stderr=true

[program:mlflow]
command=mlflow ui --backend-store-uri sqlite:////var/data/mlflow.db \
    --default-artifact-root /var/data/mlflow_artifacts --host 0.0.0.0 --port 5000
autorestart=true
startretries=3
stdout_logfile=/var/log/quant/mlflow.log
redirect_stderr=true
```

#### docker-compose.yml

```yaml
version: "3.8"

services:
  quant:
    image: gcr.io/deductive-notch-495015-c2/quant:${TAG:-latest}
    container_name: quant
    network_mode: host          # 访问宿主机 OpenD 127.0.0.1:11111
    restart: unless-stopped
    volumes:
      # 持久化数据（可写）
      - /var/log/quant:/var/log/quant
      - /var/quant/state:/var/quant/state
      - /var/quant/experiments:/var/quant/experiments
      - /var/data:/var/data                               # admin.db + mlflow.db
      - /opt/quant-prod/output:/opt/quant/output          # 实验输出
      # Admin 可编辑的配置（从镜像分离）
      - /opt/quant/configs/live:/opt/quant/live/configs   # 实验配置模板
      - /opt/quant/configs/ml:/opt/quant/ml/configs       # ML 配置模板
      - /opt/quant/configs/strategies:/opt/quant/configs/strategies  # 用户策略
    environment:
      - QUANT_ENV=prod
      - FUTU_HOST=127.0.0.1
      - FUTU_PORT=11111
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8091/api/admin/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

#### CI/CD (GitHub Actions)

```yaml
# .github/workflows/deploy.yml — 追加 docker 步骤
jobs:
  docker-build-deploy:
    if: github.ref == 'refs/heads/stable'
    steps:
      - uses: actions/checkout@v4
      
      - name: Build image
        run: |
          docker build -t gcr.io/$PROJECT/quant:${{ github.sha }} .
          docker tag gcr.io/$PROJECT/quant:${{ github.sha }} gcr.io/$PROJECT/quant:latest
      
      - name: Push to GCR
        run: |
          gcloud auth configure-docker
          docker push gcr.io/$PROJECT/quant:${{ github.sha }}
          docker push gcr.io/$PROJECT/quant:latest
      
      - name: Deploy to VM
        run: |
          gcloud compute ssh quant-vm --zone asia-east2-a -- "
            cd /opt/quant-prod &&
            TAG=${{ github.sha }} docker compose pull &&
            docker compose up -d &&
            sleep 10 &&
            curl -f http://localhost:8091/api/admin/health"
```

### Phase 1 改造清单

```
改代码:
├── admin/server.py          # /opt/quant-prod → /opt/quant (路径硬编码)
├── admin/worker.py          # 同上
├── collectors/ws_collector.py  # 同上
├── live/exp_cli.py          # 同上
├── live/experiment_manager.py # 同上
├── live/runner.py           # 同上
├── ml/registry.py           # mlflow.db 路径 → /var/data/
├── strategies/__init__.py   # 加 configs/strategies 路径
├── common/normalize.py      # symbols.yaml 路径
└── scripts/cron_wrapper.sh  # 同上

新增文件:
├── docker/supervisord.conf
├── docker-compose.yml
├── requirements.txt         # pip freeze > requirements.txt
└── .github/workflows/deploy-docker.yml

一次性操作 (部署前执行):
├── mkdir -p /var/data /opt/quant/configs/{live,ml,strategies}
├── cp /home/DangXuan/.mlflow/mlflow.db /var/data/
├── cp -r /opt/quant-dev/models_artifacts /var/data/mlflow_artifacts
├── cp /opt/quant-prod/live/configs/*.yaml /opt/quant/configs/live/
├── cp /opt/quant-prod/ml/configs/*.yaml /opt/quant/configs/ml/
├── cp /opt/quant-prod/strategies/*.py /opt/quant/configs/strategies/
├── docker compose up -d
└── # 验证后停掉旧 systemd
    systemctl stop ws-collector quant-admin quant-admin-worker mlflow-ui
    systemctl disable ws-collector quant-admin quant-admin-worker mlflow-ui
```

### Phase 1 不变的部分

```
不动:
├── OpenD (:11111)              ← 宿主机
├── cloudflared                 ← 宿主机
├── crontab (39 jobs)           ← 宿主机，不变
├── systemd: exp-*              ← 实验仍用 systemd-run
├── /opt/quant-dev/             ← dev 环境保留
└── /opt/quant-prod/            ← 保留作 fallback
```

### Phase 1 日常操作

```bash
# 看日志
docker compose logs -f quant
docker compose exec quant supervisorctl tail ws_collector

# 重启某服务
docker compose exec quant supervisorctl restart admin

# 看进程
docker compose exec quant supervisorctl status

# 进容器
docker compose exec quant bash

# 部署新版本
TAG=v1.2.3 docker compose pull && docker compose up -d

# 回滚
TAG=v1.2.2 docker compose up -d

# 实验（不变，仍在宿主机）
sudo systemctl start exp-live_hk_ml
```

### Phase 1 风险

| 风险 | 缓解 |
|------|------|
| 路径硬编码 `/opt/quant-prod` | grep 全局替换为 `/opt/quant` 或 `$QUANT_HOME` |
| 容器启动后服务起不来 | healthcheck 自动告警 |
| OpenD 连不上 | host 网络模式，127.0.0.1 直通 |
| 部署失败 | 旧 systemd 服务保留，可立即回退 |

---

## Phase 2：全量容器化

### 目标

把实验管理和 cron 也搬进容器。宿主机只剩 OpenD + cloudflared。

```diff
宿主机 (quant-vm)
  ├── Docker 容器 (quant)
+ │   ├── supervisor 新增:
+ │   │   ├── cron daemon          ← 搬进来
+ │   │   ├── experiment runner    ← 动态启停
+ │   ├── 代码: /opt/quant (只读)
  ├── OpenD (:11111)               ← 不动
  ├── cloudflared                  ← 不动
- ├── crontab (39 jobs)            ← 删，移入容器
- ├── systemd: exp-*               ← 删，移入容器
```

### 改造

#### A. 实验管理

`exp_cli.py` 和 `admin/worker.py` 不再用 systemd-run，改为直接 subprocess 管进程。

```python
# admin/worker.py — 新增
import signal, time, threading

class ExperimentRunner:
    """管理实验进程生命周期，替代 systemd-run。"""
    
    def __init__(self):
        self._procs: dict[str, subprocess.Popen] = {}
        self._monitors: dict[str, threading.Thread] = {}
    
    def start(self, exp_id: str, cmd: list[str]):
        """启动实验，后台监控退出。"""
        if exp_id in self._procs and self._procs[exp_id].poll() is None:
            raise RuntimeError(f"{exp_id} already running")
        
        proc = subprocess.Popen(
            cmd,
            cwd="/opt/quant",
            start_new_session=True,  # 独立进程组，可 kill
            stdout=open(f"/var/log/quant/live/{exp_id}.log", "a"),
            stderr=subprocess.STDOUT,
        )
        self._procs[exp_id] = proc
        
        # 后台线程等进程结束，自动更新 registry
        t = threading.Thread(target=self._await_exit, args=(exp_id, proc))
        t.daemon = True
        t.start()
    
    def stop(self, exp_id: str, force: bool = False):
        """停止实验。先 SIGTERM，10s 后 SIGKILL。"""
        proc = self._procs.get(exp_id)
        if not proc or proc.poll() is not None:
            return
        
        if force:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    
    def is_running(self, exp_id: str) -> bool:
        proc = self._procs.get(exp_id)
        return proc is not None and proc.poll() is None
    
    def _await_exit(self, exp_id: str, proc):
        proc.wait()
        del self._procs[exp_id]
        # 自动更新 experiment registry
        from live.experiment_manager import ExperimentManager
        mgr = ExperimentManager()
        try:
            exp = mgr.get(exp_id)
            if exp.active_run:
                mgr.stop_run(exp_id, exp.active_run.run_id)
        except Exception:
            pass
```

Admin worker 启动时创建全局 `ExperimentRunner` 实例，API 通过它启停实验。

#### B. Cron 管理

容器内装 `cron` daemon，Admin cron API 照常操作 crontab。

```ini
# supervisord.conf 加:
[program:cron]
command=cron -f
autorestart=true
stdout_logfile=/var/log/quant/cron.log
```

Admin 的 cron API 一行不用改（容器里有 `/usr/bin/crontab`）。

#### C. 部署时的 crontab 迁移

部署脚本加一步：
```bash
# 把宿主机 crontab 导入容器
crontab -l -u quant | docker exec -i quant crontab -
```

### Phase 2 完成后

```
宿主机只剩:
├── OpenD (:11111)              ← 必须宿主机（GUI + Futu 连接）
├── cloudflared tunnel          ← HTTPS 入口
├── Docker Engine + compose     ← 唯一运行时
└── 持久化目录（全挂载进容器）
    ├── /var/log/quant/
    ├── /var/quant/state/
    ├── /var/quant/experiments/
    ├── /var/data/
    └── /opt/quant-prod/output/

容器内:
├── supervisor                  ← 总管
│   ├── ws_collector
│   ├── admin (:8091)
│   ├── admin_worker
│   ├── mlflow (:5000)
│   ├── cron daemon             ← Phase 2 新增
│   └── (实验进程，动态启停)     ← Phase 2 新增
└── 代码 /opt/quant (只读)
```

### Phase 2 风险

| 风险 | 缓解 |
|------|------|
| 实验进程失控（僵尸） | worker 后台线程 await_exit，超时 SIGKILL |
| crontab 丢失（容器重建） | crontab 文件放在挂载卷 `/var/data/crontab`，启动时恢复 |
| 实验 crash 不重启 | worker 有 max_restarts 逻辑 |

---

## 两阶段对比

| | Phase 1 | Phase 2 |
|------|---------|---------|
| 容器内 | ws_collector + admin + mlflow + worker | + cron + 实验管理 |
| 宿主机 | OpenD + cloudflared + crontab + 实验 systemd | 只剩 OpenD + cloudflared |
| 可直接 vim 改代码 | ❌ | ❌ |
| 实验管理 | systemd-run（宿主机） | subprocess（容器内） |
| Cron | 宿主机 crontab | 容器内 cron daemon |
| 改造量 | ~50 行 + 路径替换 | ~150 行 |
| 回退难度 | 低（旧 systemd 都在） | 中（crontab 已迁） |
| 风险 | 低 | 中 |

---

## 时间线

```
Day 1: Dockerfile + supervisord + 路径替换 → 构建测试
Day 2: Phase 1 部署到 VM → 验证 24h
Day 3-4: Phase 2 实验管理改造 → 验证
Day 5: Phase 2 上线
```
