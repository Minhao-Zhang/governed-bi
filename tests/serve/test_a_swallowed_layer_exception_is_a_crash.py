"""A broken ``check()`` is our bug, and the record has to say so.

``Outcome``'s docstring is the rule: *"``crashed`` vs ``refused`` must stay separate: a crash is
our bug, a refusal is the product working."* Before the 2026-08-10 audit (C3), a turn whose every
attempt ended in a swallowed exception inside ``check()`` recorded ``outcome: refused`` — the same
value as a turn the layer stack legitimately objected to.

``govern.layers.GUARDRAIL_ERROR`` had already written down what that costs: *"a systematically
broken ``check()`` otherwise presents as an arm that refuses everything, with ``crash_rate == 0``
and every register key present."* The count existed (``guardrail_errors``, derived by
``execution_from_attempts``) and was gated; ``outcome`` contradicted it, and a reader comparing the
two fields could not tell which to trust.

Nothing in the suite covered this path — the whole suite stayed green through the fix, which is why
these tests exist rather than an amendment to an existing file.
"""

from __future__ import annotations

from typing import Any

from governed_bi.govern.layers import GUARDRAIL_ERROR, GUARDRAIL_REFUSED_BY
from governed_bi.register.stages import CRASH_REFUSED_BY, REFUSED_BY_TO_STAGE, Outcome, Stage
from governed_bi.serve.ledger import execution_from_attempts
from governed_bi.serve.nodes.stamp import stamp


def _attempt(reason_code: str) -> dict[str, Any]:
    """A failed attempt. ``verdict_layer`` is contextual for a swallowed exception, hence None."""
    return {
        "verdict_layer": None if reason_code == GUARDRAIL_ERROR else "TABLES",
        "passed": False,
        "reason_code": reason_code,
        "path": "agent",
        "executed_sql": None,
    }


def _stamp(reason_code: str) -> dict[str, Any]:
    execution = execution_from_attempts([_attempt(reason_code)])
    out = stamp(
        {
            "path_kind": "answered",
            "execution": execution,
            "generated_sql": "SELECT 1",
            "turn_id": f"turn-{reason_code}",
            "turn_index": 1,
            "knobs_resolved": {},
            "usage": [],
        }
    )
    return {**out["answer"], "guardrail_errors": execution["guardrail_errors"]}


def test_a_swallowed_check_exception_records_as_crashed() -> None:
    answer = _stamp(GUARDRAIL_ERROR)

    assert answer["guardrail_errors"] == 1, "the ledger stopped counting; this test proves nothing"
    assert answer["outcome"] == Outcome.crashed.value, (
        f"outcome={answer['outcome']!r} beside guardrail_errors=1. An exception swallowed inside "
        "check() is our bug, and recording it as `refused` reports the product working."
    )
    assert answer["refused_by"] == GUARDRAIL_ERROR
    assert answer["failed_stage"] == Stage.check.value


def test_a_layer_refusal_is_still_a_refusal() -> None:
    """The paired non-firing case: the fix must not turn every failed attempt into a crash."""
    answer = _stamp("r_table_not_licensed")

    assert answer["guardrail_errors"] == 0
    assert answer["outcome"] == Outcome.refused.value
    assert answer["refused_by"] == GUARDRAIL_REFUSED_BY
    assert answer["failed_stage"] is None


def test_the_two_outcomes_are_actually_different() -> None:
    """Guards the pair above against a future change that collapses them again — including one
    that collapses them the *other* way, by making a plain refusal crash too."""
    assert _stamp(GUARDRAIL_ERROR)["outcome"] != _stamp("r_table_not_licensed")["outcome"]


def test_the_crash_vocabulary_carries_the_producer() -> None:
    """``CRASH_REFUSED_BY``'s other member, ``model_error``, has no producer in ``src/``. This
    asserts the one that does is declared, and that ``layers.py``'s import-time guard has a stage
    to check against — a crash refusal with no stage is attributed to nothing."""
    assert GUARDRAIL_ERROR in CRASH_REFUSED_BY
    assert REFUSED_BY_TO_STAGE[GUARDRAIL_ERROR] is Stage.check
