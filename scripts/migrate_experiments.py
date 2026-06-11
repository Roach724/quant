"""Migrate existing experiments to new ExperimentManager system."""
import json
import sys
from datetime import datetime, timezone
from google.cloud import bigquery

PROJECT = "deductive-notch-495015-c2"
REGISTRY = "/var/quant/experiments/registry.json"

MIGRATIONS = [
    {"old_id": "exp1_ml_v2",       "type": "live", "market": "us", "strategy": "ml",  "version": 2,
     "name": "MLPrediction us_tech v2", "config": "live/configs/exp1_ml_us.yaml"},
    {"old_id": "exp2_simple_momentum","type": "live", "market": "us", "strategy": "mom", "version": 1,
     "name": "SimpleMomentum US (control)", "config": "live/configs/exp2_momentum_us.yaml"},
    {"old_id": "exp3_ml_hk",       "type": "live", "market": "hk", "strategy": "ml",  "version": 3,
     "name": "MLPrediction hk_tech v3", "config": "live/configs/exp3_ml_hk.yaml"},
    {"old_id": "exp4_momentum_hk", "type": "live", "market": "hk", "strategy": "mom", "version": 2,
     "name": "SimpleMomentum HK (control)", "config": "live/configs/exp4_momentum_hk.yaml"},
]


def main():
    bq = bigquery.Client(project=PROJECT)

    try:
        with open(REGISTRY) as f:
            reg = json.load(f)
    except FileNotFoundError:
        from pathlib import Path
        Path(REGISTRY).parent.mkdir(parents=True, exist_ok=True)
        reg = {"experiments": {}}

    for m in MIGRATIONS:
        new_id = f"{m['type']}_{m['market']}_{m['strategy']}_v{m['version']}"
        print(f"\n=== {m['old_id']} -> {new_id} ===")

        if new_id in reg["experiments"]:
            print(f"  SKIP: already registered")
            continue

        now = datetime.now(timezone.utc).isoformat()
        reg["experiments"][new_id] = {
            "id": new_id, "type": m["type"], "market": m["market"],
            "strategy": m["strategy"], "version": m["version"],
            "status": "running", "config_path": m["config"],
            "created_at": now, "name": m["name"],
            "current_run": None, "runs": [],
        }
        print(f"  REGISTERED: {new_id}")

        # Count old data
        eq_cnt = bq.query(
            f"SELECT COUNT(*) AS cnt FROM quant.experiment_equity WHERE exp_id='{m['old_id']}'"
        ).result().to_dataframe()["cnt"][0]
        tr_cnt = bq.query(
            f"SELECT COUNT(*) AS cnt FROM quant.experiment_trades WHERE exp_id='{m['old_id']}'"
        ).result().to_dataframe()["cnt"][0]
        print(f"  Old data: {eq_cnt} equity, {tr_cnt} trades")

        # Backfill run_id (best-effort, streaming buffer may block UPDATE)
        run_id = f"migrate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        try:
            bq.query(
                f"UPDATE quant.experiment_equity SET run_id='{run_id}' "
                f"WHERE exp_id='{m['old_id']}' AND run_id IS NULL"
            ).result()
            print(f"  equity run_id backfilled")
        except Exception as e:
            print(f"  WARNING: equity UPDATE failed: {e}")

        try:
            bq.query(
                f"UPDATE quant.experiment_trades SET run_id='{run_id}' "
                f"WHERE exp_id='{m['old_id']}' AND run_id IS NULL"
            ).result()
            print(f"  trades run_id backfilled")
        except Exception as e:
            print(f"  WARNING: trades UPDATE failed: {e}")

    with open(REGISTRY, 'w') as f:
        json.dump(reg, f, indent=2)
    print(f"\nRegistry saved to {REGISTRY}")


if __name__ == "__main__":
    main()
