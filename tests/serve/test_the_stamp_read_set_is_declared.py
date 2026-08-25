"""``stamp``'s outcome decision reads seven named channels, and that is written down.

**The defect this closes.** ``stamp`` is the node that decides what happened to a turn, and
``(state, config) -> dict`` says nothing about which of ``ServeState``'s 47 channels it reads,
which it writes, or what clears them. That is not an abstract complaint: ``measure/gates.py``
read ``Outcome.clarification`` as a witness of "reached ``stamp``" and silently dropped every row
carrying the corpus hash out of a gate's denominator. A second reader of a channel, disagreeing
with the first, in a place nothing pointed at.

**What was measured before anything was moved.** ``stamp`` reads **32** channels *effectively* —
delete any one from a populated turn and the emitted record changes — plus ``failure_cause``,
which arrives through the register fall-through and is not a ``ServeState`` channel at all, so it
is always null. Method: delete one key at a time from each of 30 turn shapes and compare
``json.dumps`` of the output.

**Seven of the 32 are the decision's, and only those are declared.** The other 25 are the
register's: ``project`` walks ``RECORD_REGISTER`` and asks ``stamp`` for each field by name, so
that read set already exists in ``register/record.py`` and a dataclass restating it would be a
second copy of one declaration. The seam is worth drawing where a *decision* can have two
readers who disagree. So this file asserts the seven, and asserts that the derivation cannot
reach anything else — not that the whole node reads seven things, which would be false.

**What it costs, stated rather than hidden.** Eight functions across the two files still take a
state mapping: three are the decision's projection, one is the node's own seam, and four are the
register's readers. :func:`test_the_derivation_reads_no_state_dict` names all eight, so a ninth
forces a decision here instead of appearing quietly.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from governed_bi.register.stages import Outcome
from governed_bi.serve.nodes.stamp import classify_turn, stamp
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


# ── the read set, written down ─────────────────────────────────────────────────


def test_the_projection_names_every_channel_the_decision_reads() -> None:
    """Whole-object equality, so an eighth field cannot be added without this test naming it.

    ``failure`` arrives as three fields, because presence and contents were read through
    different tests before the split and a crash with no detail is still a crash. ``guard``
    arrives whole and un-narrowed — :class:`OutcomeInputs` says why at length, and
    :func:`test_a_malformed_guard_is_only_read_on_the_path_that_asks` drives it.
    """
    state = {
        "path_kind": "answered",
        "failure": {"stage": "check", "error_type": "RuntimeError"},
        "generated_sql": "select 1",
        "terminal_reason": "nothing_licensed",
        "guard": {"outcome": "blocked"},
        "clarification_requested": True,
        "execution": PASSED,
        # Present and never read by the decision: every one of these reaches the *record*, and
        # the record is the register's projection, not this view's.
        "licensed": ["sales.orders"], "schemas": ["sales"], "usage": [], "turn_id": "t-1",
    }
    assert OutcomeInputs.from_state(state) == OutcomeInputs(
        path_kind="answered",
        failed=True,
        failed_stage="check",
        error_type="RuntimeError",
        has_sql=True,
        terminal_reason="nothing_licensed",
        guard={"outcome": "blocked"},
        clarification_requested=True,
        execution=PASSED,
    )


def test_an_absent_channel_projects_to_the_field_default() -> None:
    """The empty case, and it has to be its own assertion.

    An absent channel and one recorded empty are the same value here on purpose: every field is
    a route, a flag or a record, and the decision's question of each is "is there one". The
    default view is therefore the *unmarked* turn — nothing marked it, nothing raised, no
    statement, no ledger — which classifies ``crashed``, because a turn nothing observed ending
    has not been observed ending.
    """
    empty = OutcomeInputs.from_state({})
    assert empty == OutcomeInputs(execution=empty.execution)
    assert empty.execution == {"attempts": [], "terminal": "no_sql", "guardrail_errors": 0}, (
        "an absent ledger must be substituted, not left None: the --no-model stub writes none, "
        "and a raw None classified it as a crash"
    )
    assert classify_turn(OutcomeInputs()).outcome is Outcome.crashed


def test_every_declared_channel_changes_a_verdict() -> None:
    """The declaration is not padded: each of the seven moves an outcome on its own.

    A read set is only worth writing down if every name in it is load-bearing, and the cheapest
    way for this file to become decoration is for someone to add a field the decision ignores.
    So one pair per channel, differing in that channel alone, whose verdicts differ.
    """
    from dataclasses import replace

    base = OutcomeInputs(path_kind="answered", has_sql=True, execution=PASSED)
    moves: dict[str, dict[str, Any]] = {
        "path_kind": {"path_kind": "refuse"},
        "failure": {"failed": True},
        "generated_sql": {"has_sql": False, "execution": {"attempts": [], "terminal": "no_sql"}},
        "terminal_reason": {"path_kind": "decline", "terminal_reason": "nothing_licensed"},
        "guard": {"path_kind": "refuse", "guard": {"outcome": "blocked"}},
        "clarification_requested": {"clarification_requested": True},
        "execution": {"execution": {**PASSED, "terminal": "capped"}},
    }
    assert set(moves) == DECISION_CHANNELS
    for channel, change in moves.items():
        moved = replace(base, **change)
        assert classify_turn(moved) != classify_turn(base), (
            f"{channel} is declared as a decision input and changes no verdict"
        )
    # ``guard`` needs the pair one step over: on a refuse it is what separates the two fallback
    # reasons, and nothing else in the view can say which.
    refused = OutcomeInputs(path_kind="refuse")
    assert classify_turn(refused).refused_by == "negative_example"
    assert classify_turn(replace(refused, guard={"outcome": "blocked"})).refused_by == "guard"


# ── the derivation cannot reach a state dict ───────────────────────────────────


#: Every function across the two files that takes a state mapping, and what it is for. The gate
#: below asserts this is the whole list, so a ninth reader is a decision taken here rather than
#: one that appears.
STATE_READERS: dict[str, str] = {
    "normalised": "the decision's projection: clears the three reset sentinels",
    "_execution": "the decision's projection: substitutes a ledger for a turn that wrote none",
    "from_state": "the decision's projection: the one place a channel becomes a named fact",
    "stamp": "the seam itself, and the two answer-only channels the register does not carry",
    "_usage_for_turn": "the register's: this turn's rows out of an accumulating channel",
    "_latency_sec": "the register's: a derived cost field, from turn_started_at",
    "_facet_channels": "the register's: one reader for facet_channels and facet_degraded",
    "extract": "the register's own reader, driven by RECORD_REGISTER field name",
}

#: The functions that make up the decision. None may take a state mapping or name a channel.
DERIVATION: frozenset[str] = frozenset({
    "classify_turn", "_path_signals", "_final_status", "_attempts",
})


def _trees() -> list[tuple[Path, ast.Module]]:
    from governed_bi.serve import outcome
    from governed_bi.serve.nodes import stamp as stamp_module

    paths = [
        Path(inspect.getsourcefile(module) or "").resolve()
        for module in (stamp_module, outcome)
    ]
    assert len(set(paths)) == 2, paths
    return [(p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p))) for p in paths]


def test_the_derivation_reads_no_state_dict() -> None:
    """Structural, because a behavioural check passes on the day it is written.

    Two halves. Every function in :data:`DERIVATION` takes :class:`OutcomeInputs` and nothing
    else — no ``state`` parameter, and no channel name as a string literal, so
    ``state.get("path_kind")`` cannot creep back in under a local alias. And the functions that
    *do* take a state mapping are exactly :data:`STATE_READERS`, each with a stated job.

    The second half is the honest one. ``abstain``'s equivalent gate can say "one projection and
    the adapter"; this one cannot, because four of the readers are the register's and the
    register is entitled to read the state it declares fields over. What the gate buys is that
    the list is a list: a new reader has to be added here, with a reason, and adding one is
    where a second answer to "what happened to this turn" would come from.
    """
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
    """Channel names this function pulls out of a mapping, by ``.get(...)`` or ``[...]``.

    A lookup and not a bare string literal, because one of the seven channel names is also one of
    the refusal *reasons*: ``_path_signals`` writes ``reason = "guard"`` when a blocked guard
    refused the turn, which is a value in the ``refused_by`` vocabulary and not a read of the
    ``guard`` channel. A literal-only check reported it and would have had to be waived by name,
    which is how a gate stops meaning anything. Matching the lookup instead catches
    ``state.get("guard")`` and ``state["guard"]`` — including through a local alias, since the
    alias is still a mapping — and leaves the vocabulary alone.
    """
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


def test_the_seam_normalises_before_it_projects() -> None:
    """The reset sentinels, which are the reason ``normalised`` exists at all.

    ``Session.turn`` writes ``RESET`` to ``path_kind`` and ``failure``; both annotations are
    Unions, so the channel seeds ``MISSING`` and LangGraph assigns the first write raw. Read
    unnormalised, ``state.get("failure") is not None`` was true on every successful first turn of
    a fresh thread — every one of them recorded ``crashed``.

    ``from_state`` normalises its own input, so the view is right whether or not the caller did
    it first, and ``normalised`` is idempotent so the two callers cannot disagree.
    """
    raw = {"path_kind": "reset", "failure": "reset", "facets": "reset", "execution": PASSED}
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


# ── the verdicts, stated as facts instead of turns ────────────────────────────


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
            Outcome.refused, "negative_example", "refused",
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
def test_the_verdict_for_each_path(
    what: str, inputs: OutcomeInputs, outcome: Outcome, refused_by: str | None, rail_status: str
) -> None:
    """One case per path, each stated as the two or three facts it is about.

    That is the whole return on this seam, and it is worth being concrete about how small the
    cases got: a decline used to need a turn dict with identity keys, a ledger and a facet map to
    be projectable at all, and is now ``OutcomeInputs(path_kind="decline")``. What did **not**
    get smaller is the total, because the projection became worth testing directly — the three
    tests above build state dicts, and the register projection still needs a whole turn. The seam
    relocated the setup; it did not delete it.

    ``rail_status`` is asserted here rather than in a stream test because it is carried on the
    verdict on purpose: a node's effect on the timeline is a claim of its own, and ``declined``
    against ``refused`` is the one distinction :class:`Outcome` has no member for.
    """
    verdict = classify_turn(inputs)
    assert verdict.outcome is outcome, what
    assert verdict.refused_by == refused_by, what
    assert verdict.rail_status == rail_status, what


def test_a_crash_rewrites_the_ledgers_terminal_and_keeps_its_attempts() -> None:
    """So ``outcome: crashed`` never sits beside ``execution.terminal: answered``.

    A careless reader takes that pair as an answered turn. The attempts stay — they are what
    happened — and the returned ledger is a copy, so the view the decision was handed is not
    mutated under it.
    """
    inputs = OutcomeInputs(path_kind="answered", has_sql=True, failed=True, execution=PASSED)
    verdict = classify_turn(inputs)
    assert verdict.execution["terminal"] == "crashed"
    assert verdict.execution["attempts"] == PASSED["attempts"]
    assert inputs.execution["terminal"] == "answered", "the view was mutated"


def test_a_malformed_guard_is_only_read_on_the_path_that_asks() -> None:
    """Why ``guard`` is carried whole instead of narrowed to a boolean in the projection.

    ``guard`` is declared ``Absence.never``, so a non-mapping one is a wiring failure. Narrowed
    eagerly in ``from_state`` it would become ``False`` on every turn, and a refusal would then
    be attributed to ``negative_example`` — a wrong reason in place of a loud one. Carried whole,
    the malformed value reaches only the fallback that asks, which raises, and ``stamp`` is the
    node left unwrapped so the raise is not swallowed.

    Both halves: the paths that do not ask are unaffected, and the path that asks fails.
    """
    malformed = OutcomeInputs.from_state({"path_kind": "answered", "guard": "blocked"})
    assert malformed.guard == "blocked"
    assert classify_turn(malformed).outcome is Outcome.no_sql

    with pytest.raises(AttributeError):
        classify_turn(OutcomeInputs(path_kind="refuse", guard="blocked"))


def test_the_node_still_writes_one_channel_and_one_event() -> None:
    """The seam changed no interface. ``stamp`` returns ``{"answer": ...}`` and nothing else.

    Asserted on the update rather than inferred from a green turn, and paired with the verdict
    the derivation reaches for the same state, so "the adapter translates and adds nothing" is a
    property and not a hope.
    """
    state = {
        "path_kind": "answered", "generated_sql": "select 1", "execution": PASSED,
        "answer_text": "42", "result_table": {"columns": ["n"], "rows": [[42]]},
        "turn_index": 1, "usage": [],
    }
    update = stamp(state)
    assert set(update) == {"answer"}, update

    verdict = classify_turn(OutcomeInputs.from_state(state))
    assert isinstance(verdict, TurnOutcome)
    assert update["answer"]["outcome"] == verdict.outcome.value
    assert update["answer"]["record"]["execution"] is verdict.execution or (
        update["answer"]["record"]["execution"] == verdict.execution
    )
    assert update["answer"]["answer_text"] == "42"
    assert update["answer"]["result_table"] == state["result_table"]
