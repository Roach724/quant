from collectors.adapters.futu_financials_adapter import FutuFinancialsAdapter


def test_data_type():
    assert FutuFinancialsAdapter.DATA_TYPE == "financials"


def test_init_defaults():
    adapter = FutuFinancialsAdapter(host="127.0.0.1", port=11111)
    assert adapter.financial_type == 10
    assert adapter.host == "127.0.0.1"
