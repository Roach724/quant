#!/bin/bash
# scripts/cron/us_index_5m.sh
cd /opt/quant-dev && PYTHONPATH=/opt/quant-dev .venv/bin/python3 collectors/index_collector_us.py
