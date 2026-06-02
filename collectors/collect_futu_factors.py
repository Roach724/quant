"""Unified entry point for collecting all new Futu factor data.

Usage:
    python -m collectors.collect_futu_factors --table us_capital_distribution
    python -m collectors.collect_futu_factors --table us_insider_trade
    python -m collectors.collect_futu_factors --all --market us
"""

import argparse
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

COLLECTOR_MAP = {
    "us_capital_distribution": (
        "capital_distribution_collector",
        "CapitalDistributionCollector",
        "us",
    ),  # noqa: E501
    "hk_capital_distribution": (
        "capital_distribution_collector",
        "CapitalDistributionCollector",
        "hk",
    ),  # noqa: E501
    "us_insider_trade": ("insider_collector", "InsiderTradeCollector", "us"),
    "us_insider_holder": ("insider_collector", "InsiderHolderCollector", "us"),
    "us_daily_short_volume": ("daily_short_collector", "DailyShortCollector", "us"),
    "hk_daily_short_volume": ("daily_short_collector", "DailyShortCollector", "hk"),
    "us_earnings_price_move": ("earnings_collector", "EarningsPriceMoveCollector", "us"),
    "hk_earnings_price_move": ("earnings_collector", "EarningsPriceMoveCollector", "hk"),
    "us_earnings_price_history": ("earnings_collector", "EarningsPriceHistoryCollector", "us"),
    "hk_earnings_price_history": ("earnings_collector", "EarningsPriceHistoryCollector", "hk"),
    "us_owner_plate": ("static_data_collector", "OwnerPlateCollector", "us"),
    "hk_owner_plate": ("static_data_collector", "OwnerPlateCollector", "hk"),
    "us_rehab": ("static_data_collector", "RehabCollector", "us"),
    "hk_rehab": ("static_data_collector", "RehabCollector", "hk"),
    "hk_top_ten_brokers": ("static_data_collector", "TopTenBrokersCollector", "hk"),
}


def collect_table(table_name: str):  # type: ignore[no-untyped-def]
    """Collect data for a single table and save to GCS."""
    if table_name not in COLLECTOR_MAP:
        raise ValueError(f"Unknown table: {table_name}. Valid: {list(COLLECTOR_MAP.keys())}")

    module_name, cls_name, market = COLLECTOR_MAP[table_name]
    import importlib

    mod = importlib.import_module(f"collectors.{module_name}")
    collector_cls = getattr(mod, cls_name)

    # Some collectors hardcode market in __init__
    if cls_name == "TopTenBrokersCollector":
        collector = collector_cls()
    else:
        collector = collector_cls(market=market)

    logger.info(f"Collecting {table_name} ({market}) via {cls_name}...")
    df = collector.collect_all()
    logger.info(f"Collected {len(df)} rows for {table_name}")

    if not df.empty:
        source = table_name.split("_", 1)[1]
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        collector.save_to_gcs(df, source, date_str)

    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Collect Futu factor data")
    parser.add_argument(
        "--table", choices=list(COLLECTOR_MAP.keys()), help="Single table to collect"
    )
    parser.add_argument("--all", action="store_true", help="Collect all tables")
    parser.add_argument("--market", choices=["us", "hk"], help="Filter --all by market")
    args = parser.parse_args()

    if not args.table and not args.all:
        parser.error("Must specify --table or --all")

    if args.all:
        tables = [t for t in COLLECTOR_MAP if not args.market or t.startswith(args.market)]
    else:
        tables = [args.table]

    success = 0
    for t in tables:
        try:
            collect_table(t)
            success += 1
        except Exception as e:
            logger.error(f"FAILED {t}: {e}", exc_info=True)

    logger.info(f"Done: {success}/{len(tables)} tables collected")
