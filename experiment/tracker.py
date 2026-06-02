"""
Experiment Tracker — 实验追踪器

管理实验生命周期，与投资记录系统联动。

保存结构:
    data/experiments/
    ├── INDEX.md                     ← 实验总表（Markdown，人类可读）
    ├── exp_001_baseline/
    │   ├── experiment.json          ← ExperimentMeta as JSON
    │   └── investment_sessions.json ← 投资会话索引
    └── exp_002_new_factors/
        ├── experiment.json
        └── investment_sessions.json

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
import json
import logging
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Git working directory
GIT_WORK_DIR = Path("/opt/quant")


@dataclass
class ExperimentMeta:
    """实验元数据结构"""
    experiment_id: str
    name: str
    created_at: str
    git_branch: str
    git_commit: str
    hypothesis: str
    changes: list[str] = field(default_factory=list)
    status: str = "running"  # running | completed | failed | aborted
    results: dict = field(default_factory=dict)
    verdict: str = ""  # baseline | improved | degraded | inconclusive


def _get_git_info() -> tuple[str, str]:
    """获取当前 git 分支和 commit"""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=GIT_WORK_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        branch = "unknown"

    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=GIT_WORK_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        commit = "unknown"

    return branch, commit


class ExperimentTracker:
    """实验追踪器"""

    def __init__(self, base_dir: str = "/opt/quant-dev/data/experiments"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_index()

    def _exp_dir(self, exp_id: str) -> Path:
        """返回实验目录，自动补全前缀"""
        # 查找已有的匹配目录
        for p in sorted(self.base_dir.iterdir()):
            if p.is_dir() and p.name.startswith(exp_id):
                return p
        # 否则按 exp_{id} 格式创建
        return self.base_dir / exp_id

    def _ensure_index(self):
        """确保 INDEX.md 存在"""
        index_path = self.base_dir / "INDEX.md"
        if not index_path.exists():
            index_path.write_text(
                "# Experiment Index\n\n"
                "| Experiment | Name | Branch | Status | Verdict |\n"
                "|------------|------|--------|--------|---------|\n",
                encoding="utf-8",
            )

    def register_experiment(
        self,
        exp_id: str,
        name: str,
        hypothesis: str = "",
        changes: Optional[list[str]] = None,
    ) -> Path:
        """注册新实验

        Args:
            exp_id: 实验 ID (如 exp_001)
            name: 实验名称
            hypothesis: 实验假设
            changes: 变更列表

        Returns:
            实验目录路径
        """
        exp_dir = self._exp_dir(exp_id)
        exp_dir.mkdir(parents=True, exist_ok=True)

        # 自动获取 git 信息
        git_branch, git_commit = _get_git_info()

        meta = ExperimentMeta(
            experiment_id=exp_id,
            name=name,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            git_branch=git_branch,
            git_commit=git_commit,
            hypothesis=hypothesis,
            changes=changes or [],
            status="running",
            results={},
            verdict="",
        )

        exp_json = exp_dir / "experiment.json"
        with open(exp_json, "w", encoding="utf-8") as f:
            json.dump(asdict(meta), f, indent=2, ensure_ascii=False)

        # 初始化投资会话索引
        sessions_json = exp_dir / "investment_sessions.json"
        if not sessions_json.exists():
            with open(sessions_json, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)

        # 更新 INDEX.md
        self._update_index(meta)

        logger.info(f"Experiment registered: {exp_id} → {exp_dir}")
        return exp_dir

    def _update_index(self, meta: ExperimentMeta):
        """更新 INDEX.md"""
        index_path = self.base_dir / "INDEX.md"
        lines = index_path.read_text(encoding="utf-8").splitlines()

        # 检查是否已有该实验
        new_line = (
            f"| {meta.experiment_id} | {meta.name} | {meta.git_branch} | "
            f"{meta.status} | {meta.verdict} |"
        )
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"| {meta.experiment_id} "):
                lines[i] = new_line
                updated = True
                break

        if not updated:
            lines.append(new_line)

        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def update_results(
        self,
        exp_id: str,
        results: dict,
        verdict: str = "",
        status: str = "completed",
    ):
        """更新实验结果

        Args:
            exp_id: 实验 ID
            results: 结果指标字典 (如 {"sharpe": 1.5, "total_return": 0.15})
            verdict: 实验结论 (baseline | improved | degraded | inconclusive)
            status: 实验状态 (默认 completed)
        """
        exp_dir = self._exp_dir(exp_id)
        exp_json = exp_dir / "experiment.json"

        if not exp_json.exists():
            logger.warning(f"Experiment {exp_id} not found, skipping update")
            return

        with open(exp_json, "r", encoding="utf-8") as f:
            meta = json.load(f)

        meta["results"].update(results)
        if verdict:
            meta["verdict"] = verdict
        meta["status"] = status

        with open(exp_json, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        # 更新 INDEX.md
        self._update_index(ExperimentMeta(**meta))
        logger.info(f"Experiment {exp_id} updated: status={status}, verdict={verdict}")

    def record_session(
        self,
        exp_id: str,
        session_id: str,
        session_type: str,
        path: str,
        date: Optional[str] = None,
    ):
        """将投资会话记录到实验索引

        Args:
            exp_id: 实验 ID
            session_id: 投资会话 ID (如 20260518_paper_001)
            session_type: paper_trading | live_paper | live_trader
            path: 投资记录目录路径
            date: 日期字符串 (默认从 session_id 提取前8位)
        """
        exp_dir = self._exp_dir(exp_id)
        sessions_json = exp_dir / "investment_sessions.json"

        # 读取现有索引
        if sessions_json.exists():
            with open(sessions_json, "r", encoding="utf-8") as f:
                sessions = json.load(f)
        else:
            sessions = []

        # 从 session_id 提取日期
        if date is None and len(session_id) >= 8:
            date = f"{session_id[:4]}-{session_id[4:6]}-{session_id[6:8]}"

        entry = {
            "session_id": session_id,
            "type": session_type,
            "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "path": str(path),
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        sessions.append(entry)

        with open(sessions_json, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)

        logger.info(
            f"Recorded session {session_id} ({session_type}) → {exp_id}"
        )

    def get_experiment(self, exp_id: str) -> Optional[dict]:
        """读取实验元数据

        Returns:
            实验元数据字典，或 None（不存在时）
        """
        exp_dir = self._exp_dir(exp_id)
        exp_json = exp_dir / "experiment.json"

        if not exp_json.exists():
            logger.warning(f"Experiment {exp_id} not found")
            return None

        with open(exp_json, "r", encoding="utf-8") as f:
            return json.load(f)

    def compare(self, exp_id_a: str, exp_id_b: str) -> str:
        """对比两个实验的绩效指标，生成人类可读 Markdown 报告

        Args:
            exp_id_a: 第一个实验 ID
            exp_id_b: 第二个实验 ID

        Returns:
            Markdown 格式的对比报告
        """
        exp_a = self.get_experiment(exp_id_a)
        exp_b = self.get_experiment(exp_id_b)

        if not exp_a or not exp_b:
            missing = []
            if not exp_a:
                missing.append(exp_id_a)
            if not exp_b:
                missing.append(exp_id_b)
            return f"Error: Experiment(s) not found: {', '.join(missing)}"

        ra = exp_a.get("results", {})
        rb = exp_b.get("results", {})

        name_a = exp_a["name"]
        name_b = exp_b["name"]

        lines = [
            f"# Experiment Comparison: {exp_id_a} vs {exp_id_b}",
            "",
            f"| Metric | {name_a} ({exp_id_a}) | {name_b} ({exp_id_b}) | Δ |",
            "|--------|----------------------|----------------------|---|",
        ]

        def _fmt(val) -> str:
            if val is None:
                return "N/A"
            if isinstance(val, float):
                if abs(val) < 10:
                    return f"{val:.4f}"
                return f"{val:.2f}"
            return str(val)

        def _delta(a, b) -> str:
            if a is None or b is None:
                return "N/A"
            try:
                d = float(b) - float(a)
                sign = "+" if d >= 0 else ""
                return f"{sign}{d:.4f}"
            except (ValueError, TypeError):
                return "N/A"

        # Collect all metric keys from both experiments
        all_keys = sorted(set(list(ra.keys()) + list(rb.keys())))
        for key in all_keys:
            va = _fmt(ra.get(key))
            vb = _fmt(rb.get(key))
            d = _delta(ra.get(key), rb.get(key))
            lines.append(f"| {key} | {va} | {vb} | {d} |")

        lines.extend([
            "",
            "## Details",
            "",
            f"### {exp_id_a}",
            f"- **Name:** {name_a}",
            f"- **Branch:** {exp_a['git_branch']}",
            f"- **Commit:** {exp_a['git_commit']}",
            f"- **Hypothesis:** {exp_a['hypothesis']}",
            f"- **Status:** {exp_a['status']}",
            f"- **Verdict:** {exp_a.get('verdict', 'N/A')}",
            "",
            f"### {exp_id_b}",
            f"- **Name:** {name_b}",
            f"- **Branch:** {exp_b['git_branch']}",
            f"- **Commit:** {exp_b['git_commit']}",
            f"- **Hypothesis:** {exp_b['hypothesis']}",
            f"- **Status:** {exp_b['status']}",
            f"- **Verdict:** {exp_b.get('verdict', 'N/A')}",
        ])

        return "\n".join(lines)

    def list_experiments(self) -> list[dict]:
        """列出所有已注册的实验（按 ID 排序）"""
        experiments = []
        for exp_dir in self.base_dir.iterdir():
            if not exp_dir.is_dir() or exp_dir.name.startswith("."):
                continue
            exp_json = exp_dir / "experiment.json"
            if exp_json.exists():
                with open(exp_json, "r", encoding="utf-8") as f:
                    experiments.append(json.load(f))
        return sorted(experiments, key=lambda x: x["experiment_id"])

    def delete_experiment(self, exp_id: str):
        """删除实验及其目录

        Args:
            exp_id: 实验 ID
        """
        exp_dir = self._exp_dir(exp_id)
        if exp_dir.exists():
            import shutil

            shutil.rmtree(exp_dir)
            logger.info(f"Experiment {exp_id} deleted")

        # 从 INDEX.md 中移除
        index_path = self.base_dir / "INDEX.md"
        if index_path.exists():
            lines = [
                l
                for l in index_path.read_text(encoding="utf-8").splitlines()
                if not l.startswith(f"| {exp_id} ")
            ]
            index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
