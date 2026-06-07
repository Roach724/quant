#!/bin/bash
set -euo pipefail
# Ensure repo is trusted regardless of SSH user
git config --global --add safe.directory /opt/quant-prod 2>/dev/null || true

# deploy.sh — Deploy stable branch to /opt/quant-prod
# Invoked by GitHub Actions CD pipeline via gcloud compute ssh

PROD_ROOT="/opt/quant-prod"
# Ensure correct ownership (prevents "Permission denied" on non-quant files)
sudo chown -R quant:quant "$PROD_ROOT" 2>/dev/null || true
HISTORY_FILE="$PROD_ROOT/.deploy_history"
VENV_PYTHON="$PROD_ROOT/.venv/bin/python3.12"
VENV_PIP="$PROD_ROOT/.venv/bin/pip"
MARKET_CHECK="$PROD_ROOT/scripts/market_open.sh"
LOG_TAG="[deploy $(date '+%Y-%m-%d %H:%M:%S')]"

log() { echo "$LOG_TAG $*"; }
fail() { log "FAILED: $*"; exit 1; }

# ── ① Backup current state ──────────────────────────────────────────
CURRENT_COMMIT=$(cd "$PROD_ROOT" && sudo -u quant git rev-parse HEAD 2>/dev/null || echo "unknown")
log "Backing up current commit: $CURRENT_COMMIT"

# ── ② Git fetch + checkout ──────────────────────────────────────────
log "Fetching origin/stable..."
cd "$PROD_ROOT"
sudo -u quant git fetch origin stable || fail "git fetch failed"
sudo -u quant git reset --hard origin/stable || fail "git reset failed"
NEW_COMMIT=$(sudo -u quant git rev-parse HEAD)
log "Checked out: $NEW_COMMIT"

# ── ③ Sync dependencies ─────────────────────────────────────────────
log "Installing dependencies..."
"$VENV_PIP" install -r requirements.txt --quiet || fail "pip install failed"

# ── ④ Smoke test ────────────────────────────────────────────────────
log "Running smoke tests..."

# Test 1: Core modules are importable
if ! "$VENV_PYTHON" -c "
import engine, live, strategies, factors, ml, collectors, quality, oms, paper
print('All core modules imported successfully')
"; then
    log "Smoke test 1 FAILED: module import"
    # Auto-rollback
    if [ "$CURRENT_COMMIT" != "unknown" ]; then
        log "Rolling back to $CURRENT_COMMIT..."
        cd "$PROD_ROOT" && sudo -u quant git checkout "$CURRENT_COMMIT"
        "$VENV_PIP" install -r requirements.txt --quiet
        if [ -x "$MARKET_CHECK" ] && "$MARKET_CHECK"; then log "⚠️  MARKET OPEN — skipping ws_collector restart during rollback"; else sudo systemctl restart ws-collector; fi
        echo "{\"time\":\"$(date -Iseconds)\",\"commit\":\"$NEW_COMMIT\",\"status\":\"failed\",\"trigger\":\"github\",\"detail\":\"smoke_import\"}" >> "$HISTORY_FILE"
        echo "{\"time\":\"$(date -Iseconds)\",\"commit\":\"$CURRENT_COMMIT\",\"status\":\"rolled_back\",\"trigger\":\"auto\",\"detail\":\"smoke_import\"}" >> "$HISTORY_FILE"
    fi
    fail "Smoke test 1: module import"
fi

# Test 2: Config files are parseable (check a known-good YAML)
if [ -f live/configs/exp1_ml_us.yaml ]; then
    PYTHONPATH=. "$VENV_PYTHON" scripts/smoke_test_config.py || fail "Smoke test 2: config parse"
else
    log "Smoke test 2: skipped (no config file)"
fi

log "All smoke tests passed"

# ── ④.5 Build admin frontend ──────────────────────────────────────────
log "Building admin frontend..."
FRONTEND_DIR="$PROD_ROOT/admin/frontend"
if [ -f "$FRONTEND_DIR/package.json" ]; then
    cd "$FRONTEND_DIR"
    npm install 2>&1 | tail -3 || log "WARNING: npm install had warnings"
    npm run build 2>&1 | tail -5 || log "WARNING: frontend build failed (admin may use stale dist)"
    cd "$PROD_ROOT"
    log "Frontend build complete"
else
    log "Frontend dir not found — skipping build"
fi

# ── ⑤ Restart production services ───────────────────────────────────
log "Restarting quant-admin..."
sudo systemctl restart quant-admin || log "WARNING: quant-admin restart failed (non-fatal)"
sleep 2

log "Restarting ws-collector..."

# Market-hour guard: skip ws_collector restart during trading hours.
# Manual restart (sudo systemctl restart ws-collector) still works
# but requires explicit operator approval per deployment policy.
if [ -x "$MARKET_CHECK" ] && "$MARKET_CHECK"; then
    log "⚠️  MARKET OPEN — skipping ws_collector restart to protect live data"
    log "If restart is urgently needed, request operator approval."
else
    sudo systemctl restart ws-collector || fail "systemctl restart failed"
    sleep 3
fi

# ── ⑤.5 Protect running experiments ─────────────────────────────────
log "Checking experiment units..."
EXP_UNITS=$(sudo systemctl list-units 'exp-*' --no-legend --no-pager 2>/dev/null | awk '{print $1}' || true)
if [ -n "$EXP_UNITS" ]; then
    log "Found running experiments: $(echo $EXP_UNITS | tr '\n' ' ')"
    if [ -x "$MARKET_CHECK" ] && "$MARKET_CHECK"; then
        log "⚠️  MARKET OPEN — skipping experiment restart to protect live runs"
    else
        for unit in $EXP_UNITS; do
            log "Restarting $unit..."
            sudo systemctl restart "$unit" 2>/dev/null || log "WARNING: failed to restart $unit (non-fatal)"
        done
    fi
else
    log "No active experiment units"
fi

# ── ⑥ Post-deploy verification ──────────────────────────────────────
log "Verifying service status..."

SERVICE_STATUS=$(sudo systemctl is-active ws-collector 2>/dev/null || echo "inactive")
if [ "$SERVICE_STATUS" != "active" ]; then
    log "ws-collector not active (status=$SERVICE_STATUS)"
    # Auto-rollback
    if [ "$CURRENT_COMMIT" != "unknown" ]; then
        log "Rolling back to $CURRENT_COMMIT..."
        cd "$PROD_ROOT" && sudo -u quant git checkout "$CURRENT_COMMIT"
        "$VENV_PIP" install -r requirements.txt --quiet
        if [ -x "$MARKET_CHECK" ] && "$MARKET_CHECK"; then log "⚠️  MARKET OPEN — skipping ws_collector restart during rollback"; else sudo systemctl restart ws-collector; fi
        echo "{\"time\":\"$(date -Iseconds)\",\"commit\":\"$NEW_COMMIT\",\"status\":\"failed\",\"trigger\":\"github\",\"detail\":\"service_inactive\"}" >> "$HISTORY_FILE"
        echo "{\"time\":\"$(date -Iseconds)\",\"commit\":\"$CURRENT_COMMIT\",\"status\":\"rolled_back\",\"trigger\":\"auto\",\"detail\":\"service_inactive\"}" >> "$HISTORY_FILE"
    fi
    fail "Post-deploy: ws-collector not active"
fi

# Quick log check — last 10 lines, look for ERROR
if tail -10 /home/quant/logs/ws-collector.log 2>/dev/null | grep -qi "ERROR"; then
    log "WARNING: ws-collector logs contain ERROR lines — manual check advised"
fi

# ── ⑦ Record deployment ─────────────────────────────────────────────
log "Recording deployment result..."
echo "{\"time\":\"$(date -Iseconds)\",\"commit\":\"$NEW_COMMIT\",\"status\":\"success\",\"trigger\":\"github\"}" >> "$HISTORY_FILE"
log "Deployment complete — commit $NEW_COMMIT"
