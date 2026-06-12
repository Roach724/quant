"""
投资记录模块 — InvestmentRecord

为每次回测/实盘运行保存一份完整的"投资档案"：
- 元数据 + 配置快照
- 绩效指标
- 成交明细
- 每日权益曲线
- 持仓快照
- 风控事件
- 信号排名
- 人类可读报告

保存结构 (save(output_dir)):
    {output_dir}/
    ├── meta.json               — 元数据 + 配置
    ├── performance.json        — 绩效汇总
    ├── trades.csv              — 成交明细
    ├── daily_pnl.csv           — 每日权益
    ├── positions_final.csv     — 最终持仓
    ├── risk_events.csv         — 风控事件 (如有)
    ├── signal_log.csv          — 信号排名 (如有)
    └── summary_report.txt      — 人类可读报告

使用方式:
    rec = InvestmentRecord("my_strategy", {"capital": 1_000_000})
    rec.record_trade(time, "AAPL", "BUY", 100, 150.0, 5.0)
    rec.record_equity("2024-01-01", 1000000)
    rec.save("./output/my_run")
"""
import csv
import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


class InvestmentRecord:
    """投资记录 — 保存交易、权益、信号和风控事件，输出完整档案。"""

    def __init__(self, strategy_name: str = "", config: Optional[dict] = None):
        self.strategy_name = strategy_name or "unknown"
        self.created_at = datetime.utcnow()

        # 配置快照
        self._config = dict(config) if config else {}

        # 数据容器
        self._trades: list[dict] = []
        self._equity: list[dict] = []       # {date, equity}
        self._positions: list[dict] = []    # {date, symbol, shares, price}
        self._risk_events: list[dict] = []  # {event_type, detail, time}
        self._signals: list[dict] = []      # {date, symbol, score, rank}

    # ── 配置 ──

    def set_config(self, config: dict):
        """记录策略配置快照。"""
        self._config = dict(config)

    # ── 记录方法 ──

    def record_trade(self, time, symbol: str, side: str, qty: int,
                     price: float, cost: float):
        """记录一笔成交。

        Args:
            time: datetime or str
            symbol: 股票代码
            side: "BUY" / "SELL"
            qty: 成交数量
            price: 成交价格
            cost: 交易成本（佣金等）
        """
        self._trades.append({
            "time": str(time),
            "symbol": symbol,
            "side": side.upper(),
            "qty": qty,
            "price": price,
            "cost": cost,
        })

    def record_signal(self, date, symbol: str, score: float, rank: int):
        """记录一条信号/排名。"""
        self._signals.append({
            "date": str(date),
            "symbol": symbol,
            "score": score,
            "rank": rank,
        })

    def record_risk_event(self, event_type: str, detail: str):
        """记录一条风控事件。"""
        self._risk_events.append({
            "event_type": event_type,
            "detail": detail,
            "time": datetime.utcnow().isoformat(),
        })

    def record_equity(self, date, equity: float):
        """记录每日权益值。"""
        self._equity.append({
            "date": str(date),
            "equity": equity,
        })

    def record_position(self, date, symbol: str, shares: int, price: float):
        """记录一条持仓快照。"""
        self._positions.append({
            "date": str(date),
            "symbol": symbol,
            "shares": shares,
            "price": price,
        })

    # ── 绩效计算 ──

    def _compute_performance(self) -> dict:
        """从权益曲线计算绩效指标。

        Returns:
            dict with keys: total_return, annual_return, sharpe_ratio,
            max_drawdown, calmar_ratio, win_rate, total_trades, avg_trade_pnl
        """
        if len(self._equity) < 2:
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "calmar_ratio": 0.0,
                "win_rate": 0.0,
                "total_trades": len(self._trades),
                "avg_trade_pnl": 0.0,
            }

        # 按日期排序权益曲线，resample 到连续交易日填补假日/周末间隙
        sorted_equity = sorted(self._equity, key=lambda r: r["date"])
        df = pd.DataFrame(sorted_equity)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df.resample("B").ffill()  # 交易日频率，前向填充
        equity_series = df["equity"]
        equity_values = equity_series.tolist()
        initial_equity = equity_values[0]
        final_equity = equity_values[-1]

        n_days = len(equity_values)

        # 总收益
        if initial_equity > 0:
            total_return = (final_equity - initial_equity) / initial_equity
        else:
            total_return = 0.0

        # 年化收益
        annual_return = (1.0 + total_return) ** (252.0 / max(n_days, 1)) - 1.0

        # 日收益率序列
        daily_returns = []
        for i in range(1, n_days):
            prev = equity_values[i - 1]
            if prev > 0:
                daily_returns.append(
                    (equity_values[i] - prev) / prev
                )
            else:
                daily_returns.append(0.0)

        # 年化波动率 & Sharpe
        if len(daily_returns) >= 2:
            mean_ret = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (
                len(daily_returns) - 1
            )
            std_daily = math.sqrt(variance)
            annual_vol = std_daily * math.sqrt(252)
            sharpe_ratio = (
                (mean_ret * 252) / annual_vol if annual_vol > 1e-10 else 0.0
            )
        else:
            annual_vol = 0.0
            sharpe_ratio = 0.0

        # 最大回撤
        peak = equity_values[0]
        max_drawdown = 0.0
        for eq in equity_values:
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak if peak > 0 else 0.0
            if dd < max_drawdown:
                max_drawdown = dd

        # Calmar 比率
        calmar_ratio = (
            annual_return / abs(max_drawdown)
            if abs(max_drawdown) > 1e-10
            else 0.0
        )

        # 胜率（按日收益率 > 0 的比例）
        n_positive = sum(1 for r in daily_returns if r > 0)
        win_rate = n_positive / len(daily_returns) if daily_returns else 0.0

        # 平均每笔盈亏
        n_trades = len(self._trades)
        if n_trades > 0 and initial_equity > 0:
            avg_trade_pnl = (final_equity - initial_equity) / n_trades
        else:
            avg_trade_pnl = 0.0

        return {
            "total_return": total_return,
            "annual_return": annual_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "calmar_ratio": calmar_ratio,
            "win_rate": win_rate,
            "total_trades": n_trades,
            "avg_trade_pnl": avg_trade_pnl,
        }

    # ── 保存 ──

    def save(self, output_dir: str | Path):
        """保存完整的投资档案到 output_dir。

        创建:
            meta.json, performance.json, trades.csv, daily_pnl.csv,
            positions_final.csv, risk_events.csv, signal_log.csv,
            summary_report.txt
        """
        save_dir = Path(output_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        perf = self._compute_performance()

        # ----- meta.json -----
        meta = {
            "strategy_name": self.strategy_name,
            "created_at": self.created_at.isoformat(),
            "config": self._config,
            "n_trades": len(self._trades),
            "n_signals": len(self._signals),
        }
        with open(save_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        # ----- performance.json -----
        with open(save_dir / "performance.json", "w", encoding="utf-8") as f:
            json.dump(perf, f, indent=2)

        # ----- trades.csv -----
        if self._trades:
            fieldnames = ["time", "symbol", "side", "qty", "price", "cost"]
            with open(save_dir / "trades.csv", "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self._trades)
        else:
            # Write header-only file
            with open(save_dir / "trades.csv", "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["time", "symbol", "side", "qty", "price", "cost"])

        # ----- daily_pnl.csv -----
        # Compute returns from equity curve
        sorted_equity = sorted(self._equity, key=lambda r: r["date"])
        daily_data = []
        for i, rec in enumerate(sorted_equity):
            ret = 0.0
            if i > 0 and sorted_equity[i - 1]["equity"] > 0:
                prev = sorted_equity[i - 1]["equity"]
                ret = (rec["equity"] - prev) / prev
            daily_data.append({
                "date": rec["date"],
                "equity": rec["equity"],
                "returns": ret,
            })
        if daily_data:
            with open(save_dir / "daily_pnl.csv", "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["date", "equity", "returns"])
                writer.writeheader()
                writer.writerows(daily_data)
        else:
            with open(save_dir / "daily_pnl.csv", "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["date", "equity", "returns"])

        # ----- positions_final.csv -----
        if self._positions:
            fieldnames = ["date", "symbol", "shares", "price"]
            with open(save_dir / "positions_final.csv", "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self._positions)
        else:
            with open(save_dir / "positions_final.csv", "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["date", "symbol", "shares", "price"])

        # ----- risk_events.csv (if any) -----
        if self._risk_events:
            with open(save_dir / "risk_events.csv", "w", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["event_type", "detail", "time"]
                )
                writer.writeheader()
                writer.writerows(self._risk_events)

        # ----- signal_log.csv (if any) -----
        if self._signals:
            fieldnames = ["date", "symbol", "score", "rank"]
            with open(save_dir / "signal_log.csv", "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self._signals)

        # ----- summary_report.txt -----
        report = self.generate_summary()
        with open(save_dir / "summary_report.txt", "w", encoding="utf-8") as f:
            f.write(report)

    # ── 报告生成 ──

    def generate_summary(self) -> str:
        """生成人类可读的投资档案报告。"""
        perf = self._compute_performance()

        lines = [
            "=" * 60,
            f"  Investment Summary — {self.strategy_name}",
            "=" * 60,
            "",
            "  Configuration",
            "  " + "-" * 40,
        ]
        for k, v in self._config.items():
            lines.append(f"    {k}: {v}")

        lines.extend([
            "",
            "  Performance",
            "  " + "-" * 40,
            f"    Total Return:    {perf['total_return'] * 100:>10.2f}%",
            f"    Annual Return:   {perf['annual_return'] * 100:>10.2f}%",
            f"    Sharpe Ratio:    {perf['sharpe_ratio']:>10.3f}",
            f"    Max Drawdown:    {perf['max_drawdown'] * 100:>10.2f}%",
            f"    Calmar Ratio:    {perf['calmar_ratio']:>10.3f}",
            f"    Win Rate:        {perf['win_rate'] * 100:>10.1f}%",
            f"    Total Trades:    {perf['total_trades']:>10d}",
            f"    Avg Trade PnL:   {perf['avg_trade_pnl']:>10.2f}",
        ])

        lines.extend([
            "",
            "  Trade Summary",
            "  " + "-" * 40,
            f"    Total:           {len(self._trades)}",
        ])
        if self._trades:
            buys = sum(1 for t in self._trades if t["side"] == "BUY")
            sells = len(self._trades) - buys
            lines.extend([
                f"    Buy:             {buys}",
                f"    Sell:            {sells}",
            ])

        lines.extend([
            "",
            "  Equity",
            "  " + "-" * 40,
            f"    Records:         {len(self._equity)}",
        ])
        if self._equity:
            first = self._equity[0]["equity"]
            last = self._equity[-1]["equity"]
            lines.extend([
                f"    Initial:         {first:>10.2f}",
                f"    Final:           {last:>10.2f}",
            ])

        lines.extend([
            "",
            "  Positions",
            "  " + "-" * 40,
            f"    Records:         {len(self._positions)}",
        ])
        if self._positions:
            for pos in self._positions:
                lines.append(
                    f"    {pos['symbol']:<12s} {pos['shares']:>8d} shares  "
                    f"@ {pos['price']:>8.2f}"
                )

        if self._risk_events:
            lines.extend([
                "",
                "  Risk Events",
                "  " + "-" * 40,
                f"    Count:           {len(self._risk_events)}",
            ])
            for ev in self._risk_events:
                lines.append(
                    f"    [{ev.get('time', '?')}] {ev['event_type']}: {ev['detail']}"
                )

        if self._signals:
            lines.extend([
                "",
                "  Signal Log",
                "  " + "-" * 40,
                f"    Records:         {len(self._signals)}",
            ])
            for sig in self._signals:
                lines.append(
                    f"    {sig['date']} {sig['symbol']:<10s} "
                    f"score={sig['score']:.4f} rank={sig['rank']}"
                )

        lines.extend([
            "",
            "=" * 60,
        ])
        return "\n".join(lines)
