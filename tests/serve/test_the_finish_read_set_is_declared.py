"""``finish`` decides the outcome from a declared set of channels.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from governed_bi.registry.stages import Outcome
from governed_bi.serve.nodes.finish import classify_turn, finish
from governed_bi.serve.outcome import OutcomeInputs, TurnOutcome, normalised

#: The seven channels the outcome decision reads. Asserted against the projection below in both
#: directions, so this tuple cannot drift into being a comment.
DECISION_CHANNELS: frozenset[str] = frozenset({
    "path_kind", "failure", "generated_sql", "terminal_reason", "guard",
    "clarification_requested", "execution",
})

#: A ledger with one passing answering attempt — the shape ``execution_from_attempts`` returns
#: for a turn that ran a statement and got it through the layers.
PASSED = {
    "attempts": [{"attempt": 1, "sql": "select 1", "passed": True, "kind": "answering"}],
    "terminal": "answered", "guardrail_errors": 0,
}




def test_the_projection_names_every_channel_the_decision_reads() -> None:
    """Whole-object equality, so an eighth field cannot be added without this test naming it."""
    state = {
        "path_kind": "answered",
        "failure": {"stage": "check", "error_type": "RuntimeError"},
        "generated_sql": "select 1",
        "terminal_reason": "nothing_retrieved",
        "guard": {"outcome": "blocked"},
        "clarification_requested": True,
        "execution": PASSED,
        # Present and never read by the decision: every one of these reaches the *record*, and
        # the record is the register's projection, not this view's.
        "retrieved_tables": ["sales.orders"], "schemas": ["sales"], "usage": [], "turn_id": "t-1",
    }
    assert OutcomeInputs.from_state(state) == OutcomeInputs(
        path_kind="answered",
        failed=True,
        failed_stage="check",
        error_type="RuntimeError",
        has_sql=True,
        terminal_reason="nothing_retrieved",
        guard={"outcome": "blocked"},
        clarification_requested=True,
        execution=PASSED,
    )


def test_an_absent_channel_projects_to_the_field_default() -> None:
    """The empty case, and it has to be its own assertion."""
    empty = OutcomeInputs.from_state({})
    assert empty == OutcomeInputs(execution=empty.execution)
    assert empty.execution == {"attempts": [], "terminal": "no_sql", "guardrail_errors": 0}, (
        "an absent ledger must be substituted, not left None: the --no-model stub writes none, "
        "and a raw None classified it as a crash"
    )
    assert classify_turn(OutcomeInputs()).outcome is Outcome.crashed


def test_every_declared_channel_changes_a_result() -> None:
    """The declaration is not padded: each of the seven moves an outcome on its own."""
    from dataclasses import replace

    base = OutcomeInputs(path_kind="answered", has_sql=True, execution=PASSED)
    moves: dict[str, dict[str, Any]] = {
        "path_kind": {"path_kind": "refuse"},
        "failure": {"failed": True},
        "generated_sql": {"has_sql": False, "execution": {"attempts": [], "terminal": "no_sql"}},
        "terminal_reason": {"path_kind": "decline", "terminal_reason": "nothing_retrieved"},
        "guard": {"path_kind": "refuse", "guard": {"outcome": "blocked"}},
        "clarification_requested": {"clarification_requested": True},
        "execution": {"execution": {**PASSED, "terminal": "capped"}},
    }
    assert set(moves) == DECISION_CHANNELS
    for channel, change in moves.items():
        moved = replace(base, **change)
        assert classify_turn(moved) != classify_turn(base), (
            f"{channel} is declared as a decision input and changes no result"
        )
    # ``guard`` needs the pair one step over: on a refuse it is what separates the two fallback
    # reasons, and nothing else in the view can say which.
    refused = OutcomeInputs(path_kind="refuse")
    assert classify_turn(refused).refused_by == "guard"
    assert classify_turn(replace(refused, guard={"outcome": "blocked"})).refused_by == "guard"




#: Every function across the two files that takes a state mapping, and what it is for. The check
#: below asserts this is the whole list, so a ninth reader is a decision taken here rather than
#: one that appears.
STATE_READERS: dict[str, str] = {
    "normalised": "the decision's projection: clears the three reset sentinels",
    "_execution": "the decision's projection: substitutes a ledger for a turn that wrote none",
    "from_state": "the decision's projection: the one place a channel becomes a named fact",
    "finish": "the seam itself, and the two answer-only channels the register does not carry",
    "_usage_for_turn": "the register's: this turn's rows out of an accumulating channel",
    "_latency_sec": "the register's: a derived cost field, from turn_started_at",
    "_recorded_channels": "the register's: one reader for channel_scoring and channel_degraded",
    "extract": "the register's own reader, driven by RECORD_REGISTER field name",
}

#: The functions that make up the decision. None may take a state mapping or name a channel.
DERIVATION: frozenset[str] = frozenset({
    "classify_turn", "_path_signals", "_final_status", "_attempts",
})


def _trees() -> list[tuple[Path, ast.Module]]:
    from governed_bi.serve import outcome
    from governed_bi.serve.nodes import finish as finish_module

    paths = [
        Path(inspect.getsourcefile(module) or "").resolve()
        for module in (finish_module, outcome)
    ]
    assert len(set(paths)) == 2, paths
    return [(p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p))) for p in paths]


def test_the_derivation_reads_no_state_dict() -> None:
    """Structural, because a behavioural check passes on the day it is written."""
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in functions, f"{node.name} is defined twice ({path})"
                functions[node.name] = node

    assert DERIVATION <= set(functions), sorted(DERIVATION - set(functions))
    takes_state = {
        name for name, fn in functions.items()
        if any(arg.arg == "state" for arg in fn.args.args)
    }
    assert takes_state == set(STATE_READERS), (
        f"unnamed state readers: {sorted(takes_state - set(STATE_READERS))}; "
        f"named but gone: {sorted(set(STATE_READERS) - takes_state)}"
    )

    for name in sorted(DERIVATION):
        looked_up = _channels_looked_up(functions[name])
        assert not looked_up, (
            f"{name} looks up {sorted(looked_up)} on a mapping. The derivation takes "
            "OutcomeInputs; a channel lookup in it is a second reader of the state dict"
        )


def _channels_looked_up(fn: ast.AST) -> set[str]:
    """Channel names this function pulls out of a mapping, by ``.get(...)`` or ``[...]``."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.add(node.args[0].value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            out.add(node.slice.value)
    return out & DECISION_CHANNELS


def test_the_boundary_normalises_before_it_projects() -> None:
    """The reset sentinels, which are the reason ``normalised`` exists at all."""
    raw = {"path_kind": "reset", "failure": "reset", "channels": "reset", "execution": PASSED}
    assert OutcomeInputs.from_state(raw) == OutcomeInputs.from_state(normalised(raw))
    assert normalised(normalised(raw)) == normalised(raw)

    # The regression itself: a successful turn writes no `failure`, so the bare sentinel made
    # `failure is not None` true and every first turn of a fresh thread recorded `crashed`.
    answered = OutcomeInputs.from_state({**raw, "path_kind": "answered", "generated_sql": "s"})
    assert answered.failed is False
    assert classify_turn(answered).outcome is Outcome.answered

    view = OutcomeInputs.from_state(raw)
    assert view.path_kind is None and view.failed is False
    # Still `crashed`, and for the other reason: nothing marked this turn at all. The two causes
    # are worth keeping apart — `failed_stage` names one and is null for the other.
    assert classify_turn(view).outcome is Outcome.crashed
    assert classify_turn(view).failed_stage is None




@pytest.mark.parametrize(
    ("what", "inputs", "outcome", "refused_by", "rail_status"),
    [
        (
            "a statement that passed",
            OutcomeInputs(path_kind="answered", has_sql=True, execution=PASSED),
            Outcome.answered, None, "ok",
        ),
        (
            "a refusal that named no reason and no blocked guard",
            OutcomeInputs(path_kind="refuse"),
            Outcome.refused, "guard", "refused",
        ),
        (
            "a decline that named no reason",
            OutcomeInputs(path_kind="decline"),
            Outcome.refused, "no_schema_matched", "declined",
        ),
        (
            "a clarification",
            OutcomeInputs(path_kind="decline", clarification_requested=True),
            Outcome.clarification, "no_schema_matched", "declined",
        ),
        (
            "the cap, over a passing attempt",
            OutcomeInputs(
                path_kind="answered", has_sql=True, execution={**PASSED, "terminal": "capped"}
            ),
            Outcome.capped, "attempt_cap", "cap",
        ),
        (
            "a finished loop that ran no governed statement",
            OutcomeInputs(path_kind="answered", execution={"attempts": [], "terminal": "no_sql"}),
            Outcome.no_sql, None, "ok",
        ),
        (
            "a crash after a statement passed",
            OutcomeInputs(path_kind="answered", has_sql=True, failed=True, execution=PASSED),
            Outcome.crashed, None, "error",
        ),
        (
            "a turn nothing marked",
            OutcomeInputs(),
            Outcome.crashed, None, "error",
        ),
    ],
)
def test_the_result_for_each_path(
    what: str, inputs: OutcomeInputs, outcome: Outcome, refused_by: str | None, rail_status: str
) -> None:
    """One case per path, each stated as the two or three facts it is about."""
    result = classify_turn(inputs)
    assert result.outcome is outcome, what
    assert result.refused_by == refused_by, what
    assert result.rail_status == rail_status, what


def test_a_crash_rewrites_the_ledgers_terminal_and_keeps_its_attempts() -> None:
    """So ``outcome: crashed`` never sits beside ``execution.terminal: answered``."""
    inputs = OutcomeInputs(path_kind="answered", has_sql=True, failed=True, execution=PASSED)
    result = classify_turn(inputs)
    assert result.execution["terminal"] == "crashed"
    assert result.execution["attempts"] == PASSED["attempts"]
    assert inputs.execution["terminal"] == "answered", "the view was mutated"


def test_a_malformed_guard_is_only_read_on_the_path_that_asks() -> None:
    """Why ``guard`` is carried whole instead of narrowed to a boolean in the projection."""
    malformed = OutcomeInputs.from_state({"path_kind": "answered", "guard": "blocked"})
    assert malformed.guard == "blocked"
    assert classify_turn(malformed).outcome is Outcome.no_sql

    with pytest.raises(AttributeError):
        classify_turn(OutcomeInputs(path_kind="refuse", guard="blocked"))


def test_the_node_still_writes_one_channel_and_one_event() -> None:
    """The boundary changed no interface."""
    state = {
        "path_kind": "answered", "generated_sql": "select 1", "execution": PASSED,
        "answer_text": "42", "result_table": {"columns": ["n"], "rows": [[42]]},
        "turn_index": 1, "usage": [],
    }
    update = finish(state)
    assert set(update) == {"answer"}, update

    result = classify_turn(OutcomeInputs.from_state(state))
    assert isinstance(result, TurnOutcome)
    assert update["answer"]["outcome"] == result.outcome.value
    assert update["answer"]["record"]["execution"] is result.execution or (
        update["answer"]["record"]["execution"] == result.execution
    )
    assert update["answer"]["answer_text"] == "42"
    assert update["answer"]["result_table"] == state["result_table"]
