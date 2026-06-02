"""Tests for FutuCollector base class."""
import pytest
import pandas as pd
from collectors.futu_collector_base import FutuCollector

class TestFutuCollectorSmoke:
    def test_instantiation(self):
        c = FutuCollector(market="us")
        assert c.market == "us"
    
    def test_rate_limit_window(self):
        c = FutuCollector(market="us", rate_limit_per_min=10)
        for _ in range(5):
            c._rate_limit()
        assert c._request_count == 5
    
    def test_collect_one_not_implemented(self):
        c = FutuCollector(market="us")
        with pytest.raises(NotImplementedError):
            c.collect_one("US.AAPL")
