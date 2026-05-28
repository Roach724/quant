#!/bin/bash
set -e

# --- Generate FutuOpenD config from template ---
if [ -n "${FUTU_LOGIN_ACCOUNT}" ] && [ -n "${FUTU_LOGIN_PWD_MD5}" ]; then
  echo "[start_collect] Generating FutuOpenD.xml from template..."
  sed -e "s/\${FUTU_LOGIN_ACCOUNT}/${FUTU_LOGIN_ACCOUNT}/g" \
      -e "s/\${FUTU_LOGIN_PWD_MD5}/${FUTU_LOGIN_PWD_MD5}/g" \
      /opt/FutuOpenD.xml.template > /opt/opend/FutuOpenD.xml
else
  echo "[start_collect] WARNING: FUTU_LOGIN_ACCOUNT or FUTU_LOGIN_PWD_MD5 not set. OpenD may fail to login."
  cp /opt/FutuOpenD.xml.template /opt/opend/FutuOpenD.xml
fi

# --- Start OpenD in background ---
OPEND_HOST="${OPEND_HOST:-127.0.0.1}"
OPEND_PORT="${OPEND_PORT:-11111}"

echo "[start_collect] Starting FutuOpenD..."
# Set LD_LIBRARY_PATH so OpenD finds its bundled .so files
export LD_LIBRARY_PATH=/opt/opend:${LD_LIBRARY_PATH}
/opt/opend/FutuOpenD -cfg_file=/opt/opend/FutuOpenD.xml &
OPEND_PID=$!

# --- Wait for OpenD login ---
echo "[start_collect] Waiting for OpenD to login (host=${OPEND_HOST}, port=${OPEND_PORT})..."
MAX_RETRIES=30
for i in $(seq 1 ${MAX_RETRIES}); do
  sleep 1
  if python3 -c "
from futu import *
ctx = OpenQuoteContext(host='${OPEND_HOST}', port=${OPEND_PORT})
ret, state = ctx.get_global_state()
ctx.close()
exit(0 if ret == RET_OK and state.get('qot_logined') else 1)
" 2>/dev/null; then
    echo "[start_collect] OpenD logged in successfully (attempt ${i})."
    break
  fi
  if [ $i -eq ${MAX_RETRIES} ]; then
    echo "[start_collect] ERROR: OpenD failed to login after ${MAX_RETRIES} attempts."
    kill ${OPEND_PID} 2>/dev/null || true
    exit 1
  fi
done

# --- Run collector ---
echo "[start_collect] Running collector (source=${COLLECTOR_SOURCE})..."
cd /app && python3 -m collectors.main
COLLECTOR_EXIT=$?

# --- Shutdown OpenD ---
echo "[start_collect] Shutting down OpenD (PID=${OPEND_PID})..."
kill ${OPEND_PID} 2>/dev/null || true
wait ${OPEND_PID} 2>/dev/null || true

echo "[start_collect] Collector exited with code ${COLLECTOR_EXIT}"
exit ${COLLECTOR_EXIT}
