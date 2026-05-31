from collectors.adapters.futu_analyst_adapter import FutuAnalystAdapter


def test_data_type():
    assert FutuAnalystAdapter.DATA_TYPE == "analyst"


def test_parse_flat_dict():
    adapter = FutuAnalystAdapter(symbols=[])
    raw = {"highest": 400.0, "average": 323.63, "lowest": 253.0, "rating": 4}
    df = adapter._parse("US.AAPL", raw)
    assert len(df) == 1
    assert df.iloc[0]["highest"] == 400.0
