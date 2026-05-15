import pytest


@pytest.fixture
def default_config():
    from engine.config import BacktestConfig
    return BacktestConfig()
