#!/usr/bin/env python3.12
"""W3 Experiment: 5m-frequency strategy validation.

Runs Momentum + ML strategies on us_bars_5m BQ data via PaperRunner.
⚠️ POC only — 29 days of 5m data means limited statistical significance.
"""
import sys, os, yaml, logging, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("w3")

def main():
    config_path = os.environ.get("W3_CONFIG", "experiment/config_w3_5m.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    log.info("=" * 60)
    log.info("W3 5m Experiment: %s", config["experiment"]["name"])
    log.info("⚠️  POC — only %s → %s (29 days)", config["data"]["start"], config["data"]["end"])
    log.info("=" * 60)

    wf = config["walk_forward"]
    symbols = wf["symbols"]
    capital = wf.get("capital", 100000)
    results = {}

    # Import PaperRunner (local)
    from run_paper import PaperRunner

    for strat_cfg in config["strategies"]:
        name = strat_cfg["name"]
        cls_name = strat_cfg["class"]
        params = strat_cfg.get("params", {})

        log.info("--- %s (%s) ---", name, cls_name)
        t0 = time.time()

        strategy_kwargs = {**params}
        if cls_name == "SimpleMomentum":
            strategy_kwargs["top_k"] = params.get("top_k", 10)

        try:
            runner = PaperRunner({
                "market": wf["market"],
                "capital": capital,
                "strategy": cls_name,
                "strategy_kwargs": strategy_kwargs,
                "start": wf["paper_start"],
                "end": wf["paper_end"],
                "symbols": symbols,
                "data_source": "bq_5m",
            })
            result = runner.run()
            results[name] = result["metrics"]
            elapsed = time.time() - t0
            log.info("%s done in %.0fs. Return: %.2f%%, Sharpe: %.2f",
                     name, elapsed,
                     result["metrics"].get("total_return", 0) * 100,
                     result["metrics"].get("sharpe_ratio", 0))

        except Exception as e:
            log.error("%s failed: %s", name, e)
            import traceback
            log.error(traceback.format_exc())
            results[name] = {"error": str(e)}

    # Print comparison
    print()
    print("=" * 70)
    print("  📊  W3 5M EXPERIMENT RESULTS (POC)")
    print("=" * 70)
    print(f"  Period: {wf['paper_start']} → {wf['paper_end']} | {len(symbols)} stocks | ${capital:,.0f}")
    print(f"  ⚠️  29 days only — statistical significance limited")
    print("-" * 70)
    print(f"  {'Metric':<22s} {'Momentum 5m':>14s} {'ML 5m':>14s}")
    print("-" * 70)
    for metric in ["total_return", "annual_return", "sharpe_ratio", "max_drawdown", "win_rate"]:
        v1 = results.get("momentum_5m", {}).get(metric, 0) or 0
        v2_val = results.get("ml_5m", {})
        v2 = v2_val.get(metric, 0) or 0
        if "error" in v2_val:
            v2_str = f"{'ERROR':>14s}"
        elif metric in ("total_return", "annual_return", "max_drawdown", "win_rate"):
            print(f"  {metric:<22s} {v1*100:>13.2f}% {v2*100:>13.2f}%")
        else:
            print(f"  {metric:<22s} {v1:>14.2f} {v2:>14.2f}")
    print("-" * 70)
    for name in results:
        if "error" in results[name]:
            print(f"  ⚠️  {name}: {results[name]['error']}")
    print(f"  ⚠️  Note: 29-day POC — do not draw strong conclusions")
    print("=" * 70)

if __name__ == "__main__":
    main()
