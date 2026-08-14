"""A measured row carries the reflector's verdict, or the arm that pays for it measures nothing.

``reflect_enabled`` ships off, and the knob's own note says it stays off *"until
tools/score_reflector.py shows the verdict beats the base rate"*. That tool reads measurement
rows. ``stamp`` has projected ``reflect_verdict`` into the turn record since the node landed and
nothing carried it out to the artifact — so the run that was supposed to settle the question
would have spent one utility-model call on every one of 1,351 turns and produced an artifact with
no verdict in it.

The verdict is `None` when the reflector did not run, and `None` has to stay distinguishable from
"ran and said nothing": both tests below assert a *value*, not the presence of the key, because a
row carrying a constant `None` satisfies `"reflect_verdict" in row` forever.
"""

from __future__ import annotations

from typing import Any

from governed_bi.eval.harness import project_turn


def _state(record: dict[str, Any]) -> dict[str, Any]:
    """The minimum a turn needs to project, with the record under test attached."""
    return {
        "answer": {"answer_text": "42", "outcome": "answered", "record": record},
        "licensed": ["s.t"],
        "schemas": ["s"],
    }


def _record(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "outcome": "answered",
        "terminal_reason": None,
        "execution": {"attempts": []},
        "usage": [],
        "corpus_content_hash": "corpus-x",
        "prompt_set_hash": "prompt-x",
    }
    base.update(over)
    return base


def _question() -> dict[str, Any]:
    return {"question_id": "q1", "db_id": "s", "question": "how many?"}


def test_a_verdict_on_the_record_reaches_the_measured_row():
    row = project_turn(
        _state(_record(reflect_verdict="likely_wrong")),
        question=_question(),
        arm="test",
    )
    assert row["reflect_verdict"] == "likely_wrong", (
        "the reflector ran and the artifact does not say what it decided; "
        "score_reflector.py has nothing to score"
    )


def test_each_verdict_value_survives_rather_than_being_coerced():
    """`unsure` is a first-class verdict in this vocabulary, not a failure to decide.

    Coercing it — to None, to a bool, to "" — would delete exactly the rows that distinguish a
    calibrated judge from a confident one.
    """
    for verdict in ("likely_right", "likely_wrong", "unsure"):
        row = project_turn(
            _state(_record(reflect_verdict=verdict)),
            question=_question(),
            arm="test",
        )
        assert row["reflect_verdict"] == verdict


def test_a_turn_where_the_reflector_did_not_run_carries_none_not_a_guess():
    row = project_turn(
        _state(_record()),
        question=_question(),
        arm="test",
    )
    assert row["reflect_verdict"] is None
