#!/bin/bash
set -euo pipefail

# rollback.sh — Manually rollback /opt/quant-prod to a previous commit
# Usage: ./scripts/rollback.sh                → roll back to last successful commit
#        ./scripts/rollback.sh <commit-hash>   → roll back to specific commit

PROD_ROOT="/opt/quant-prod"
HISTORY_FILE="$PROD_ROOT/.deploy_history"
VENV_PYTHON="$PROD_ROOT/.venv/bin/python3.12"
VENV_PIP="$PROD_ROOT/.venv/bin/pip"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[rollback]${NC} $*"; }
warn() { echo -e "${YELLOW}[rollback]${NC} $*"; }
err() { echo -e "${RED}[rollback]${NC} $*"; }

# Determine target commit
TARGET=""
if [ $# -ge 1 ]; then
    TARGET="$1"
    log "Rollback target specified: $TARGET"
else
    if [ ! -f "$HISTORY_FILE" ]; then
        err "No deploy history found and no commit specified."
        echo "Usage: $0 [commit-hash]"
        exit 1
    fi
    TARGET=$(tail -5 "$HISTORY_FILE" | grep '"status":"success"' | tail -1 | python3 -c "import sys,json; print(json.loads(sys.stdin.readline())['commit'])" 2>/dev/null)
    if [ -z "$TARGET" ]; then
        err "No successful commit found in deploy history."
        echo "Usage: $0 [commit-hash]"
        exit 1
    fi
    log "Using last successful commit: $TARGET"
fi

# Verify target exists
cd "$PROD_ROOT"
if ! git cat-file -e "$TARGET" 2>/dev/null; then
    git fetch origin stable
    if ! git cat-file -e "$TARGET" 2>/dev/null; then
        err "Commit $TARGET not found."
        exit 1
    fi
fi

# Show what we're rolling back from
CURRENT=$(git rev-parse --short HEAD)
TARGET_SHORT=$(git rev-parse --short "$TARGET")
log "Current: $CURRENT → Target: $TARGET_SHORT"

# Perform rollback
git checkout "$TARGET" || { err "git checkout failed"; exit 1; }
"$VENV_PIP" install -r requirements.txt --quiet || warn "pip install had warnings"
sudo systemctl restart ws-collector || { err "systemctl restart failed"; exit 1; }
sleep 3

# Verify
STATUS=$(sudo systemctl is-active ws-collector 2>/dev/null || echo "inactive")
if [ "$STATUS" != "active" ]; then
    err "Service not active after rollback! Status: $STATUS"
    exit 1
fi

echo "{\"time\":\"$(date -Iseconds)\",\"commit\":\"$TARGET\",\"status\":\"rolled_back\",\"trigger\":\"manual\"}" >> "$HISTORY_FILE"
log "Rollback complete — now on commit $TARGET_SHORT ($TARGET)"
log "Service ws-collector: $STATUS"
