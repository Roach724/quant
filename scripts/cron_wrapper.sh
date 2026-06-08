#!/bin/bash
# cron_wrapper.sh — unified cron job wrapper with structured logging
#
# Env vars:
#   QUANT_MODULE:  log subdirectory (loader/cron/factor/quality/collector)
#   QUANT_ENV:     "prod" or "dev", default "prod"
#
# Usage:
#   env QUANT_MODULE=loader cron_wrapper.sh bq_loader_us_5m python -m bigquery_loader.main

set -euo pipefail

JOB_NAME="$1"
shift

# Auto-detect module from job name prefix
MODULE="${QUANT_MODULE:-}"
if [ -z "$MODULE" ]; then
    case "$JOB_NAME" in
        bq_loader_*)          MODULE="loader" ;;
        collector_*|f10_collector_*) MODULE="cron" ;;
        collect_*)            MODULE="factor" ;;
        load_*)               MODULE="factor" ;;
        quality_*)            MODULE="quality" ;;
        *)                    MODULE="cron" ;;
    esac
fi

ENV="${QUANT_ENV:-prod}"

# Primary log path (unified structured)
LOG_DIR="/var/log/quant/${ENV}/${MODULE}"
LOGFILE="${LOG_DIR}/${JOB_NAME}.log"

# Fallback: legacy path
LEGACY_DIR="/home/quant/logs"
LEGACY_LOGFILE="${LEGACY_DIR}/${JOB_NAME}.log"
ALERTFILE="${LEGACY_DIR}/quant_alerts.log"

LOCKFILE="/tmp/cron_${JOB_NAME}.lock"
GLOBAL_SEM="/tmp/cron_global.sem"
MAX_CONCURRENT=3

# Try primary log dir; fall back to legacy
if mkdir -p "$LOG_DIR" 2>/dev/null && [ -w "$LOG_DIR" ]; then
    USE_LEGACY=false
else
    mkdir -p "$LEGACY_DIR" 2>/dev/null || true
    LOG_DIR="$LEGACY_DIR"
    LOGFILE="$LEGACY_LOGFILE"
    USE_LEGACY=true
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${JOB_NAME} START  module=${MODULE} env=${ENV}" >> "$LOGFILE"

# ── Concurrency control ──
# 1. Job-level: prevent the same cron job from running twice
( flock -n 9 || {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${JOB_NAME} SKIPPED (already running)" >> "$LOGFILE"
    exit 0
}

# 2. Global: limit total concurrent cron Python processes
ACQUIRED_SEM=false
for i in $(seq 1 30); do
    RUNNING_COUNT=$(pgrep -cf 'cron_wrapper\\|python.*collector\\|python.*loader\\|python.*quality\\|python.*factor' 2>/dev/null || echo 0)
    if [ "$RUNNING_COUNT" -lt "$MAX_CONCURRENT" ]; then
        ACQUIRED_SEM=true
        break
    fi
    sleep 2
done
if [ "$ACQUIRED_SEM" = false ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${JOB_NAME} SKIPPED (concurrency limit=$MAX_CONCURRENT)" >> "$LOGFILE"
    exit 0
fi

# Run the command, capture all output
"$@" >> "$LOGFILE" 2>&1
RC=$?

) 9>"$LOCKFILE"

TS="$(date '+%Y-%m-%d %H:%M:%S')"
if [ $RC -eq 0 ]; then
    echo "[$TS] ${JOB_NAME} OK  module=${MODULE} env=${ENV}" >> "$LOGFILE"
else
    echo "[$TS] ${JOB_NAME} FAILED (exit=$RC)  module=${MODULE} env=${ENV}" | tee -a "$LOGFILE" "$ALERTFILE" 2>/dev/null || true
fi

# Clean up flock lock file
rm -f "$LOCKFILE" 2>/dev/null || true

exit $RC
