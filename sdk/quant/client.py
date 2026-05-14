import requests
import pandas as pd


class QuantClient:
    def __init__(self, base_url: str = "http://localhost:8080"):
        self._base_url = base_url.rstrip("/")

    def bars(
        self,
        symbols: str | list[str],
        start: str,
        end: str,
        market: str = "us",
        frequency: str = "1m",
    ) -> pd.DataFrame:
        if isinstance(symbols, list):
            symbols = ",".join(symbols)

        resp = requests.get(
            f"{self._base_url}/api/v1/bars",
            params={
                "market": market,
                "symbols": symbols,
                "start": start,
                "end": end,
                "frequency": frequency,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("bars"):
            return pd.DataFrame()

        df = pd.DataFrame(data["bars"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.set_index(["symbol", "timestamp"]).sort_index()
