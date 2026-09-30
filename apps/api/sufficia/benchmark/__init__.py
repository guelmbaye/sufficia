"""Canonical 50-case benchmark: runner, fidelity checks and reports (Document 06 §7-25, §103, §108)."""

from .report import write_reports
from .runner import (
    BenchmarkError,
    BenchmarkRun,
    CaseResult,
    check_explanations,
    load_fixtures,
    make_snapshot,
    replay_snapshot,
    run_benchmark,
)

__all__ = [
    "BenchmarkError",
    "BenchmarkRun",
    "CaseResult",
    "check_explanations",
    "load_fixtures",
    "make_snapshot",
    "replay_snapshot",
    "run_benchmark",
    "write_reports",
]
