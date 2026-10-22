"""A swallowed exception inside ``check()`` records ``outcome: crashed``, not ``refused``.

A crash is our bug and a refusal is the product working. ``guardrail_errors`` already counts
the exception; ``outcome`` has to agree with that count.
"""

from __future__ import annotations

from typing import Any

from governed_bi.govern.layers import GUARDRAIL_ERROR, GUARDRAIL_REFUSED_BY
from governed_bi.registry.stages import CRASH_REFUSED_BY, REFUSED_BY_TO_STAGE, Outcome, Stage
from governed_bi.serve.ledger import execution_from_attempts
from governed_bi.serve.nodes.finish import finish


def _attempt(reason_code: str) -> dict[str, Any]:
    """A failed attempt. ``result_layer`` is contextual for a swallowed exception, hence None."""
    return {
        "result_layer": None if reason_code == GUARDRAIL_ERROR else "TABLES",
        "passed": False,
        "reason_code": reason_code,
        "path": "agent",
        "executed_sql": None,
    }


def _finish(reason_code: str) -> dict[str, Any]:
    execution = execution_from_attempts([_attempt(reason_code)])
    out = finish(
        {
            "path_kind": "answered",
            "execution": execution,
            "generated_sql": "SELECT 1",
            "turn_id": f"turn-{reason_code}",
            "turn_index": 1,
            "settings_resolved": {},
            "usage": [],
        }
    )
    return {**out["answer"], "guardrail_errors": execution["guardrail_errors"]}


def test_a_swallowed_check_exception_records_as_crashed() -> None:
    answer = _finish(GUARDRAIL_ERROR)

    assert answer["guardrail_errors"] == 1, "the ledger stopped counting; this test proves nothing"
    assert answer["outcome"] == Outcome.crashed.value, (
        f"outcome={answer['outcome']!r} beside guardrail_errors=1. An exception swallowed inside "
        "check() is our bug, and recording it as `refused` reports the product working."
    )
    assert answer["refused_by"] == GUARDRAIL_ERROR
    assert answer["failed_stage"] == Stage.check.value


def test_a_layer_refusal_is_still_a_refusal() -> None:
    """The paired non-firing case: the fix must not turn every failed attempt into a crash."""
    answer = _finish("r_table_not_retrieved")

    assert answer["guardrail_errors"] == 0
    assert answer["outcome"] == Outcome.refused.value
    assert answer["refused_by"] == GUARDRAIL_REFUSED_BY
    assert answer["failed_stage"] is None


def test_the_two_outcomes_are_different() -> None:
    """A plain refusal and a swallowed ``check()`` exception stay different outcomes."""
    assert _finish(GUARDRAIL_ERROR)["outcome"] != _finish("r_table_not_retrieved")["outcome"]


def test_the_crash_vocabulary_carries_the_producer() -> None:
    """``GUARDRAIL_ERROR`` is a declared crash reason with a stage, so it is attributable."""
    assert GUARDRAIL_ERROR in CRASH_REFUSED_BY
    assert REFUSED_BY_TO_STAGE[GUARDRAIL_ERROR] is Stage.check
