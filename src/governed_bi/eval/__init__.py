"""Eval harness: grade serve turns, report with measure gates (Parcel G)."""

from governed_bi.eval.arms import ArmSpec, oracle_arm, scripted_arm, stub_arm
from governed_bi.eval.grade import grade_results, grade_turn, result_fingerprint
from governed_bi.eval.harness import run_arm, run_comparison
from governed_bi.eval.oracle import oracle_grade
from governed_bi.eval.report import (
    comparison_quotable,
    context_hashes_distinct,
    headline_ex,
    paired_ex,
    summarise,
)

__all__ = [
    "ArmSpec",
    "oracle_arm",
    "scripted_arm",
    "stub_arm",
    "grade_results",
    "grade_turn",
    "result_fingerprint",
    "run_arm",
    "run_comparison",
    "oracle_grade",
    "comparison_quotable",
    "context_hashes_distinct",
    "headline_ex",
    "paired_ex",
    "summarise",
]
