#!/bin/bash
# cron_wrapper.sh — unified cron job wrapper with structured logging
#
# Env vars:
#   QUANT_MODULE:  log subdirectory (loader/cron/factor/quality/collector), default "cron"
#   QUANT_ENV:     "prod" or "dev", default "prod"
#
# Usage:
#   env QUANT_MODULE=loader cron_wrapper.sh bq_loader_us_5m python -m bigquery_loader.main

set -euo pipefail

JOB_NAME="$1"
shift

MODULE="${QUANT_MODULE:-}"
if [ -z "$MODULE" ]; then
    # Auto-detect module from job name prefix
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

# Primary log path (unified JSON / structured)
LOG_DIR="/var/log/quant/${ENV}/${MODULE}"
LOGFILE="${LOG_DIR}/${JOB_NAME}.log"

# Fallback: legacy path for backward compatibility
LEGACY_DIR="/home/quant/logs"
LEGACY_LOGFILE="${LEGACY_DIR}/${JOB_NAME}.log"
ALERTFILE="${LEGACY_DIR}/quant_alerts.log"

LOCKFILE="/tmp/${JOB_NAME}.lock"

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

# Run the command, capture all output
"$@" >> "$LOGFILE" 2>&1
RC=$?

TS="$(date '+%Y-%m-%d %H:%M:%S')"
if [ $RC -eq 0 ]; then
    echo "[$TS] ${JOB_NAME} OK  module=${MODULE} env=${ENV}" >> "$LOGFILE"
else
    echo "[$TS] ${JOB_NAME} FAILED (exit=$RC)  module=${MODULE} env=${ENV}" | tee -a "$LOGFILE" "$ALERTFILE" 2>/dev/null || true
fi

# Clean up flock lock file
rm -f "$LOCKFILE" 2>/dev/null || true

exit $RC
