"""A regrade is the most tightly paired comparison here, and it reported the loosest number.

Audit E1. ``tools/regrade.py`` re-grades one artifact's rows with a changed grader — same
questions, same run, one variable — and then printed two bare rates. Three things were wrong and
all pointed the same way, toward a result looking more informative than it is:

* ``sum(1 for r in regraded if r.get("correct"))`` counted a row the regrade **could not judge**
  as *wrong*, against ``eval/grade.py``'s "callers must propagate the ``None`` rather than coerce
  it" — and against the comment in ``regrade.py`` itself, which says exactly that about the row
  while the headline coerced it anyway;
* the denominator was every row, unmeasured included;
* no paired test, though ``flips`` already holds the McNemar table (``wrong -> correct`` is
  ``only_b``, ``correct -> wrong`` is ``only_a``).

Measured on a four-row fixture with one unjudgeable row: the old arithmetic prints
``EX after: 3/4 = 0.750`` against a before of ``0.500`` — a 25-point improvement invented by a
row nobody could grade.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("G")

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
    assert "not measured" in out, f"an absent verdict must read as unmeasured:\n{out}"
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
    """``measure/`` owns rendering, so ``check_measurement_locality``'s rule reaches this too.

    The old line was ``f"{before / total:.3f}"`` — a format spec with a precision, which is what
    that gate exists to reject inside the package. It sat in ``tools/``, which the gate does not
    scan.
    """
    source = TOOL.read_text(encoding="utf-8")
    assert ":.3f}" not in source, "a rate is being rounded at the print boundary"


@pytest.mark.parametrize("missing_side", ["before", "after"])
def test_a_unit_present_on_one_side_only_is_refused_rather_than_dropped(missing_side: str) -> None:
    """A regrade writes every row it read, so a set that moved is a broken run.

    ``mcnemar`` refuses a mismatched unit set where the rival copy in
    ``tools/query_summary_alignment.py`` silently intersected it — and ``_regrade_report`` must not
    undo that refusal by reconciling the sets itself. Its first implementation did: it filled a
    missing ``after`` row from the matching ``before`` row, which made a row the regrade failed to
    write read as *unchanged*, and it ignored an extra row on the other side. Found by this test.
    """
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
