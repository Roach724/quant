from unittest.mock import patch, Mock
import pandas as pd
import pytest
from quant.client import QuantClient


def test_client_bars_returns_dataframe():
    mock_resp = {
        "bars": [
            {"symbol": "AAPL", "timestamp": "2026-05-13T10:00:00Z",
             "open": 189.5, "high": 190.2, "low": 189.3, "close": 189.8,
             "volume": 1000000, "market": "us", "frequency": "1m"},
        ],
        "status": "ok",
    }

    with patch("quant.client.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_resp
        mock_get.return_value.raise_for_status = Mock()

        client = QuantClient(base_url="http://test:8080")
        df = client.bars("AAPL", "2026-05-01", "2026-05-13", market="us")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.index.get_level_values("symbol")[0] == "AAPL"
    assert df.iloc[0]["close"] == 189.8


def test_client_raises_on_bad_response():
    with patch("quant.client.requests.get") as mock_get:
        mock_get.return_value.raise_for_status.side_effect = Exception("500")

        client = QuantClient(base_url="http://test:8080")
        with pytest.raises(Exception):
            client.bars("AAPL", "2026-05-01", "2026-05-13")
