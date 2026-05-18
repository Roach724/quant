"""实验管理器 — ExperimentTracker

管理实验生命周期，与投资记录系统联动。

使用方式:
    tracker = ExperimentTracker()
    tracker.register_experiment("exp_001", "Baseline",
                                 hypothesis="43个量价因子baseline",
                                 changes=["initial setup"])
    tracker.update_results("exp_001", {"sharpe": 1.5}, verdict="improved")
    tracker.record_session("exp_001", "20260518_paper_001",
                            "paper_trading", "data/investments/...")
    exp = tracker.get_experiment("exp_001")
    report = tracker.compare("exp_001", "exp_002")
"""

from .tracker import ExperimentTracker
from .runner import ExperimentRunner

__all__ = ["ExperimentTracker", "ExperimentRunner"]
