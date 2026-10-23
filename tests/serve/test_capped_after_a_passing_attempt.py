"""A turn the attempt cap ended is ``capped``, even when an earlier statement succeeded.

The cap terminates the turn (ADR 0006). Both the ledger and ``finish`` test the cap before
"did any attempt pass", so an earlier success cannot hide it.
"""

from __future__ import annotations

from typing import Any

from governed_bi.registry.stages import ATTEMPT_CAP_REFUSED_BY, Outcome
from governed_bi.serve.ledger import cap_attempt, execution_from_attempts
from governed_bi.serve.nodes.finish import finish


def _attempt(passed: bool, reason_code: str, path: str = "agent") -> dict[str, Any]:
    return {
        "result_layer": None if passed else "TABLES",
        "passed": passed,
        "reason_code": reason_code,
        "path": path,
        "executed_sql": "SELECT 1" if passed else None,
        "checked_sql": "SELECT 1" if passed else None,
    }


#: The live shape: two statements ran, two were blocked, and the cap ended the turn.
_MIXED = [
    _attempt(True, "passed"),
    _attempt(False, "r_table_not_retrieved"),
    _attempt(True, "passed"),
    _attempt(False, "r_table_not_retrieved"),
    cap_attempt(),
]


def test_the_ledger_calls_a_capped_turn_capped_even_after_a_pass() -> None:
    assert execution_from_attempts(_MIXED)["terminal"] == "capped", (
        "an earlier passing statement made 'capped' unreachable, so the terminal that the "
        "cap row exists to produce was never written on a turn that had answered anything"
    )


def test_a_pass_with_no_cap_row_still_reads_answered() -> None:
    """The other direction, so the fix cannot be 'always capped'."""
    assert execution_from_attempts(_MIXED[:4])["terminal"] == "answered"


def test_the_record_of_a_capped_turn_says_capped_not_answered() -> None:
    """``finish`` reads the ledger's terminal, so ``outcome`` cannot contradict it."""
    execution = execution_from_attempts(_MIXED)
    out = finish(
        {
            "path_kind": "answered",
            "execution": execution,
            "generated_sql": "SELECT 1",
            "turn_id": "turn-capped",
            "turn_index": 1,
            "settings_resolved": {},
            "usage": [],
        }
    )
    answer = out["answer"]
    assert answer["outcome"] == Outcome.capped.value, (
        f"outcome={answer['outcome']!r} beside terminal={execution['terminal']!r}; "
        "whichever a reader trusts, the other is lying"
    )
    assert answer["refused_by"] == ATTEMPT_CAP_REFUSED_BY
    assert answer["record"]["outcome"] == Outcome.capped.value


def test_a_turn_whose_every_attempt_was_blocked_is_still_a_guardrail_refusal() -> None:
    """The branch the cap check now sits in front of. It must keep its own reason: 'the
    guardrails refused everything' and 'the cap ran out' are different engineering problems."""
    blocked = [_attempt(False, "r_table_not_retrieved"), _attempt(False, "r_binding")]
    out = finish(
        {
            "path_kind": "answered",
            "execution": execution_from_attempts(blocked),
            "generated_sql": "SELECT 1",
            "turn_id": "turn-blocked",
            "turn_index": 1,
            "settings_resolved": {},
            "usage": [],
        }
    )
    assert out["answer"]["outcome"] == Outcome.refused.value
    assert out["answer"]["refused_by"] != ATTEMPT_CAP_REFUSED_BY


def test_a_crashed_turn_does_not_keep_execution_terminal_answered() -> None:
    """``outcome=crashed`` beside ``execution.terminal=answered`` invites readers to treat
    a failed turn as answered. Finish rewrites the projected terminal; attempts stay."""
    execution = execution_from_attempts([_attempt(True, "passed")])
    assert execution["terminal"] == "answered"
    out = finish(
        {
            "path_kind": "crashed",
            "failure": {"stage": "agent_core", "error_type": "TimeoutError"},
            "execution": execution,
            "generated_sql": "SELECT 1",
            "turn_id": "turn-crashed",
            "turn_index": 1,
            "settings_resolved": {},
            "usage": [],
        }
    )
    answer = out["answer"]
    assert answer["outcome"] == Outcome.crashed.value
    projected = answer["record"]["execution"]
    assert projected["terminal"] == "crashed", (
        f"outcome=crashed beside terminal={projected['terminal']!r}; "
        "whichever a reader trusts, the other is lying"
    )
    assert projected["attempts"], "the ledger rows that already ran must still be on the record"
