"""A measured row carries its token totals, so cost can be reported next to EX per question."""

from __future__ import annotations

from typing import Any

from governed_bi.eval.harness import project_turn
from governed_bi.register.quantity import Measured


def _row(usage: list[dict[str, Any]]) -> dict[str, Any]:
    record = {
        "outcome": "answered",
        "terminal_reason": None,
        "execution": {"attempts": []},
        "usage": usage,
        "corpus_content_hash": "corpus-x",
        "prompt_set_hash": "prompt-x",
    }
    state = {"answer": {"answer_text": "42", "outcome": "answered", "record": record}, "licensed": [], "schemas": []}
    return project_turn(state, question={"question_id": "q1", "db_id": "s", "question": "how many?"}, arm="t")


def test_the_totals_sum_every_stage() -> None:
    row = _row(
        [
            {"stage": "guard", "input_tokens": 3600, "output_tokens": 2},
            {"stage": "agent_core", "input_tokens": 40000, "output_tokens": 900},
        ]
    )
    assert (row["input_tokens"], row["output_tokens"]) == (43600, 902)


def test_a_turn_with_no_model_call_spent_nothing() -> None:
    row = _row([])
    assert (row["input_tokens"], row["output_tokens"]) == (0, 0)


def test_an_uncounted_call_makes_the_total_unknown_rather_than_small() -> None:
    unmeasured = Measured.unmeasured("provider reported no usage")
    row = _row(
        [
            {"stage": "guard", "input_tokens": 3600, "output_tokens": 2},
            {"stage": "agent_core", "input_tokens": unmeasured, "output_tokens": unmeasured},
        ]
    )
    assert row["input_tokens"] is None and row["output_tokens"] is None
