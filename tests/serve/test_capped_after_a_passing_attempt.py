"""A turn the attempt cap ended is ``capped``, even when an earlier statement succeeded.

**Observed live.** An agent with two passing and two blocked attempts hit the cap and closed
with *"The query tool reached its execution-attempt limit before returning the winning
district, so I can't reliably state the result"* — and the record stamped ``outcome:
answered`` beside ``terminal: "answered"``. The user was told nothing and the artifact said
they had been told the answer.

The cause was an ordering, in two places that had to agree and did:
``execution_from_attempts`` tested "did any attempt pass" *before* "did the cap fire", so
``"capped"`` was unreachable on any turn that had ever succeeded; and ``stamp._path_signals``
read the cap out of a ternary nested inside its own "no attempt passed" branch, so its capped
arm was unreachable for the same reason. ADR 0006 §5 says the cap **terminates the turn** and
that a cap-terminated turn gets its own ``Outcome`` member; an earlier success does not undo
the termination.

Kept as a test because ``AGENTS.md`` reserves tests for problems actually hit, and this one was
hit on a paid run — where the cost of the confusion is an EX credited to a turn that refused to
state its own result.
"""

from __future__ import annotations

from typing import Any

from governed_bi.register.stages import ATTEMPT_CAP_REFUSED_BY, Outcome
from governed_bi.serve.ledger import cap_attempt, execution_from_attempts
from governed_bi.serve.nodes.stamp import stamp


def _attempt(passed: bool, reason_code: str, path: str = "agent") -> dict[str, Any]:
    return {
        "verdict_layer": None if passed else "TABLES",
        "passed": passed,
        "reason_code": reason_code,
        "path": path,
        "executed_sql": "SELECT 1" if passed else None,
    }


#: The live shape: two statements ran, two were blocked, and the cap ended the turn.
_MIXED = [
    _attempt(True, "passed"),
    _attempt(False, "r_table_not_licensed"),
    _attempt(True, "passed"),
    _attempt(False, "r_table_not_licensed"),
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
    """``stamp`` reads the ledger's verdict rather than re-deriving it, so the two cannot
    disagree about the same attempts — which is how ``outcome`` and ``execution.terminal``
    came to contradict each other in the first place."""
    execution = execution_from_attempts(_MIXED)
    out = stamp(
        {
            "path_kind": "answered",
            "execution": execution,
            "generated_sql": "SELECT 1",
            "turn_id": "turn-capped",
            "turn_index": 1,
            "knobs_resolved": {},
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
    blocked = [_attempt(False, "r_table_not_licensed"), _attempt(False, "r_binding")]
    out = stamp(
        {
            "path_kind": "answered",
            "execution": execution_from_attempts(blocked),
            "generated_sql": "SELECT 1",
            "turn_id": "turn-blocked",
            "turn_index": 1,
            "knobs_resolved": {},
            "usage": [],
        }
    )
    assert out["answer"]["outcome"] == Outcome.refused.value
    assert out["answer"]["refused_by"] != ATTEMPT_CAP_REFUSED_BY
