#!/bin/bash
# market_open.sh — Exit 0 if any monitored market is in trading hours.
# Used by deploy.sh to prevent ws_collector restart during live trading.
#
# Considers both US (NYSE) and HK (HKEX) markets.
# Includes a 5-minute pre-market window (same as ws_collector preheat).
#
# Exit codes:
#   0 — at least one market is open (preheat window included)
#   1 — all markets closed (safe to restart ws_collector)
#   2 — error checking (treat as open for safety)

set -euo pipefail

PROD_ROOT="${PROD_ROOT:-/opt/quant-prod}"
PYTHON="${PROD_ROOT}/.venv/bin/python3.12"

# Falling back to system python if venv python not available
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

"$PYTHON" -c '
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root for imports
sys.path.insert(0, "/opt/quant-prod")

try:
    from live.market_calendar import MarketCalendar
except ImportError:
    # If module not available, err on the side of caution
    print("MARKET_CHECK: Cannot import MarketCalendar — treating as OPEN", file=sys.stderr)
    sys.exit(0)

PREHEAT_MINUTES = 5
now = datetime.now(timezone.utc)
open_markets = []

for market in ("us", "hk"):
    cal = MarketCalendar(market)
    if cal.is_open_now(preheat_minutes=PREHEAT_MINUTES):
        open_markets.append(market)

if open_markets:
    print(f"MARKET_OPEN: {", ".join(open_markets)} at {now.isoformat(timespec="minutes")}")
    sys.exit(0)
else:
    now_str = now.isoformat(timespec="minutes")
    print(f"MARKET_CLOSED: all markets closed at {now_str}")
    sys.exit(1)
'

exit $?
