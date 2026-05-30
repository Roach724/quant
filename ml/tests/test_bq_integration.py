"""Integration tests for BQ-based factor loading."""
import pytest


def test_load_from_bq_returns_feature_matrix():
    """load_from_bq should load factor data from BigQuery for given symbols."""
    from ml.trainer import ModelTrainer
    trainer = ModelTrainer(factor_path=None)
    
    # Use a small date range and few symbols to keep test fast
    df = trainer.load_from_bq(
        symbols=["AAPL", "MSFT"],
        start="2026-01-01",
        end="2026-03-31",
        market="us",
    )
    
    assert df is not None
    assert len(df) > 0
    assert len(trainer.feature_cols) > 0
    # Should have loaded some factors
    assert "symbol" in df.columns
    assert "date" in df.columns
