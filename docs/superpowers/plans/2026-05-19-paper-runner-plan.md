# Paper Runner 设计方案

## 目标
一个 `python run_paper.py` 一键启动的纸交易系统，支持多市场历史数据回放模拟。

## 架构

```
run_paper.py  (CLI 入口)
  └── PaperRunner (核心类)
        ├── 加载配置 (CLI args / YAML)
        ├── 创建 PaperBroker + OrderManager + PositionTracker
        ├── 加载历史数据 → DataFrameSource
        ├── 实例化 Strategy
        ├── 逐 bar 重放:
        │     ├── strategy.on_bar() → list[Signal]
        │     ├── RiskGateway 预检
        │     ├── OrderManager 下单 → PaperBroker 成交
        │     ├── PositionTracker 记录
        │     └── InvestmentRecord 记日志
        ├── 收盘:
        │     ├── 生成绩效指标 (metrics)
        │     ├── HTML 报告 (report)
        │     └── 输出完整投资档案
        └── market.py — 市场常量 (交易时间/时区/最小佣金等)
```

## 多市场支持

| 市场 | 数据来源 | 交易时段 | 特殊处理 |
|------|----------|----------|----------|
| US | SDK (GCS parquet) 或 CSV | 9:30-16:00 ET 周一到周五 | — |
| HK | SDK (GCS parquet) 或 CSV | 9:30-16:00 HKT 周一到周五 | 午休 12:00-13:00 不交易 |
| Crypto | SDK (GCS parquet) 或 CSV | 24/7 | 无需交易时段过滤 |

## 输出

```
{output_dir}/
├── meta.json               — 运行元数据
├── performance.json        — 12 项绩效指标
├── trades.csv              — 逐笔成交
├── daily_pnl.csv           — 每日权益
├── positions_final.csv     — 最终持仓
├── risk_events.csv         — 风控事件
├── summary_report.txt      — 文本报告
└── report.html             — HTML 图表报告
```

## CLI 接口

```bash
# HK 市场 100万港币 动量策略
python run_paper.py --market hk --capital 1000000 --strategy my_strategies.MomentumStrategy \
  --start 2024-01-01 --end 2024-12-31 --output ./output/hk_momentum

# US 市场
python run_paper.py --market us --capital 100000 --strategy my_strategies.BuyHold

# Crypto 市场
python run_paper.py --market crypto --capital 50000 --strategy my_strategies.MeanReversion

# 使用配置文件
python run_paper.py --config paper_config.yaml

# 列出内置策略
python run_paper.py --list-strategies
```

## 文件清单

1. `run_paper.py` — CLI 入口 + PaperRunner 核心类
2. `paper/market.py` — 市场常量与工具函数
3. `paper/strategies.py` — 内置示例策略 (BuyHold, SimpleMomentum)
4. `paper/__init__.py` — 包声明
