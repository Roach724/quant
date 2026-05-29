#!/bin/bash
echo "=============================================="
echo "  Quant VM Status — $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

echo ""
echo "--- OpenD (port 11111) ---"
if ss -tlnp | grep -q 11111; then
    echo "  RUNNING"
else
    echo "  NOT RUNNING !!!"
fi

echo ""
echo "--- ws_collector ---"
systemctl is-active ws-collector 2>/dev/null || echo "  NOT RUNNING"
if systemctl is-active --quiet ws-collector 2>/dev/null; then
    tail -3 /home/quant/logs/ws_collector.log 2>/dev/null | while read line; do
        echo "  $line"
    done
fi

echo ""
echo "--- Quality Timer ---"
systemctl list-timers quality-check.timer --no-pager 2>/dev/null | tail -3

echo ""
echo "--- Logs disk usage ---"
du -sh /home/quant/logs/ 2>/dev/null || echo "  (no logs dir)"

echo ""
echo "--- Query API ---"
systemctl is-active query-api 2>/dev/null || echo "  NOT RUNNING"

echo ""
echo "--- Cron jobs ---"
sudo crontab -u quant -l 2>/dev/null | grep -v '^#' | grep -v '^$' | while read line; do
    echo "  $line"
done

echo ""
echo "--- Recent alerts (last 10) ---"
if [ -f /home/quant/logs/quant_alerts.log ]; then
    tail -10 /home/quant/logs/quant_alerts.log
else
    echo "  (no alerts)"
fi

echo ""
echo "--- GCS files today ---"
TODAY=$(date +%d)
GCS_BASE="gs://deductive-notch-495015-c2-quant-data/raw"
for mkt in us hk; do
    for freq in 5m 1d; do
        count=$(gsutil ls "${GCS_BASE}/${mkt}/bars/freq=${freq}/year=2026/month=05/day=${TODAY}/" 2>/dev/null | wc -l)
        echo "  ${mkt}_${freq}: ${count} files"
    done
done

echo ""
echo "--- BQ row counts ---"
for tbl in us_bars_5m us_bars_1d hk_bars_5m hk_bars_1d; do
    cnt=$(bq query --nouse_legacy_sql --format=sparse --project_id=deductive-notch-495015-c2 "SELECT COUNT(*) AS c FROM quant.${tbl}" 2>/dev/null | tail -1)
    echo "  ${tbl}: ${cnt}"
done

echo ""
echo "--- Disk usage ---"
df -h / | tail -1

echo ""
echo "--- OpenD log (last 3 lines) ---"
tail -3 /tmp/opend.log 2>/dev/null || echo "  (no log)"

echo ""
echo "=============================================="
