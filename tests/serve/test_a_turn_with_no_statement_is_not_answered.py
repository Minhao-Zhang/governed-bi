"""A turn that executed no governed statement must not record ``outcome: answered``.

Observed live on 2026-08-18. Asked something the corpus does not cover, the model wrote *"Could
you clarify what you mean by 'assets', 'actively maintained', and 'PM task'? These terms are not
defined in the provided schemas, so I can't form a reliable governed query."* — and the record
said::

    outcome    answered      terminal_reason None      generated_sql None
    execution  {"attempts": [], "terminal": "no_sql", "guardrail_errors": 0}

``stamp._path_signals``'s ``path_kind == "answered"`` branch fell through to
``return None, None, None, None, True`` — ``has_sql`` **hardcoded**, ``state["generated_sql"]``
never read — and ``classify_outcome``'s ``if has_sql: return Outcome.answered`` did the rest.

The ``True`` was load-bearing rather than a typo, which is why the fix is a new
:class:`~governed_bi.register.stages.Outcome` member and not a deleted line: with ``has_sql=False``
and no ``refused_by`` the classifier's last line returned ``crashed``, and a turn that produced
prose is not a crash. So the four tests below are a *pair of pairs*. Two pin the outcome on either
side of the statement question (:func:`test_a_prose_decline_with_no_statement_is_not_answered`,
:func:`test_a_turn_with_a_governed_statement_is_still_answered`), and two pin the things it must
not be mistaken for — the stub, which shares every signal, and a crash.

The three paths that reach here cannot be told apart from the record and this file does not try:
``Outcome.no_sql``'s docstring names all three, and a "does this prose look like a refusal"
heuristic would be a declaration with no enforcer.
"""

from __future__ import annotations

from typing import Any

from governed_bi.register.stages import Outcome, classify_outcome
from governed_bi.serve.nodes.stamp import stamp

#: The prose from the live turn, verbatim. Not asserted *on* — the fix reads the ledger, never the
#: text — but carried so a reader can see what the record called an answer.
DECLINE_PROSE = (
    "Could you clarify what you mean by 'assets', 'actively maintained', and 'PM task'? "
    "These terms are not defined in the provided schemas, so I can't form a reliable "
    "governed query."
)

_PASSING_ATTEMPT: dict[str, Any] = {
    "verdict_layer": None,
    "passed": True,
    "reason_code": "passed",
    "path": "agent",
    "executed_sql": "SELECT count(*) FROM sales.orders",
}


def _turn(**overrides: Any) -> dict[str, Any]:
    """The turn as the graph leaves it for ``stamp``: agent loop finished, nothing else set."""
    state: dict[str, Any] = {
        "path_kind": "answered",
        "turn_id": "turn-under-test",
        "turn_index": 1,
        "usage": [],
    }
    state.update(overrides)
    return state


def test_a_prose_decline_with_no_statement_is_not_answered() -> None:
    """The live turn, replayed. The two assertions are not the same assertion.

    ``!= answered`` is the defect; ``is no_sql`` is the fix. Kept apart so a change that makes
    the turn ``crashed`` — the other thing the old fall-through was protecting against — fails
    the second line and not the first, and says which of the two went wrong.
    """
    answer = stamp(
        _turn(
            execution={"attempts": [], "terminal": "no_sql", "guardrail_errors": 0},
            generated_sql=None,
            answer_text=DECLINE_PROSE,
        )
    )["answer"]

    assert answer["outcome"] != Outcome.answered.value, (
        "a turn that executed no governed statement recorded `answered`. This is the 2026-08-18 "
        "defect: `stamp._path_signals` hardcodes `has_sql=True` in the `path_kind == 'answered'` "
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
    answer = stamp(
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
    """``agent_core._stub`` writes no ``execution`` at all, and that is the trap.

    ``_path_signals`` used to read ``state["execution"]`` directly, where the stub's update leaves
    ``None``; deriving the outcome from a raw ``None`` would have classified the stub ``crashed``.
    :func:`~governed_bi.serve.nodes.stamp._execution` is the one place that substitutes
    ``execution_from_attempts(())``, so the ledger it reads is the one the record carries.

    Driven through ``_stub``'s own update rather than a hand-written dict, so the shape under test
    is the shape the node actually returns — the ``--no-model`` CLI path (``python -m
    governed_bi.serve --no-model``) reproduces it end to end.
    """
    from governed_bi.serve.nodes.agent_core import STUB_ANSWER, _stub

    update = _stub({"turn_index": 1})
    assert "execution" not in update, (
        "the stub now writes a ledger, so this test no longer covers the absent-ledger case"
    )

    answer = stamp(_turn(**{k: v for k, v in update.items() if k != "messages"}))["answer"]

    assert answer["outcome"] == Outcome.no_sql.value, (
        f"the stub path recorded {answer['outcome']!r}. `crashed` means the classification is "
        "reading a raw absent `execution` instead of `_execution`'s substitution; `answered` "
        f"means the fall-through is back. ({STUB_ANSWER} is not a governed answer either way.)"
    )
    assert answer["record"]["execution"] == {
        "attempts": [],
        "terminal": Outcome.no_sql.value,
        "guardrail_errors": 0,
    }


def test_a_turn_with_no_ledger_verdict_at_all_is_still_a_crash() -> None:
    """The case the hardcoded ``True`` was protecting, kept protected.

    ``classify_outcome`` returns :attr:`Outcome.no_sql` only when the caller hands over the
    ledger's own verdict. A turn nothing marked and nothing wrote a ledger for has not been
    observed ending, and ``crashed`` is what this repository calls that — collapsing it into
    ``no_sql`` would give every dropped turn a benign name, which is the 2026-07-25 defect
    (a crash counted as a refusal) in the other direction.
    """
    assert (
        classify_outcome(error=None, refused_by=None, has_sql=False, terminal=None)
        is Outcome.crashed
    )
    assert stamp(_turn(path_kind=None))["answer"]["outcome"] == Outcome.crashed.value


def test_a_refusal_outranks_an_empty_ledger() -> None:
    """A guard block carries ``attempts: []``, so its ledger also says ``no_sql``.

    ``register/stages._assert_refusal_tables_are_closed`` asserts this over the whole declared
    vocabulary at import; this is the end-to-end half, through ``stamp``, because the import guard
    compares declarations to declarations and never to a stamped turn. If the ledger were consulted
    before ``refused_by``, the refusal would keep its reason and its stage and only ``outcome``
    would stop saying a decision was taken — and ``eval/report.refusal_histogram`` counts
    ``refused`` rows, so the reason would silently leave the histogram.
    """
    answer = stamp(
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
