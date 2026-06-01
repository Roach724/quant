"""Reporter — generate post-run HTML report with matplotlib charts."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


class Reporter:
    """Generate HTML summary report with embedded charts at end of run."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)

    def generate(self, stop_reason: str = ""):
        """Generate report.html with equity curve, drawdown, pie chart, summary."""
        logger.info("Generating report...")

        # Load data
        equity_path = self.output_dir / "equity_curve.csv"
        trades_path = self.output_dir / "trades.csv"
        
        if not equity_path.exists():
            logger.warning("No equity_curve.csv found — skipping report")
            return
        
        equity_df = pd.read_csv(equity_path, parse_dates=["timestamp"])
        
        if equity_df.empty:
            logger.warning("No equity data for report")
            return

        charts = []

        # 1. Equity curve
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(equity_df["timestamp"], equity_df["equity"], color="#2E86AB", linewidth=1.5)
        ax.set_title("Equity Curve")
        ax.set_ylabel("Equity ($)")
        ax.grid(alpha=0.3)
        charts.append(self._save_chart(fig, "equity_curve.png"))

        # 2. Drawdown
        equity = equity_df["equity"].values
        peak = pd.Series(equity).cummax()
        dd = ((equity - peak) / peak.replace(0, 1)) * 100
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.fill_between(range(len(dd)), 0, dd, color="#D81159", alpha=0.5)
        ax.set_title("Drawdown (%)")
        ax.set_ylabel("Drawdown %")
        ax.grid(alpha=0.3)
        charts.append(self._save_chart(fig, "drawdown.png"))

        # 3. Position concentration pie chart (last snapshot)
        snap_path = self.output_dir / "positions_snapshot.csv"
        if snap_path.exists():
            snap_df = pd.read_csv(snap_path, parse_dates=["timestamp"])
            if not snap_df.empty:
                last_ts = snap_df["timestamp"].max()
                current = snap_df[snap_df["timestamp"] == last_ts]
                if not current.empty and (current["mkt_value"] > 0).any():
                    fig, ax = plt.subplots(figsize=(6, 6))
                    labels = current["symbol"].tolist()
                    values = current["mkt_value"].tolist()
                    ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
                    ax.set_title("Portfolio Allocation")
                    charts.append(self._save_chart(fig, "allocation.png"))

        # 4. Signal frequency
        sig_path = self.output_dir / "signals.csv"
        if sig_path.exists():
            sig_df = pd.read_csv(sig_path)
            if not sig_df.empty:
                buy_counts = sig_df[sig_df["side"] == "buy"]["symbol"].value_counts()
                if not buy_counts.empty:
                    fig, ax = plt.subplots(figsize=(10, 4))
                    buy_counts.plot(kind="bar", ax=ax, color="#A1C181")
                    ax.set_title("Buy Signal Frequency by Symbol")
                    ax.set_xlabel("Symbol")
                    ax.set_ylabel("Count")
                    charts.append(self._save_chart(fig, "signals.png"))

        # 5. Summary metrics
        total_return = (equity_df["equity"].iloc[-1] / equity_df["equity"].iloc[0] - 1) * 100
        n_days = len(equity_df)

        max_dd = float(dd.min()) if len(dd) > 0 else 0
        max_equity = equity_df["equity"].max()
        min_equity = equity_df["equity"].min()

        stop_reason_display = stop_reason or "market close"

        total_trades = 0
        if trades_path.exists():
            trades_df = pd.read_csv(trades_path)
            total_trades = len(trades_df)

        # Render HTML
        chart_html = "\n".join(
            f'<h3>{name}</h3><img src="{path}" style="max-width:800px;">'
            for name, path in charts
        )

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Live Run Report</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 40px; color: #333; }}
h1 {{ color: #2E86AB; }}
table {{ border-collapse: collapse; width: 100%; max-width: 600px; }}
td, th {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }}
th {{ background: #f5f5f5; }}
</style></head><body>
<h1>📊 Live Run Report</h1>
<h2>Summary</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Total Return</td><td>{total_return:+.2f}%</td></tr>
<tr><td>Stop Reason</td><td style="color:#D81159">{stop_reason_display}</td></tr>
<tr><td>Trading Days</td><td>{n_days}</td></tr>
<tr><td>Total Trades</td><td>{total_trades}</td></tr>
<tr><td>Max Drawdown</td><td>{max_dd:.2f}%</td></tr>
<tr><td>Max Equity</td><td>${max_equity:,.0f}</td></tr>
<tr><td>Min Equity</td><td>${min_equity:,.0f}</td></tr>
</table>
<h2>Charts</h2>
{chart_html}
</body></html>"""

        report_path = self.output_dir / "report.html"
        report_path.write_text(html)
        logger.info("Report written to %s", report_path)

    def _save_chart(self, fig, name: str):
        chart_path = self.output_dir / name
        fig.savefig(chart_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return (name.replace(".png", "").replace("_", " ").title(), name)
