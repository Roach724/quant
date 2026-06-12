"""Tests for MLPrediction."""
import pytest
import pandas as pd
import numpy as np
from engine.strategy import StrategyContext, Signal
from engine.data import DataFrameSource
from engine.portfolio import Portfolio


def test_ml_prediction_initializes():
    """Strategy should initialize without errors."""
    from strategies.MLPrediction import MLPrediction
    strategy = MLPrediction(market="us", top_k=5)
    assert strategy.market == "us"
    assert strategy.top_k == 5
    assert strategy.model_type == "lightgbm"
    # Parameters should be discoverable via Strategy.parameters()
    params = strategy.parameters()
    assert "market" in params
    assert "top_k" in params


def test_ml_prediction_handles_empty_context():
    """Strategy should handle empty universe gracefully (training will fail gracefully)."""
    from strategies.MLPrediction import MLPrediction
    
    strategy = MLPrediction(market="us")
    dates = pd.date_range("2026-01-01", periods=10, freq="B")
    close = pd.DataFrame({"AAPL": [100.0]*10}, index=dates)
    open_df = pd.DataFrame({"AAPL": [99.0]*10}, index=dates)
    high = pd.DataFrame({"AAPL": [101.0]*10}, index=dates)
    low = pd.DataFrame({"AAPL": [98.0]*10}, index=dates)
    volume = pd.DataFrame({"AAPL": [1000]*10}, index=dates)
    ds = DataFrameSource(close=close, open=open_df, high=high, low=low, volume=volume)
    ctx = StrategyContext(data=ds, portfolio=Portfolio(100000), config={})
    
    # on_bar should not crash even without training
    signals = strategy.on_bar(ctx, bar=5)
    assert isinstance(signals, list)
    assert len(signals) == 0  # not trained, should return empty
