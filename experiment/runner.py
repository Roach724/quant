"""
Experiment Runner — 实验运行器

一键运行完整实验流水线，或为已有的 Engine 运行结果生成投资记录。

使用方式:
    runner = ExperimentRunner()
    meta = runner.run_full_experiment(
        "exp_001", "Baseline",
        hypothesis="Buy & hold generates positive returns"
    )

    # 从已有的 Engine 结果创建
    result = Engine(config).run(strategy, data)
    meta = runner.run_from_engine_result(
        "exp_002", "Test", "hypothesis", engine_result=result
    )
"""
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from experiment.investment_record import InvestmentRecord
from experiment.tracker import ExperimentTracker

logger = logging.getLogger(__name__)


class ExperimentRunner:
    """实验运行器 — 编排完整实验流水线"""

    def __init__(
        self,
        experiments_dir: str = "./data/experiments",
        investments_dir: str = "./data/investments",
    ):
        self.tracker = ExperimentTracker(base_dir=experiments_dir)
        self.investments_dir = Path(investments_dir)
        self.investments_dir.mkdir(parents=True, exist_ok=True)

    # ── 完整实验流水线 ──

    def run_full_experiment(
        self,
        exp_id: str,
        name: str,
        hypothesis: str,
        changes: Optional[list[str]] = None,
        skip_training: bool = False,
        skip_backtest: bool = False,
    ) -> dict:
        """运行完整实验流水线。

        Args:
            exp_id: 实验 ID (如 exp_001)
            name: 实验名称
            hypothesis: 实验假设
            changes: 变更列表
            skip_training: 是否跳过训练阶段
            skip_backtest: 是否跳过回测阶段

        Returns:
            实验结果摘要 dict
        """
        logger.info("=" * 60)
        logger.info(f"Experiment Pipeline: {exp_id} — {name}")
        logger.info("=" * 60)

        t0 = time.time()
        results: dict[str, Any] = {
            "experiment_id": exp_id,
            "name": name,
            "hypothesis": hypothesis,
        }

        # 1. 注册实验
        self.tracker.register_experiment(
            exp_id=exp_id,
            name=name,
            hypothesis=hypothesis,
            changes=changes or [],
        )

        # 2. (预留) 训练模型
        if not skip_training:
            logger.info("\n--- Phase 1: Model Training ---")
            logger.info("Training module not yet implemented — skipping")
        else:
            logger.info("\n--- Phase 1: Training skipped ---")

        # 3. (预留) 回测
        if not skip_backtest:
            logger.info("\n--- Phase 2: Backtest ---")
            logger.info("Backtest module not yet implemented — skipping")
        else:
            logger.info("\n--- Phase 2: Backtest skipped ---")

        # 4. 汇总 & 更新实验结果
        duration = time.time() - t0
        results["duration_seconds"] = duration

        self.tracker.update_results(
            exp_id=exp_id,
            results={
                "duration_seconds": duration,
            },
            verdict="completed",
            status="completed",
        )

        logger.info("\n" + "=" * 60)
        logger.info(f"Experiment {exp_id} completed in {duration:.1f}s")
        logger.info("=" * 60)

        return results

    # ── 从 Engine 结果创建投资记录 ──

    def run_from_engine_result(
        self,
        exp_id: str,
        name: str,
        hypothesis: str,
        engine_result: Any,
        changes: Optional[list[str]] = None,
    ) -> dict:
        """从已有的 Engine.run() 结果创建实验和投资记录。

        自动提取: equity curve、performance metrics、trades 和 holdings,
        保存为 InvestmentRecord。

        Args:
            exp_id: 实验 ID
            name: 实验名称
            hypothesis: 实验假设
            engine_result: Engine.run() 返回的 Result 对象
            changes: 变更列表

        Returns:
            实验元数据 dict
        """
        # 1. 注册实验
        self.tracker.register_experiment(
            exp_id=exp_id,
            name=name,
            hypothesis=hypothesis,
            changes=changes or [],
        )

        # 2. 创建 InvestmentRecord
        record = InvestmentRecord(
            strategy_name=engine_result.strategy_name,
        )

        # 3. 设置配置快照
        config = engine_result.config
        if hasattr(config, "__dataclass_fields__"):
            record.set_config(asdict(config))
        elif isinstance(config, dict):
            record.set_config(config)
        else:
            record.set_config({"initial_capital": getattr(config, "initial_capital", 0)})

        # 4. 提取 equity curve 并记录每日权益
        portfolio = engine_result.portfolio
        equity_series = portfolio.equity_curve

        for ts, eq in equity_series.items():
            record.record_equity(ts, eq)

        # 5. 提取持仓快照
        for sym, pos in portfolio.positions.items():
            record.record_position(
                date=equity_series.index[-1] if len(equity_series) > 0 else datetime.now(),
                symbol=sym,
                shares=pos.size,
                price=pos.avg_entry,
            )

        # 6. 生成 session_id
        now = datetime.now(timezone.utc)
        session_id = f'{now.strftime("%Y%m%d")}_{exp_id}_backtest'

        # 7. 保存投资记录
        inv_dir = self.investments_dir / session_id
        record.save(str(inv_dir))

        # 8. 记录到实验索引
        self.tracker.record_session(
            exp_id=exp_id,
            session_id=session_id,
            session_type="backtest",
            path=str(inv_dir),
            date=now.strftime("%Y-%m-%d"),
        )

        # 9. 计算绩效指标
        perf = record._compute_performance()

        # 通过 engine.metrics 计算更精确的指标
        from engine.metrics import summary as engine_summary

        engine_perf = engine_summary(engine_result)
        # Merge: engine metrics take precedence
        perf.update(engine_perf)

        # 10. 更新实验结果
        self.tracker.update_results(
            exp_id=exp_id,
            results=perf,
            verdict="completed",
            status="completed",
        )

        # 11. 获取完整实验元数据并返回
        meta = self.tracker.get_experiment(exp_id) or {}
        meta["session_id"] = session_id
        return meta

    # ── 工具方法 ──

    def get_summary(self, exp_id: str) -> str:
        """获取实验摘要"""
        return self.tracker.get_experiment(exp_id) or f"Experiment {exp_id} not found"

    def compare(self, exp_id_a: str, exp_id_b: str) -> str:
        """对比两个实验"""
        return self.tracker.compare(exp_id_a, exp_id_b)
