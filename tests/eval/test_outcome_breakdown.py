"""Clarification and refusal are different results; both would otherwise read as not correct."""

from __future__ import annotations

from governed_bi.eval.harness import project_turn
from governed_bi.eval.report import outcome_rates
from governed_bi.measure.population import Population


def _question(qid: str = "q1") -> dict:
    return {"question_id": qid, "question": "how many customers", "db_id": "main"}


def test_a_clarification_interrupt_is_flagged_clarified_not_refused() -> None:
    state = {"__interrupt__": [{"value": {"kind": "clarification"}}], "answer": None}
    row = project_turn(state, question=_question(), config="curated")
    assert row["outcome"] == "clarification"
    assert row["clarified"] is True
    assert row["refused"] is False
    assert row["correct"] is False


def test_a_refusal_is_flagged_refused_not_clarified() -> None:
    state = {"answer": {"outcome": "refused", "refused_by": "no_coverage", "record": {}}}
    row = project_turn(state, question=_question(), config="curated")
    assert row["outcome"] == "refused"
    assert row["refused"] is True
    assert row["clarified"] is False
    assert row["correct"] is False


def test_a_clean_answer_is_neither() -> None:
    state = {
        "answer": {
            "outcome": "answered",
            "record": {"generated_sql": "SELECT 1"},
        }
    }
    row = project_turn(state, question=_question(), config="curated")
    assert row["outcome"] == "answered"
    assert row["refused"] is False
    assert row["clarified"] is False


def test_outcome_rates_reports_all_three_buckets_summing_to_the_population() -> None:
    """The report-level aggregate the plan asks for."""
    rows = [
        {"question_id": "q1", "correct": True, "refused": False, "clarified": False},
        {"question_id": "q2", "correct": True, "refused": False, "clarified": False},
        {"question_id": "q3", "correct": False, "refused": False, "clarified": True},
        {"question_id": "q4", "correct": False, "refused": True, "clarified": False},
    ]
    pop = Population.of("curated", rows)
    rates = outcome_rates(pop)
    assert rates["correct"].value == 0.5
    assert rates["clarified"].value == 0.25
    assert rates["refused"].value == 0.25
