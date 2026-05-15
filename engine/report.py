import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import io, base64
from engine.metrics import summary as metrics_summary

TPL = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Backtest: {name}</title>
<style>body{{font-family:sans-serif;max-width:960px;margin:0 auto;padding:20px;background:#f8f9fa}}
h1{{color:#1a1a2e}}h2{{color:#333;border-bottom:2px solid #1a1a2e;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{padding:8px 12px;border:1px solid #ddd;text-align:right}}
th{{background:#1a1a2e;color:#fff}}img{{max-width:100%;margin:16px 0;border-radius:4px;box-shadow:0 2px 8px rgba(0,0,0,.1)}}
.metric{{display:inline-block;margin:8px 16px 8px 0}}
.metric .label{{font-size:12px;color:#888}} .metric .value{{font-size:24px;font-weight:700;color:#1a1a2e}}
</style></head><body>
<h1>{name}</h1>
<div>{metrics_html}</div>
<h2>Equity Curve</h2><img src="data:image/png;base64,{equity_img}">
<h2>Drawdown</h2><img src="data:image/png;base64,{dd_img}">
<h2>Monthly Returns</h2><img src="data:image/png;base64,{monthly_img}">
</body></html>"""


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def generate(result, output_path: str):
    s = metrics_summary(result)
    metrics_html = " ".join(
        f'<div class="metric"><div class="label">{k}</div><div class="value">{v}</div></div>'
        for k, v in s.items()
    )

    eq = result.portfolio.equity_curve

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(eq.index, eq.values, color="#1a1a2e", linewidth=1)
    ax.set_title("Equity Curve")
    equity_img = _fig_to_b64(fig)

    rolling_max = eq.expanding().max()
    dd = (eq - rolling_max) / rolling_max
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.fill_between(dd.index, dd.values, 0, color="#e74c3c", alpha=0.3)
    ax.plot(dd.index, dd.values, color="#e74c3c", linewidth=0.5)
    ax.set_title("Drawdown")
    dd_img = _fig_to_b64(fig)

    if len(eq) >= 21:
        monthly = eq.resample("ME").last().pct_change().dropna()
    else:
        monthly = eq.pct_change().dropna()
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.bar(range(len(monthly)), monthly.values, color=["#2ecc71" if v >= 0 else "#e74c3c" for v in monthly.values])
    ax.set_title("Monthly Returns")
    monthly_img = _fig_to_b64(fig)

    html = TPL.format(name=result.strategy_name, metrics_html=metrics_html,
                      equity_img=equity_img, dd_img=dd_img, monthly_img=monthly_img)
    with open(output_path, "w") as f:
        f.write(html)
