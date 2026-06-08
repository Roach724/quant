#!/usr/bin/env python3
"""Return Futu subscription quota as JSON (called by admin server)."""
import json, sys, os, logging, io

# Suppress ALL log output to stdout BEFORE importing futu
logging.basicConfig(level=logging.CRITICAL, stream=io.StringIO())
for name in logging.root.manager.loggerDict:
    logging.getLogger(name).setLevel(logging.CRITICAL)

from futu import OpenQuoteContext

# Redirect futu's stdout noise
ctx = OpenQuoteContext('127.0.0.1', 11111)
try:
    r1, d1 = ctx.query_subscription()
    r2, d2 = ctx.get_history_kl_quota()
    result = {
        "rt": {"used": d1.get("total_used", 0), "remain": d1.get("remain", 0)} if isinstance(d1, dict) else None,
        "hist": {"remain": int(d2[0]), "today_used": int(d2[1])} if isinstance(d2, tuple) and len(d2) >= 2 else None,
    }
finally:
    ctx.close()

# Only print JSON to stdout
sys.stdout.write(json.dumps(result) + '\n')
