#!/bin/bash
# Backfill chain: HK 1d → US 5m → HK 5m → BQ Loader
# Triggered by at-job after US 1d backfill completes
set -e
cd /opt/quant

GCS="deductive-notch-495015-c2-quant-data"
PROJECT="deductive-notch-495015-c2"
LOG="/home/quant/logs/backfill_chain.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

# ── Step 1: HK 1d (2020-2026, 15 symbols, ~30min) ──
log "=== [1/4] HK 1d backfill ==="
python3.12 collectors/backfill.py \
  --start 2020-01-01 --end 2026-05-28 \
  --source futu_stock --all --market hk \
  --frequency 1d --gcs-bucket "$GCS" 2>&1 | tee -a "$LOG"
log "HK 1d done."

# ── Step 2: US 5m (last 30 days, 234 symbols, ~15min) ──
START_5M=$(date -d '30 days ago' +%Y-%m-%d)
END_5M=$(date +%Y-%m-%d)
log "=== [2/4] US 5m backfill ($START_5M → $END_5M) ==="
python3.12 collectors/backfill.py \
  --start "$START_5M" --end "$END_5M" \
  --source futu_stock --all --market us \
  --frequency 5m --gcs-bucket "$GCS" 2>&1 | tee -a "$LOG"
log "US 5m done."

# ── Step 3: HK 5m (last 30 days, 15 symbols, ~2min) ──
log "=== [3/4] HK 5m backfill ($START_5M → $END_5M) ==="
python3.12 collectors/backfill.py \
  --start "$START_5M" --end "$END_5M" \
  --source futu_stock --all --market hk \
  --frequency 5m --gcs-bucket "$GCS" 2>&1 | tee -a "$LOG"
log "HK 5m done."

# ── Step 4: BQ Loader (1d: full history, 5m: last 30 days) ──
log "=== [4/4] BQ Loader ==="

for market in us hk; do
  for freq in 1d 5m; do
    TABLE="${market}_bars_${freq}"
    log "Loading $TABLE ..."

    if [ "$freq" = "1d" ]; then
      export LOAD_DAYS=2500
      export START_DATE=2020-01-01
    else
      export LOAD_DAYS=30
      unset START_DATE
    fi

    GCS_BUCKET="$GCS" GCP_PROJECT="$PROJECT" \
      MARKET="$market" FREQUENCY="$freq" TABLE="$TABLE" \
      python3.12 -m bigquery_loader.main 2>&1 | tee -a "$LOG"
    log "$TABLE done."
  done
done

log "=== ALL DONE ==="
