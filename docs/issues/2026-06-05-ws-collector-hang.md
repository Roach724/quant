# ws_collector WebSocket 卡死 — 根因分析与修复方案

> 2026-06-05 | 发现者: 老大 | 分析: Jarvis

## 事件

2026-06-05 北京时间 10:35，管理平台显示 ws_collector 心跳停止。排查发现进程 (PID 285230) 自 02:40 UTC 起无日志、无数据写入 BQ，OpenD 连接失联但进程未退出。

## 根因

**futu-api WebSocket 连接静默断开后，主循环卡死在 `subscribe()` / `unsubscribe()` 调用上。**

具体链路：
1. OpenD 与 ws_collector 之间的 WebSocket TCP 连接静默断开（可能是 OpenD 侧重启/异常、网络波动、或 futu-api 内部 bug）
2. Python futu-api 的 `OpenQuoteContext` 对象未检测到断开，未抛异常
3. 主循环每 60s 调 `ctx.subscribe()` / `ctx.unsubscribe()` 做订阅轮转
4. 某次调用在底层阻塞，既不返回也不抛异常 → 主循环卡死
5. watchdog 和主循环同线程 → watchdog 永远不触发

**本质问题：单线程架构。** 业务逻辑、订阅管理、watchdog 全在一条线程，一个 futu-api 调用卡住就全死。

## 进程当时状态

| 指标 | 值 |
|------|-----|
| State | S (sleeping) |
| Threads | 7 |
| Memory | 261 MB |
| CPU | 2h31m (累计) |
| Open fds | 0 |
| 最后日志 | 02:40 UTC (Flushed 270 bars HK) |
| 最后心跳 | 02:35 UTC |

## 缺失数据

| 时段 (UTC) | 内容 | 估计缺失 |
|------------|------|---------|
| 02:40–04:00 | HK 早盘尾段 | ~80 min |
| 04:00–05:00 | HK 午休 | 0 (无数据) |
| 05:00–05:41 | HK 午盘开头 | ~41 min |
| **合计** | | **~2h HK 5m K 线** |

> 后续需要 `backfill.py` 回填 2026-06-05 02:40–05:41 的 HK 数据。

## 修复记录

1. `sudo kill -9 285230` — 旧进程已 hang，SIGTERM 无响应
2. systemd 自动重启 → 新 PID 466131
3. 验证：05:45 UTC Flushed 267 bars HK → BQ ✅

## 建议架构修复

### 1. watchdog 拆到独立线程

```python
import threading, os, time

def watchdog_thread(stop_event, get_last_heartbeat, timeout_sec):
    while not stop_event.is_set():
        time.sleep(30)
        if time.time() - get_last_heartbeat() > timeout_sec:
            logger.critical("WATCHDOG: main loop frozen, exiting for systemd restart")
            os._exit(1)  # 不走正常退出，让 systemd 拉起

# 启动
stop = threading.Event()
watchdog = threading.Thread(target=watchdog_thread, args=(stop, lambda: last_heartbeat, 3600))
watchdog.daemon = True
watchdog.start()
```

### 2. futu-api 调用加超时保护

```python
import signal

def with_timeout(func, timeout=10):
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError))
    signal.alarm(timeout)
    try:
        return func()
    finally:
        signal.alarm(0)
```

但 `signal.alarm` 只在主线程生效。更好的方案可能是用 `concurrent.futures` + `ThreadPoolExecutor` 把 subscribe/unsubscribe 扔到子线程加 timeout。

### 3. 加连接健康检查

定期调一个轻量 OpenD API（如 `get_market_state`）验证连接存活，失败了主动重连。

## 优先级

- **P1** — watchdog 拆线程（最小改动，最大收益）
- **P2** — subscribe/unsubscribe 加超时
- **P3** — 连接健康检查
