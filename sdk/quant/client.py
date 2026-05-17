import os
import requests
import pandas as pd


def _get_id_token(audience: str) -> str | None:
    """Get GCP identity token for Cloud Run IAM auth.

    Tries: gcloud CLI (works for user+SA accounts), then google-auth
    (works for service accounts), then env var.
    """
    # gcloud CLI — works for both user accounts and service accounts
    for gcloud_name in ("gcloud", "gcloud.cmd"):
        try:
            import subprocess
            result = subprocess.run(
                [gcloud_name, "auth", "print-identity-token"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            continue

    # google-auth — works for service accounts with ADC
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token

        request = google.auth.transport.requests.Request()
        token = id_token.fetch_id_token(request, audience)
        return token
    except Exception:
        pass

    # explicit env var
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
            self._auth_token = _get_id_token(self._base_url)
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

        # Ensure RFC3339 format for Go API (append T00:00:00Z if date-only)
        if "T" not in start:
            start = f"{start}T00:00:00Z"
        if "T" not in end:
            end = f"{end}T00:00:00Z"

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
