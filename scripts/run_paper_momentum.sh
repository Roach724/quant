#!/bin/bash
# Paper Runner — SimpleMomentum on US daily data
set -e
cd /opt/quant

MARKET="${1:-us}"
START="${2:-2026-01-01}"
END="${3:-$(date +%Y-%m-%d)}"
CAPITAL="${4:-100000}"

echo "=== Paper Runner: SimpleMomentum ==="
echo "Market: $MARKET | $START -> $END | Capital: \$$CAPITAL"

mkdir -p output

python3.12 run_paper.py \
  --market "$MARKET" \
  --start "$START" \
  --end "$END" \
  --capital "$CAPITAL" \
  --strategy SimpleMomentum \
  --data-source bq \
  --lookback 20 \
  --top-k 20 \
  --rebalance-every 5 \
  --output "./output/paper_momentum_${MARKET}_$(date +%Y%m%d_%H%M%S)" \
  2>&1 | tee "./output/paper_momentum_${MARKET}_latest.log"

echo ""
echo "=== Done. Output in ./output/ ==="
