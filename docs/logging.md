# 日志规范 (Logging Standard)

> 最后更新: 2026-06-03  
> 状态: ✅ 已上线全部组件

## 架构

```
应用层           Python logging → QuantJsonFormatter → JSON 行
                   ├─ FileHandler → /var/log/quant/{env}/{module}/
                   └─ StreamHandler → stderr (dev 可见性 / cron wrapper 捕获)

系统层           cron_wrapper.sh → tee 捕获 stdout/stderr
                   └─ 自动路由: job name 前缀 → module 目录

传输层           Ops Agent (files receiver)
                   ├─ parse_json processor → jsonPayload.* 结构化字段
                   └─ → GCP Logs Explorer
```

## 日志目录

```
/var/log/quant/
├── prod/                      # 生产环境
│   ├── collector/             # ws_collector
│   ├── loader/                # BQ loader cron
│   ├── cron/                  # 数据采集 cron (bars, F10)
│   ├── factor/                # 因子采集/加载
│   └── quality/               # 数据质量检查
└── dev/                       # 开发环境
    ├── live/                  # 实验/模拟
    ├── train/                 # 模型训练
    ├── backfill/              # 历史回填
    └── adhoc/                 # 临时调试
```

权限: `root:quant 775` (目录), `644` (文件)

## JSON 格式

```json
{
  "ts": "2026-06-03T04:00:00.123Z",
  "level": "INFO",
  "logger": "live.runner",
  "quant_env": "dev",
  "quant_module": "live",
  "msg": "Runner starting"
}
```

| 字段 | 说明 | 示例 |
|------|------|------|
| `ts` | ISO-8601 UTC 时间戳 | `2026-06-03T04:00:00.123Z` |
| `level` | 日志级别 | `INFO`, `WARNING`, `ERROR` |
| `logger` | Python logger 名称 | `live.runner`, `ws_collector` |
| `quant_env` | 环境 | `prod`, `dev` |
| `quant_module` | 模块 | `collector`, `loader`, `live`, `cron`, `factor`, `quality`, `train`, `backfill`, `adhoc` |
| `msg` | 日志消息 | 任意文本 |
| `exception` | (可选) 异常消息 | `division by zero` |
| `traceback` | (可选) 异常堆栈 | 完整 traceback |

## 日志轮替

`/etc/logrotate.d/quant`: daily, 保留 7 天, 单文件上限 100MB, gzip 压缩

## Logs Explorer 查询

```
# 按级别筛选
jsonPayload.level="ERROR"

# 按模块筛选
jsonPayload.quant_module="collector"

# 组合筛选
jsonPayload.quant_env="prod" AND jsonPayload.level="WARNING"

# 排除测试日志
NOT jsonPayload.logger:"test"

# 最近 1 小时错误
jsonPayload.level="ERROR" AND timestamp > "-1h"
```

## 开发流程

### 新脚本接入（3 步）

**Step 1: 导入**

```python
from common.logging_util import setup_root_json
```

**Step 2: 初始化（在 logging.basicConfig 之后）**

```python
logging.basicConfig(level=logging.INFO, ...)
try:
    setup_root_json(module="loader")  # 自动使用脚本名作为日志文件名
except Exception:
    pass
```

**Step 3: 正常使用 logging**

```python
logger = logging.getLogger(__name__)
logger.info("Processing started")
logger.warning("Skipping invalid record")
```

### 现有模块

| 模块 | 入口脚本 | 日志文件 |
|------|---------|---------|
| loader | `bigquery_loader/main.py` | `/var/log/quant/prod/loader/main.log` |
| factor | `bigquery_loader/load_futu_factors.py` | `/var/log/quant/prod/factor/load_futu_factors.log` |
| factor | `collectors/collect_futu_factors.py` | `/var/log/quant/prod/factor/collect_futu_factors.log` |
| cron | `collectors/main.py` | `/var/log/quant/prod/cron/main.log` |
| cron | `collectors/fundamental_collector.py` | `/var/log/quant/prod/cron/fundamental_collector.log` |
| quality | `quality/main.py` | `/var/log/quant/prod/quality/main.log` |
| collector | `collectors/ws_collector.py` | `/var/log/quant/prod/collector/ws_collector.log` |
| live | `live/run.py` (--config *.yaml) | `/var/log/quant/dev/live/{experiment_id}.log` |

### cron_wrapper.sh 模块路由

自动根据 JOB_NAME 前缀检测：

| 前缀 | 模块 |
|------|------|
| `bq_loader_*` | loader |
| `collector_*`, `f10_collector_*` | cron |
| `collect_*`, `load_*` | factor |
| `quality_*` | quality |
| 其他 | cron (默认) |

## 排障

### 日志文件未生成
1. 检查目录权限: `ls -la /var/log/quant/prod/{module}/`
2. 检查 sys.path: 脚本能否 `from common.logging_util import setup_root_json`
3. 检查 try/except 是否静默吞错

### Logs Explorer 搜索不到
1. 等 1-2 分钟 (Ops Agent 上传延迟)
2. 检查 Ops Agent 状态: `sudo service google-cloud-ops-agent status`
3. 检查字段路径: 使用 `jsonPayload.quant_module` 而非 `jsonPayload.module`

### 日志重复
- ws_collector: 确保 handler 只挂 root logger，不重复挂 ws_collector logger
