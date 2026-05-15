from engine.report import generate
from engine.engine import Result
from engine.portfolio import Portfolio
from engine.config import BacktestConfig
import tempfile, os, pandas as pd


def test_generate_creates_html_file():
    eq = [100_000 + i * 100 for i in range(50)]
    pf = Portfolio(eq[0])
    pf._equity = eq
    pf._timestamps = list(pd.date_range("2026-01-01", periods=len(eq), freq="D"))
    r = Result(portfolio=pf, config=BacktestConfig(), strategy_name="TestStrat")
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        path = f.name
    try:
        generate(r, path)
        assert os.path.exists(path)
        with open(path) as f:
            html = f.read()
        assert "<html" in html.lower()
        assert "TestStrat" in html
    finally:
        os.unlink(path)
