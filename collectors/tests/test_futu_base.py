from unittest.mock import patch
import pandas as pd
from collectors.adapters._futu_base import FutuBaseAdapter


class DummyAdapter(FutuBaseAdapter):
    DATA_TYPE = "dummy"
    
    def _call_api(self, symbol: str):
        return {"value": 1.0}
    
    def _parse(self, symbol: str, raw) -> pd.DataFrame:
        return pd.DataFrame([{"symbol": symbol, "value": raw.get("value", 0)}])


def test_base_connects_with_env_vars():
    adapter = DummyAdapter(host="127.0.0.1", port=11111)
    assert adapter.host == "127.0.0.1"
    assert adapter.port == 11111


def test_base_default_symbols():
    adapter = DummyAdapter()
    symbols = adapter._default_symbols()
    assert isinstance(symbols, list)
    assert len(symbols) > 0


def test_base_rate_limit_does_not_raise():
    adapter = DummyAdapter()
    adapter._rate_limit()


def test_base_market_from_code():
    adapter = DummyAdapter()
    assert adapter._market_from_code("HK.00700") == "hk"
    assert adapter._market_from_code("US.AAPL") == "us"


def test_dict_to_dataframe():
    d = {"highest": 400.0, "average": 323.63, "lowest": 253.0, "rating": 4}
    df = FutuBaseAdapter._dict_to_dataframe(d)
    assert len(df) == 1
    assert df.iloc[0]["highest"] == 400.0


def test_fetch_all_returns_dict():
    adapter = DummyAdapter(symbols=["HK.00700", "US.AAPL"])
    result = adapter.fetch_all()
    assert isinstance(result, dict)
    assert len(result) == 2
