from collectors.adapters.futu_capital_flow_adapter import FutuCapitalFlowAdapter


def test_data_type():
    assert FutuCapitalFlowAdapter.DATA_TYPE == "capital_flow"


def test_init():
    adapter = FutuCapitalFlowAdapter(host="127.0.0.1", port=11111)
    assert adapter.host == "127.0.0.1"
