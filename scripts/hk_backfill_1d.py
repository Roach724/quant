"""Quick HK 1d backfill via yfinance — skips failures, writes to BQ."""
import sys, yaml, logging
import pandas as pd
import yfinance as yf
from google.cloud import bigquery

sys.path.insert(0, "/opt/quant-dev")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hk_backfill")

cfg = yaml.safe_load(open("config/symbols.yaml"))
syms = cfg["markets"]["hk"]["symbols"]
syms = [s.replace("HK.", "").lstrip("0") or "0" for s in syms]
yf_syms = [f"{s}.HK" for s in syms]
log.info(f"Loading {len(syms)} HK symbols")

client = bigquery.Client(project="deductive-notch-495015-c2")
total_rows = 0
failed = 0

for i, (code, ticker) in enumerate(zip(syms, yf_syms)):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(start="2020-01-01", end="2026-05-04", interval="1d")
        if df.empty:
            log.warning(f"[{i+1}/{len(syms)}] {ticker}: no data")
            failed += 1
            continue
        
        df = df.reset_index()
        df = df.rename(columns={"Date": "timestamp", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        df["symbol"] = f"HK.{code.zfill(5)}"
        df["market"] = "hk"
        df["frequency"] = "1d"
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("UTC")
        df = df[["symbol", "timestamp", "open", "high", "low", "close", "volume", "market", "frequency"]]
        df = df.dropna(subset=["open", "close"])
        
        from common.bq_writer import write_rows_to_bq
        n = write_rows_to_bq(df, table_name="hk_bars_1d")
        total_rows += n
        
        if (i+1) % 25 == 0:
            log.info(f"[{i+1}/{len(syms)}] {ticker}: {len(df)} rows, total={total_rows:,}")
    except Exception as e:
        log.warning(f"[{i+1}/{len(syms)}] {ticker}: FAIL {e}")
        failed += 1

log.info(f"DONE: {total_rows:,} rows, {len(syms)-failed}/{len(syms)} ok, {failed} failed")
