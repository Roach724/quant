import os
import requests
import pandas as pd


def _get_id_token() -> str | None:
    """Get GCP identity token for Cloud Run auth, if available."""
    try:
        import google.auth.transport.requests
        import google.auth

        creds, _ = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception:
        pass

    try:
        token = os.environ.get("GCLOUD_ID_TOKEN", "").strip()
        if token:
            return token
    except Exception:
        pass

    return None


class QuantClient:
    def __init__(self, base_url: str = "http://localhost:8080", auth_token: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token

    @property
    def auth_token(self):
        if self._auth_token is None:
            self._auth_token = _get_id_token()
        return self._auth_token

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

        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        resp = requests.get(
            f"{self._base_url}/api/v1/bars",
            params={
                "market": market,
                "symbols": symbols,
                "start": start,
                "end": end,
                "frequency": frequency,
            },
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("bars"):
            return pd.DataFrame()

        df = pd.DataFrame(data["bars"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.set_index(["symbol", "timestamp"]).sort_index()
