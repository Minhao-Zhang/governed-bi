"""A turn that executed no governed statement records ``outcome: no_sql``, not ``answered``.

``has_sql`` follows the ledger. An empty ledger with no refusal is ``no_sql``; a turn nothing
observed ending is still ``crashed``, and a guard refusal outranks the empty ledger.
"""

from __future__ import annotations

from typing import Any

from governed_bi.registry.stages import Outcome, classify_outcome
from governed_bi.serve.nodes.finish import finish

#: The prose from the live turn, verbatim. Not asserted *on* — the fix reads the ledger, never the
#: text — but carried so a reader can see what the record called an answer.
DECLINE_PROSE = (
    "Could you clarify what you mean by 'assets', 'actively maintained', and 'PM task'? "
    "These terms are not defined in the provided schemas, so I can't form a reliable "
    "governed query."
)

_PASSING_ATTEMPT: dict[str, Any] = {
    "result_layer": None,
    "passed": True,
    "reason_code": "passed",
    "path": "agent",
    "executed_sql": "SELECT count(*) FROM sales.orders",
    "checked_sql": "SELECT count(*) FROM sales.orders",
}


def _turn(**overrides: Any) -> dict[str, Any]:
    """The turn as the graph leaves it for ``finish``: agent loop finished, nothing else set."""
    state: dict[str, Any] = {
        "path_kind": "answered",
        "turn_id": "turn-under-test",
        "turn_index": 1,
        "usage": [],
    }
    state.update(overrides)
    return state


def test_a_prose_decline_with_no_statement_is_not_answered() -> None:
    """``!= answered`` and ``== no_sql`` are separate: a crash is the other wrong outcome."""
    answer = finish(
        _turn(
            execution={"attempts": [], "terminal": "no_sql", "guardrail_errors": 0},
            generated_sql=None,
            answer_text=DECLINE_PROSE,
        )
    )["answer"]

    assert answer["outcome"] != Outcome.answered.value, (
        "a turn that executed no governed statement recorded `answered`. This is the 2026-08-18 "
        "defect: `finish._path_signals` hardcodes `has_sql=True` in the `path_kind == 'answered'` "
        f"fall-through, so `generated_sql={answer['record']['generated_sql']!r}` and "
        f"`execution.terminal={answer['record']['execution']['terminal']!r}` are ignored and the "
        "UI renders `answered` beside `ledger: no_sql` and `no SQL attempted`"
    )
    assert answer["outcome"] == Outcome.no_sql.value, (
        f"expected {Outcome.no_sql.value!r} and got {answer['outcome']!r}. `crashed` here means "
        "the fall-through was deleted rather than replaced: nothing failed on this turn, it "
        "simply ran no statement"
    )

    # The record and the ledger say one thing, which is the whole point of naming the outcome
    # after the ledger's own word.
    assert answer["record"]["execution"]["terminal"] == Outcome.no_sql.value
    assert answer["record"]["generated_sql"] is None
    assert answer["refused_by"] is None and answer["failed_stage"] is None
    # The model's prose survives. A turn whose only content is what the model said must not lose
    # it to the reclassification — the audit surface would have nothing to show.
    assert answer["answer_text"] == DECLINE_PROSE


def test_a_turn_with_a_governed_statement_is_still_answered() -> None:
    """The paired positive. Without it, "not answered" is satisfiable by never answering."""
    answer = finish(
        _turn(
            execution={
                "attempts": [_PASSING_ATTEMPT],
                "terminal": "answered",
                "guardrail_errors": 0,
            },
            generated_sql=_PASSING_ATTEMPT["executed_sql"],
            answer_text="There are 42 orders.",
        )
    )["answer"]

    assert answer["outcome"] == Outcome.answered.value, (
        f"a turn with a passing ledger row and a statement recorded {answer['outcome']!r}. The "
        "`no_sql` split must narrow `answered` to turns that ran a statement, not empty it"
    )
    assert answer["record"]["execution"]["terminal"] == "answered"


def test_the_no_model_stub_records_no_statement_too() -> None:
    """The stub writes no ledger; the outcome is derived from an empty attempt list, not a raw ``None``."""
    from governed_bi.serve.nodes.agent_core import STUB_ANSWER, _stub

    update = _stub({"turn_index": 1})
    assert "execution" not in update, (
        "the stub now writes a ledger, so this test no longer covers the absent-ledger case"
    )

    answer = finish(_turn(**{k: v for k, v in update.items() if k != "messages"}))["answer"]

    assert answer["outcome"] == Outcome.no_sql.value, (
        f"the stub path recorded {answer['outcome']!r}. `crashed` means the classification is "
        "reading a raw absent `execution` instead of `OutcomeInputs`' substitution; `answered` "
        f"means the fall-through is back. ({STUB_ANSWER} is not a governed answer either way.)"
    )
    assert answer["record"]["execution"] == {
        "attempts": [],
        "terminal": Outcome.no_sql.value,
        "guardrail_errors": 0,
    }


def test_a_turn_with_no_ledger_result_at_all_is_still_a_crash() -> None:
    """``no_sql`` needs the ledger's own empty result; an unmarked turn is still a crash."""
    assert (
        classify_outcome(error=None, refused_by=None, has_sql=False, terminal=None)
        is Outcome.crashed
    )
    assert finish(_turn(path_kind=None))["answer"]["outcome"] == Outcome.crashed.value


def test_a_refusal_outranks_an_empty_ledger() -> None:
    """A guard refusal outranks an empty ledger, so the outcome stays ``refused``."""
    answer = finish(
        _turn(
            path_kind="refuse",
            terminal_reason="guard",
            guard={"outcome": "blocked", "rule_id": "g_instruction_override"},
            execution={"attempts": [], "terminal": "no_sql", "guardrail_errors": 0},
        )
    )["answer"]

    assert answer["outcome"] == Outcome.refused.value, (
        f"a guard-blocked turn recorded {answer['outcome']!r}. Its ledger is empty like every "
        "statement-less turn's, but something decided, and `Outcome` requires the two stay apart"
    )
    assert answer["refused_by"] == "guard"
