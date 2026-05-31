from collectors.adapters.futu_shareholder_adapter import FutuShareholderAdapter


def test_data_type():
    assert FutuShareholderAdapter.DATA_TYPE == "shareholder"


def test_init():
    adapter = FutuShareholderAdapter(host="127.0.0.1", port=11111)
    assert adapter.host == "127.0.0.1"
