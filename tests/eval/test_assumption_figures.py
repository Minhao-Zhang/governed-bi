"""Assumption counts on the eval row, and the report figures of ADR 0018 §5."""

from __future__ import annotations

from typing import Any

from governed_bi.eval.harness import project_turn
from governed_bi.eval.projection import ASSUMPTION_BASES
from governed_bi.eval.report import assumption_figures, assumption_report_lines, summarise

_ZERO = dict.fromkeys(ASSUMPTION_BASES, 0)


def _claim(basis: str) -> dict[str, str]:
    return {"statement": basis, "about": basis, "basis": basis, "asset_id": "asset"}


def _row(
    qid: str,
    assumptions: list[dict[str, str]] | None,
    *,
    guess_count: int | None = None,
    refusals: int | None = None,
    correct: bool | None = None,
    outcome: str = "answered",
) -> dict[str, Any]:
    """One projected turn. ``correct`` is set after, because this fixture has no gold to grade."""
    record: dict[str, Any] = {"outcome": outcome}
    if assumptions is not None:
        record["assumptions"] = assumptions
        record["guess_count"] = guess_count
        record["ambiguous_guess_refusals"] = refusals
    row = project_turn(
        {"answer": {"outcome": outcome, "record": record}},
        question={"question_id": qid, "db_id": "s", "question": "how many?"},
        config="t",
    )
    row["correct"] = correct
    return row


def _fixture() -> list[dict[str, Any]]:
    """A correct guess, a wrong answer with no guess, and two server-added entries."""
    return [
        _row(
            "q1",
            [_claim("layer"), _claim("guess")],
            guess_count=1,
            refusals=2,
            correct=True,
        ),
        _row(
            "q2",
            [_claim("convention")],
            guess_count=0,
            refusals=0,
            correct=False,
        ),
        _row(
            "q3",
            [_claim("found_in_query"), _claim("found_in_query")],
            guess_count=0,
            refusals=1,
            correct=True,
        ),
    ]


# Hand totals over the three answers: layer 1, convention 1, guess 1, found_in_query 2.
_EXPECTED = {
    "n_answers": 3,
    "not_recorded": 0,
    "by_basis": {
        "layer": {"total": 1, "mean": 1 / 3},
        "convention": {"total": 1, "mean": 1 / 3},
        "guess": {"total": 1, "mean": 1 / 3},
        "found_in_query": {"total": 2, "mean": 2 / 3},
    },
    "with_guess": {"n": 1, "n_correct": 1, "accuracy": 1 / 1},
    "without_guess": {"n": 2, "n_correct": 1, "accuracy": 1 / 2},
    "found_in_query_unstated": {"total": 2, "n_with_any": 1, "share": 1 / 3},
    "ambiguous_guess_refusals": 3,
}

_EXPECTED_LINES = [
    "\nassumptions per answer (3 recorded, 0 not recorded):",
    "  layer: 1/3",
    "  convention: 1/3",
    "  guess: 1/3",
    "  found_in_query: 2/3",
    "  execution accuracy with a guess: 1/1 (n=1)",
    "  execution accuracy with no guess: 1/2 (n=2)",
    "  found_in_query the model did not state: 2 (1/3 answers)",
    "  ambiguous_guess_refusals: 3",
]


def test_the_row_carries_the_counts_and_leaves_a_missing_record_empty() -> None:
    """A missing list is ``None``. Zero is a written count, including a guess count of zero."""
    rows = _fixture()
    assert rows[0]["assumption_counts"] == {**_ZERO, "layer": 1, "guess": 1}
    assert rows[0]["guess_count"] == 1
    assert rows[0]["ambiguous_guess_refusals"] == 2
    assert rows[1]["assumption_counts"] == {**_ZERO, "convention": 1}
    assert rows[1]["guess_count"] == 0
    assert rows[2]["assumption_counts"] == {**_ZERO, "found_in_query": 2}
    assert rows[2]["ambiguous_guess_refusals"] == 1

    bare = _row("bare", None, correct=True)
    assert bare["assumption_counts"] is None
    assert bare["guess_count"] is None
    assert bare["ambiguous_guess_refusals"] is None

    empty = _row("empty", [], guess_count=0, refusals=0, correct=True)
    assert empty["assumption_counts"] == _ZERO
    assert empty["guess_count"] == 0
    assert empty["ambiguous_guess_refusals"] == 0


def test_the_three_row_fixture_matches_the_hand_figures() -> None:
    """Every reported figure, including the lines, against the totals above the fixture."""
    figures = assumption_figures(_fixture())
    assert figures == _EXPECTED
    assert assumption_report_lines(figures) == _EXPECTED_LINES


def test_a_row_without_assumption_fields_is_not_recorded_and_changes_nothing_else() -> None:
    """The extra answer is a gap in the recording. It is not an answer that stated nothing."""
    three = assumption_figures(_fixture())
    four = assumption_figures(_fixture() + [_row("q4", None, correct=False)])
    assert four["not_recorded"] == 1
    assert four["n_answers"] == three["n_answers"]
    kept = (
        "by_basis",
        "with_guess",
        "without_guess",
        "found_in_query_unstated",
        "ambiguous_guess_refusals",
    )
    assert {key: four[key] for key in kept} == {key: three[key] for key in kept}
    lines = assumption_report_lines(four)
    assert lines[0] == "\nassumptions per answer (3 recorded, 1 not recorded):"
    assert lines[1:] == _EXPECTED_LINES[1:]


def test_an_empty_assumption_list_is_a_measured_zero() -> None:
    """Zero refusals and a mean of zero are recorded. They are not the missing-field gap."""
    row = _row("empty", [], guess_count=0, refusals=0, correct=True)
    figures = assumption_figures([row])
    assert figures["n_answers"] == 1
    assert figures["not_recorded"] == 0
    assert figures["by_basis"] == {basis: {"total": 0, "mean": 0.0} for basis in ASSUMPTION_BASES}
    assert figures["with_guess"] == {"n": 0, "n_correct": None, "accuracy": None}
    assert figures["without_guess"] == {"n": 1, "n_correct": 1, "accuracy": 1.0}
    assert figures["found_in_query_unstated"] == {"total": 0, "n_with_any": 0, "share": 0.0}
    assert figures["ambiguous_guess_refusals"] == 0


def test_refusals_count_every_turn_that_recorded_them() -> None:
    """A refused guess usually ends in a clarification, so non-answers count too."""
    rows = _fixture()
    rows.append({"outcome": "clarification", "ambiguous_guess_refusals": 2})
    assert assumption_figures(rows)["ambiguous_guess_refusals"] == 5
    for row in rows:
        row["ambiguous_guess_refusals"] = None
    figures = assumption_figures(rows)
    assert figures["ambiguous_guess_refusals"] is None
    assert figures["by_basis"]["guess"]["total"] == 1
    assert "  ambiguous_guess_refusals: not recorded" in assumption_report_lines(figures)


def test_the_config_summary_carries_the_assumption_figures() -> None:
    """``summarise`` is what the run report reads, so the figures end there."""
    rows = _fixture()
    summary = summarise({"v4": rows})
    assert summary["configs"]["v4"]["assumptions"] == assumption_figures(rows)
