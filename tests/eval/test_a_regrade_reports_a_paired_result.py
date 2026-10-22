"""A regrade that counts an unjudgeable row as wrong invents a gain; ``None`` stays unmeasured."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


TOOL = Path(__file__).resolve().parents[2] / "tools" / "regrade.py"


def _regrade_report(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> str:
    """Load the tool by path — ``tools/`` is not a package and nothing imports it."""
    spec = importlib.util.spec_from_file_location("regrade_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._regrade_report(before, after)


def _rows(*pairs: tuple[str, Any]) -> list[dict[str, Any]]:
    return [{"question_id": qid, "correct": value} for qid, value in pairs]


def test_a_row_the_regrade_cannot_judge_is_not_counted_as_wrong() -> None:
    before = _rows(("q1", True), ("q2", False), ("q3", False), ("q4", True))
    after = _rows(("q1", True), ("q2", True), ("q3", None), ("q4", True))

    out = _regrade_report(before, after)

    assert "0.750" not in out, (
        "the unjudgeable row was counted as wrong, which is how a regrade invents a 25-point "
        f"improvement:\n{out}"
    )
    assert "not measured" in out, f"an absent result must read as unmeasured:\n{out}"
    assert "unmeasured: 1 row" in out, f"the count must be stated, not implied:\n{out}"


def test_a_fully_measured_regrade_reports_the_paired_test() -> None:
    """The non-firing half: when every row is judged, the numbers appear — with the test.

    Kept as a pair so the assertion above cannot be satisfied by a report that says "not
    measured" about everything.
    """
    before = _rows(*[(f"q{i}", i % 3 == 0) for i in range(30)])
    after = _rows(*[(f"q{i}", i % 2 == 0) for i in range(30)])

    out = _regrade_report(before, after)

    assert "EX before: 0.33" in out, out
    assert "EX after : 0.50" in out, out
    # The point of the fix: the delta arrives with what it takes to interpret it.
    for token in ("p=", "discordant=", "MDE="):
        assert token in out, f"{token!r} missing, so the delta is uninterpretable:\n{out}"


def test_the_report_does_not_round_a_rate_into_a_claim() -> None:
    """``measure/`` owns rendering.

    The old line was ``f"{before / total:.3f}"`` — a format spec with a precision, which
    ``Measured.render`` exists to replace. It sat in ``tools/``.
    """
    source = TOOL.read_text(encoding="utf-8")
    assert ":.3f}" not in source, "a rate is being rounded at the print boundary"


@pytest.mark.parametrize("missing_side", ["before", "after"])
def test_a_unit_present_on_one_side_only_is_refused_rather_than_dropped(missing_side: str) -> None:
    """Filling a missing after-row from before made a failed write look unchanged."""
    before = _rows(("q1", True), ("q2", False))
    after = _rows(("q1", False), ("q2", True))
    if missing_side == "after":
        after = after[:1]
    else:
        before = before[:1]

    out = _regrade_report(before, after)
    assert out.startswith("not comparable:"), (
        "a row on one side only was reconciled into a number. The first implementation filled a "
        "missing `after` row from `before`, so a row the regrade failed to write read as "
        f"*unchanged*, and an extra row on the other side was ignored entirely:\n{out}"
    )
    assert "not paired" in out
