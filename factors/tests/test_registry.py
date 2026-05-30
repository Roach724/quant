import pytest
from unittest.mock import patch
import pandas as pd
from factors.registry import FactorRegistry


@pytest.fixture
def registry():
    return FactorRegistry(project="test-project")


def test_register_inserts_row(registry):
    with patch.object(registry, '_client') as mock_client:
        mock_client.insert_rows_json.return_value = []
        result = registry.register(
            factor_id="us_momentum_20d", name="20-Day Momentum",
            market="us", source="Alpha158",
            formula="factors/builder.py::momentum_20d",
            category="momentum", tags=["trend"],
        )
        assert result is True


def test_get_active_returns_dataframe(registry):
    mock_df = pd.DataFrame({
        "factor_id": ["us_momentum_20d", "us_vol_10d"],
        "is_active": [True, True],
        "latest_ic_mean": [0.06, 0.04],
    })
    with patch.object(registry._client, 'query') as mock_query:
        mock_query.return_value.to_dataframe.return_value = mock_df
        result = registry.get_active("us")
        assert len(result) == 2

def test_evaluate_writes_to_bq(registry):
    import numpy as np
    n = 200
    fv = pd.Series(np.random.randn(n))
    f1 = pd.Series(np.random.randn(n))
    f5 = pd.Series(np.random.randn(n))
    f20 = pd.Series(np.random.randn(n))
    with patch.object(registry._client, 'query') as mq, \
         patch.object(registry._client, 'insert_rows_json') as mi:
        mq.return_value.result.return_value = None
        mi.return_value = []
        result = registry.evaluate("us_momentum_20d", fv, f1, f5, f20, force=True)
        assert result is not None
        assert "ic_mean" in result
