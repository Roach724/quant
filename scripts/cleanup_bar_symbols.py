"""BQ symbol normalization + dedup for bars tables.

Target: all bars tables have consistent {MARKET}.{NORMALIZED} symbol format.
US: US.AAPL (strip prefix only)
HK: HK.00005 (strip prefix + zero-pad to 5 digits)
"""
import sys
from google.cloud import bigquery

PROJECT = "deductive-notch-495015-c2"
DATASET = "quant"
TABLES = ["hk_bars_1d", "hk_bars_5m", "us_bars_1d", "us_bars_5m"]

client = bigquery.Client(project=PROJECT)


def clean_table(table_name: str) -> None:
    market = "hk" if table_name.startswith("hk") else "us"
    prefix = f"{market.upper()}."
    full = f"{PROJECT}.{DATASET}.{table_name}"
    tmp = f"{full}_clean_tmp"

    # Build normalization expression
    if market == "hk":
        norm_expr = f"CONCAT('{prefix}', LPAD(REGEXP_REPLACE(REPLACE(symbol, '{prefix}', ''), r'^0+', ''), 5, '0'))"
    else:
        norm_expr = f"CONCAT('{prefix}', REPLACE(symbol, '{prefix}', ''))"

    # Get columns (self-describing)
    schema = client.query(f"SELECT * FROM `{full}` LIMIT 1").to_dataframe()
    cols = list(schema.columns)
    if "symbol" not in cols:
        print(f"  SKIP {table_name}: no symbol column")
        return

    # Build column list for SELECT (replace symbol with normalized)
    sel_cols = []
    for c in cols:
        if c == "symbol":
            sel_cols.append(f"{norm_expr} AS symbol")
        else:
            sel_cols.append(c)

    col_list = ",\n        ".join(sel_cols)

    # Count before
    before = list(client.query(f"SELECT COUNT(*) AS n FROM `{full}`").result())[0].n

    # Dedup: ROW_NUMBER over (normalized_symbol, timestamp) 
    # Prefer latest _ingest_time if available, otherwise latest close
    norm_symbol = norm_expr.replace(" AS symbol", "")
    if "_ingest_time" in cols:
        order_col = "_ingest_time"
    else:
        order_col = "close"

    dedup_sql = f"""
        CREATE OR REPLACE TABLE `{tmp}` AS
        SELECT {col_list}
        FROM (
            SELECT {col_list}, 
                   ROW_NUMBER() OVER (
                       PARTITION BY {norm_symbol}, timestamp 
                       ORDER BY {order_col} DESC
                   ) AS _rn
            FROM `{full}`
        )
        WHERE _rn = 1
    """
    
    print(f"\n{'='*60}")
    print(f"Cleaning {table_name} ({market}) — before: {before:,} rows")
    print(f"  norm: {norm_expr}")
    
    client.query(dedup_sql).result()
    after = list(client.query(f"SELECT COUNT(*) AS n FROM `{tmp}`").result())[0].n
    
    # Check duplicates removed
    dup_check = client.query(f"""
        SELECT COUNT(*) AS dup_pairs FROM (
            SELECT symbol, timestamp, COUNT(*) AS cnt
            FROM `{tmp}` GROUP BY symbol, timestamp HAVING cnt > 1
        )
    """).result()
    dups = list(dup_check)[0].n if dup_check else 0

    # Swap: drop original, rename tmp
    client.query(f"DROP TABLE `{full}`").result()
    client.query(f"ALTER TABLE `{tmp}` RENAME TO `{table_name}`").result()
    
    print(f"  after: {after:,} rows, removed: {before - after:,}, duplicates: {dups}")

    # Verify symbol format
    if market == "hk":
        bad = client.query(f"""
            SELECT COUNT(*) AS n FROM `{full}`
            WHERE NOT REGEXP_CONTAINS(symbol, r'^{prefix}[0-9]{{5}}$')
        """).result()
        bad_count = list(bad)[0].n
        if bad_count:
            print(f"  WARNING: {bad_count} rows have non-standard HK symbol format!")
        else:
            print(f"  ✅ All HK symbols match {prefix}XXXXX format")
    else:
        bad = client.query(f"""
            SELECT COUNT(*) AS n FROM `{full}`
            WHERE NOT STARTS_WITH(symbol, '{prefix}')
        """).result()
        bad_count = list(bad)[0].n
        if bad_count:
            print(f"  WARNING: {bad_count} rows without {prefix} prefix!")
        else:
            print(f"  ✅ All US symbols have {prefix} prefix")


if __name__ == "__main__":
    for t in TABLES:
        try:
            clean_table(t)
        except Exception as e:
            print(f"  ERROR cleaning {t}: {e}", file=sys.stderr)
    print("\n✅ Done")
