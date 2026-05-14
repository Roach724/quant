import pandas as pd


def bars_direct(
    symbols: str | list[str],
    start: str,
    end: str,
    market: str = "us",
    base_path: str | None = None,
) -> pd.DataFrame:
    if isinstance(symbols, str):
        symbols = [symbols]

    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    frames = []
    for symbol in symbols:
        date_range = pd.date_range(start_dt, end_dt, freq="D")
        for d in date_range:
            path = (
                f"{base_path or ''}/raw/{market}/bars/"
                f"{d.year:04d}/{d.month:02d}/{d.day:02d}/{symbol}.parquet"
            )
            try:
                df = pd.read_parquet(path)
                frames.append(df)
            except FileNotFoundError:
                continue

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    return result.set_index(["symbol", "timestamp"]).sort_index()
