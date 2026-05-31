#!/usr/bin/env python3.12
"""W2 Experiment: Momentum vs LightGBM walk-forward comparison.

Runs both strategies via PaperRunner using BQ data, records metrics.
"""
import sys, os, yaml, logging, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from run_paper import PaperRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("w2")

def main():
    import argparse as _ap
    _cli = _ap.ArgumentParser(add_help=False)
    _cli.add_argument("--factor-source", default="tech",
                      choices=["tech", "fundamental", "all"],
                      help="Factor source for ML strategy (default: tech)")
    _cli_args, _ = _cli.parse_known_args()

    config_path = os.environ.get("W2_CONFIG", "experiment/config_w2.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    log.info("=" * 60)
    log.info("W2 Experiment: %s", config["experiment"]["name"])
    log.info("=" * 60)

    wf = config["walk_forward"]
    symbols = wf["symbols"]
    capital = wf.get("capital", 100000)
    results = {}

    for strat_cfg in config["strategies"]:
        name = strat_cfg["name"]
        cls_name = strat_cfg["class"]
        params = strat_cfg.get("params", {})

        log.info("--- %s (%s) ---", name, cls_name)
        t0 = time.time()

        strategy_kwargs = {**params}
        if cls_name == "SimpleMomentum":
            strategy_kwargs["top_k"] = params.get("top_k", 20)
        if cls_name == "MLLightGBM" and "factor_source" not in strategy_kwargs:
            strategy_kwargs["factor_source"] = _cli_args.factor_source

        try:
            runner = PaperRunner({
                "market": wf["market"],
                "capital": capital,
                "strategy": cls_name,
                "strategy_kwargs": strategy_kwargs,
                "start": wf["paper_start"],
                "end": wf["paper_end"],
                "symbols": symbols,
                "data_source": "bq",
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
    print("  📊  W2 EXPERIMENT RESULTS")
    print("=" * 70)
    print(f"  Period: {wf['paper_start']} → {wf['paper_end']} | {len(symbols)} stocks | ${capital:,.0f}")
    print("-" * 70)
    print(f"  {'Metric':<22s} {'Momentum':>14s} {'ML LightGBM':>14s}")
    print("-" * 70)
    for metric in ["total_return", "annual_return", "sharpe_ratio", "max_drawdown", "win_rate"]:
        v1 = results.get("momentum_baseline", {}).get(metric, 0) or 0
        v2_val = results.get("ml_lightgbm", {})
        v2 = v2_val.get(metric, 0) or 0
        if "error" in v2_val:
            v2_str = f"{'ERROR':>14s}"
        elif metric in ("total_return", "annual_return", "max_drawdown", "win_rate"):
            print(f"  {metric:<22s} {v1*100:>13.2f}% {v2*100:>13.2f}%")
        else:
            print(f"  {metric:<22s} {v1:>14.2f} {v2:>14.2f}")
    print("-" * 70)

    # Check for errors
    for name in results:
        if "error" in results[name]:
            print(f"  ⚠️  {name}: {results[name]['error']}")
    print("=" * 70)

if __name__ == "__main__":
    main()
