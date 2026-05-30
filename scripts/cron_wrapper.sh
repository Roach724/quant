#!/bin/bash
JOB_NAME="$1"
shift
LOGFILE="/home/quant/logs/${JOB_NAME}.log"
ALERTFILE="/home/quant/logs/quant_alerts.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${JOB_NAME} START" >> "$LOGFILE"
"$@" >> "$LOGFILE" 2>&1
RC=$?
if [ $RC -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${JOB_NAME} OK" >> "$LOGFILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${JOB_NAME} FAILED (exit=$RC)" | tee -a "$LOGFILE" "$ALERTFILE"
fi
exit $RC
