from collectors.adapters.futu_short_interest_adapter import FutuShortInterestAdapter


def test_data_type():
    assert FutuShortInterestAdapter.DATA_TYPE == "short_interest"


def test_init():
    adapter = FutuShortInterestAdapter(host="127.0.0.1", port=11111)
    assert adapter.host == "127.0.0.1"
