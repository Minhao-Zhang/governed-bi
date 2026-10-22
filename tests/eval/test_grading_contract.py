"""The number is about the answer the system gave. A refused statement is not graded by running it again."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Each spec is its own strict xfail. One module marker is one deletion that disarms every test.
UNWRITTEN = pytest.mark.xfail(strict=True, reason="contract specified, body not yet written")


# ── the grader is not a governance bypass ─────────────────────────────────────


@UNWRITTEN
def test_a_governance_refusal_is_never_scored_correct() -> None:
    """A refused statement has no result to grade. Re-running it outside governance scored it correct."""
    pytest.fail("not implemented: see docstring")


def test_the_grader_has_no_execution_path_that_skips_govern() -> None:
    """Gold and a refused proposal execute at a fixed set of sites. A new site must be visible. See ADR 0006."""
    import ast
    import pathlib

    #: Counted, not pinned to line numbers. A new ``execute`` is what must fail.
    ALLOWED: dict[str, tuple[int, tuple[str, ...]]] = {
        "projection.py": (
            3,
            (
                "abstention pricing: runs the refused proposal to price what the refusal cost",
                "the answered turn's own governed statement, re-run for the fingerprint",
                "gold SQL, which the benchmark owns and no corpus licenses",
            ),
        ),
        "oracle.py": (
            1,
            ("the oracle config executes gold directly; it is the upper bound, not a served turn",),
        ),
    }

    eval_dir = pathlib.Path(__file__).resolve().parents[2] / "src" / "governed_bi" / "eval"
    found: dict[str, int] = {}
    for source in sorted(eval_dir.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        calls = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        )
        if calls:
            found[source.name] = calls

    declared = {name: count for name, (count, _) in ALLOWED.items()}
    assert found == declared, (
        "the set of ungoverned database calls in eval/ moved.\n"
        f"  found   : {dict(sorted(found.items()))}\n"
        f"  declared: {dict(sorted(declared.items()))}\n"
        "A new site is a new way for the grader to reach the database without governance. If it "
        "is necessary, add it to ALLOWED with the reason grading needs it. If one was removed, "
        "drop it here in the same commit — a stale allowance is a capability nobody is watching."
    )
    # Anti-vacuity: the walk must be finding calls, or an empty `found` would match an
    # empty `declared` and this test would pass on a tree with no eval/ at all.
    assert sum(found.values()) == 4, f"expected 4 declared ungoverned executes, saw {found}"


# ── absence must not become a value, in either direction ──────────────────────


@UNWRITTEN
def test_a_missing_outcome_is_not_counted_as_a_crash() -> None:
    """An unrecorded outcome is unmeasured, not a crash."""
    pytest.fail("not implemented: see docstring")


@pytest.mark.parametrize("field", ["guardrail_error", "re_served", "channel_degraded"])
@UNWRITTEN
def test_an_unrecorded_health_field_is_not_recorded_as_clean(field: str) -> None:
    """Substituting ``0`` or ``False`` turns an absent field into a clean measurement."""
    pytest.fail("not implemented: see docstring")


# ── the comparison must refuse itself when it cannot be trusted ───────────────


@UNWRITTEN
def test_an_config_with_zero_successful_executions_is_not_quotable() -> None:
    """An EX with zero in-turn executions is not a result. The count has to be on the row."""
    pytest.fail("not implemented: see docstring")


@UNWRITTEN
def test_the_report_never_renders_an_unmeasured_quantity_as_a_number() -> None:
    """A rate is rendered by ``Measured``. An f-string would print an unmeasured quantity as a number."""
    pytest.fail("not implemented: see docstring")


@UNWRITTEN
def test_a_clean_run_is_quotable(capsys) -> None:
    """A harness that refuses everything satisfies the checks above. A fully measured config must still be quotable."""
    pytest.fail("not implemented: see docstring")


def test_an_unwritten_count_does_not_pass_the_check_as_a_clean_zero() -> None:
    """``0`` is both a clean count and the shape of a missing field. Substituting it reports that nobody counted as
    nothing went wrong."""
    from governed_bi.eval.harness import project_turn

    row = project_turn(
        {"answer": {"record": {"outcome": "answered"}}},
        question={"question_id": "q1", "db_id": "sales", "question": "how many"},
        config="test",
    )
    for field in ("guardrail_error", "re_served", "channel_degraded"):
        assert row[field] is None, (
            f"{field} reads {row[field]!r} for a turn that never recorded it, and the check then "
            "passes on a measurement nobody took"
        )
