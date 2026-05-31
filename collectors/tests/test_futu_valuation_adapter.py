from collectors.adapters.futu_valuation_adapter import FutuValuationAdapter


def test_data_type():
    assert FutuValuationAdapter.DATA_TYPE == "valuation"


def test_symbols_default():
    adapter = FutuValuationAdapter()
    assert len(adapter.symbols) > 0
