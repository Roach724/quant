#!/bin/bash
# Daily BigQuery → GCS backup. Runs after all data collection is complete.
# Schedule: 06:00 UTC daily via cron.
#
# Exports each table as Parquet to gs://BUCKET/backup/{table}/{YYYY-MM-DD}/data_*.parquet

set -euo pipefail

BUCKET="deductive-notch-495015-c2-quant-data"
PROJECT="deductive-notch-495015-c2"
DATASET="quant"
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting BQ → GCS backup for ${YESTERDAY}"

# Bar tables
for market in us hk; do
  for freq in 1d 5m; do
    table="${market}_bars_${freq}"
    dest="gs://${BUCKET}/backup/${table}/${YESTERDAY}/data_*.parquet"
    echo "[$(date '+%H:%M:%S')] Backing up ${table} → ${dest}"
    bq extract \
      --destination_format=PARQUET \
      --compression=SNAPPY \
      "${PROJECT}:${DATASET}.${table}" \
      "${dest}" 2>&1 || echo "[$(date '+%H:%M:%S')] WARNING: backup failed for ${table}"
  done
done

echo "[$(date '+%H:%M:%S')] Backup complete"
