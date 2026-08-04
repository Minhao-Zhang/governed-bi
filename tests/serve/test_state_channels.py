"""What a state channel must do when a turn ends and the next one begins.

**Every test here passed nothing before 2026-08-04 — it could not, because the failures it
covers are not exceptions any node raises.** Three of them are silent: a channel read by the
turn after the one that wrote it. One is loud in the wrong place: ``InvalidUpdateError``
raised by the channel itself, after the nodes have returned, where ``wrap_node`` is no longer
on the stack and ``stamp`` never runs.

That is the shape worth naming. The suite was green through all four, so a green suite was
evidence about the nodes and not about the graph. These tests fail against the reducers and
the ``turn()`` reset removed.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.serve import graph as graph_mod
from governed_bi.serve.state import (
    ACCUMULATING,
    PER_TURN_RESET,
    RESET,
    TEST_HOOKS,
    TURN_IDENTITY,
    ServeState,
    merge_facets,
    settle_failure,
    settle_path_kind,
)

CRASHING_FACETS = ("facet_term", "facet_metric")


def _turn(**overrides: Any) -> dict[str, Any]:
    """A turn with the fifteen fields the record requires. Same shape as F's fixtures."""
    payload: dict[str, Any] = {
        "question": "customers", "thread_id": "thread-channels", "turn_index": 1,
        "run_id": "run-channels", "turn_id": "turn-channels", "question_id": "q-channels",
        "db_id": "sales_a", "attempt_id": "attempt-channels", "n_re_served": 0,
        "corpus_content_hash": "corpus-hash", "prompt_set_hash": "prompt-hash",
        "knobs_resolved": {"route_top_n": 1}, "messages": [], "usage": [], "route_top_n": 1,
    }
    payload.update(overrides)
    return payload


def _crash(real: Any, *, on_turn: int | None) -> Any:
    """A facet node that raises, on one turn index or on all of them."""

    def node(state: dict, config: Any) -> dict:
        if on_turn is None or state.get("turn_index") == on_turn:
            raise ValueError("facet exploded")
        return real(state, config)

    return node


@pytest.fixture
def crashing_facets(monkeypatch):
    """Make two of the five facet nodes raise. ``on_turn`` selects which turns."""

    def install(*, on_turn: int | None = None) -> None:
        patched = tuple(
            (name, _crash(fn, on_turn=on_turn) if name in CRASHING_FACETS else fn)
            for name, fn in graph_mod._FACET_NODES
        )
        monkeypatch.setattr(graph_mod, "_FACET_NODES", patched)

    return install


def _config(index: Any, assets: dict[str, Any], policy: Any, thread: str) -> dict[str, Any]:
    return {"configurable": {
        "thread_id": thread, "policy": policy, "index": index, "assets_by_id": assets,
    }}


# ── the loud one: a channel raising where nothing can catch it ────────────────


def test_two_facets_crashing_in_one_super_step_still_produces_a_record(
    crashing_facets, two_schema_index, two_schema_assets, guard_off_policy
):
    """Two concurrent writes to one channel must not take the whole turn out.

    The five facet nodes run in a single super-step, and ``wrap.py`` turns any exception into
    ``{"failure": ..., "path_kind": "crashed"}`` — so two of them failing means two writes to
    ``failure``, which had no reducer. LangGraph raises ``InvalidUpdateError: At key
    'failure': Can receive only one value per step`` **from the channel**, after both nodes
    have returned successfully. ``wrap_node`` is off the stack, ``stamp`` never runs, and the
    turn produces no record of any kind: the single failure mode the wrapper exists to
    prevent, reached through the wrapper working correctly.
    """
    crashing_facets()
    out = graph_mod.compile_graph().invoke(
        _turn(),
        _config(two_schema_index, two_schema_assets, guard_off_policy, "t-concurrent"),
    )

    answer = out.get("answer")
    assert answer is not None, (
        "the turn ended with no answer at all. Two facet nodes crashed in one super-step and "
        "the un-reduced 'failure' channel raised InvalidUpdateError after they returned, "
        "which is past the point wrap_node can catch anything."
    )
    assert answer["outcome"] == "crashed", answer
    assert answer["record"]["failed_stage"] in CRASHING_FACETS, answer["record"]


def test_the_concurrently_dropped_failure_is_named_rather_than_discarded(
    crashing_facets, two_schema_index, two_schema_assets, guard_off_policy
):
    """``failed_stage`` holds one stage, so the second crash must still be legible.

    The register has one ``failed_stage`` field and two facets crashed, so one of the two is
    genuinely not representable there. Dropping it silently would be the reportable-state-
    treated-as-nothing shape this repository keeps re-finding, so ``settle_failure`` appends
    it to ``detail`` — free text that already reaches the record.
    """
    crashing_facets()
    out = graph_mod.compile_graph().invoke(
        _turn(),
        _config(two_schema_index, two_schema_assets, guard_off_policy, "t-dropped"),
    )
    failure = out.get("failure") or {}
    detail = str(failure.get("detail") or "")
    other = [name for name in CRASHING_FACETS if name != failure.get("stage")]
    assert other[0] in detail, (
        f"two facets crashed and the record names only {failure.get('stage')!r}; "
        f"detail={detail!r} does not mention the other one"
    )


# ── the silent ones: a channel outliving its turn ─────────────────────────────


def test_a_crash_is_not_erased_by_the_node_that_runs_after_the_fan_in(
    crashing_facets, two_schema_index, two_schema_assets, guard_off_policy
):
    """``route`` is the fan-in, so it runs after a facet crash — and must not proceed.

    It had no terminal guard (every node downstream of it has one) and it wrote
    ``"path_kind": None`` unconditionally. Together that erased the crash outright: routing,
    reference closure, join connection, context assembly and a full billed model call all ran
    on a turn that had already failed, and ``stamp`` then recorded the crash as though nothing
    had happened in between. The observable consequence is a usage row.
    """
    crashing_facets()
    out = graph_mod.compile_graph().invoke(
        _turn(),
        _config(two_schema_index, two_schema_assets, guard_off_policy, "t-erased"),
    )

    assert out.get("path_kind") == "crashed", (
        f"path_kind is {out.get('path_kind')!r}: route erased the crash mark by writing None "
        "over it, and everything downstream ran on a failed turn."
    )
    assert not (out.get("usage") or []), (
        f"usage={out.get('usage')} — the agent node ran on a crashed turn. That is a paid "
        "model call bought after the turn was already lost."
    )
    assert not out.get("schemas"), f"routing selected {out.get('schemas')} on a crashed turn"


def test_the_turn_after_a_crashed_turn_is_still_servable(
    crashing_facets, two_schema_index, two_schema_assets, guard_off_policy
):
    """A crash must not make the whole thread unservable.

    ``path_kind`` outlives its turn under a checkpointer and nothing cleared it, so a crashed
    turn 1 left ``"crashed"`` in the channel — and ``_after_guard`` reads exactly that key to
    decide whether to skip straight to ``stamp``. Turn 2 was therefore routed to ``stamp``
    before ``rewrite``, ``negative_gate`` or the fan-out ran, and stamped turn 1's failure as
    its own. Every later turn of that conversation did the same. This is the multi-turn REST
    path, which is live.
    """
    crashing_facets(on_turn=1)
    compiled = graph_mod.compile_graph()
    config = _config(two_schema_index, two_schema_assets, guard_off_policy, "t-next")

    first = compiled.invoke(_turn(turn_index=1, turn_id="turn-1"), config)
    assert first["answer"]["outcome"] == "crashed", "precondition: turn 1 really crashed"

    second = compiled.invoke(
        _turn(turn_index=2, turn_id="turn-2", **{k: v for k, v in PER_TURN_RESET.items()}),
        config,
    )
    record = second["answer"]["record"]
    assert record["failed_stage"] is None, (
        f"turn 2 reports failed_stage={record['failed_stage']!r}, which is turn 1's crash. "
        "The channel was never cleared, so the thread could not be served again."
    )
    assert second["answer"]["outcome"] != "crashed", second["answer"]


def test_turn_clears_every_per_turn_channel_through_the_real_reducers(guard_off_policy):
    """The reset has to survive the reducers, not just appear in the dict.

    Three of these channels are reduced, and a reducer is free to ignore an update: writing
    ``{}`` to ``facets`` is a no-op under ``merge_facets``, and writing ``None`` to
    ``path_kind`` is a no-op under ``settle_path_kind`` — deliberately, because that is what
    stops a node erasing a crash. So the reset is a **sentinel**, and asserting that
    ``turn()`` mentions the key would not show that the value clears anything. This applies
    each reducer by hand.
    """
    reducers = {"facets": merge_facets, "path_kind": settle_path_kind, "failure": settle_failure}
    stale: dict[str, Any] = {
        "path_kind": "crashed",
        "failure": {"stage": "facet_term", "error_type": "ValueError"},
        "facets": {"schema": {"facet": "schema", "queries": [], "hits": [1], "channels": {}}},
        "negative": {"outcome": "hit", "tau": 0.5, "top_score": 0.9, "matched_id": "neg-1"},
        "guard": {"outcome": "blocked"}, "rewrite": {"outcome": "rewritten"},
        "retrieved": {"by_type": {"table": ["sales_a.customers"]}},
        "delivery": {"context_hash": "stale"}, "execution": {"attempts": [1], "terminal": "refused"},
        "answer": {"outcome": "crashed"}, "generated_sql": "SELECT 1",
        "terminal_reason": "missing_join_path", "schemas": ["ops_b"], "crossings": [{}],
        "licensed": ["ops_b.sensors"], "clarification_requested": True,
    }
    assert set(stale) == set(PER_TURN_RESET), (
        "this fixture must cover exactly the declared per-turn set; "
        f"missing={set(PER_TURN_RESET) - set(stale)} extra={set(stale) - set(PER_TURN_RESET)}"
    )

    for name, reset in PER_TURN_RESET.items():
        reducer = reducers.get(name)
        settled = reducer(stale[name], reset) if reducer else reset
        assert not settled, (
            f"{name!r} still holds {settled!r} after the reset. The next turn of this thread "
            "reads it and stamps it into its own record."
        )
        assert settled != RESET, f"{name!r} leaked the reset sentinel into state"


# ── the classification that keeps the reset honest ────────────────────────────


def test_every_declared_channel_is_classified_as_per_turn_or_not():
    """A new channel must be classified before it can be forgotten.

    ``PER_TURN_RESET`` is a list of names, and a list of names drifts — which is what ADR
    0005 §6's "no hand-maintained field lists" is about. It cannot be *derived*, because
    nothing in a ``TypedDict`` says whether a channel accumulates on purpose
    (``usage``) or leaks (``negative``); that is a judgement. So the judgement is made once
    per channel and this test refuses the alternative: adding a channel to ``ServeState``
    without deciding fails here, rather than silently defaulting to "leaks".
    """
    declared = set(ServeState.__annotations__)
    classified = set(PER_TURN_RESET) | ACCUMULATING | TURN_IDENTITY | TEST_HOOKS
    unclassified = declared - classified
    assert not unclassified, (
        f"{sorted(unclassified)} are declared channels that no category claims. Decide: "
        "PER_TURN_RESET (cleared by turn()), ACCUMULATING (survives on purpose, carries a "
        "turn key), TURN_IDENTITY (written by turn()), or TEST_HOOKS (set by a caller)."
    )
    invented = classified - declared
    assert not invented, f"{sorted(invented)} are classified but not declared on ServeState"


def test_session_turn_writes_the_reset(guard_off_policy):
    """The seam that mints a turn is the seam that clears the last one."""
    from governed_bi.serve.session import Session

    session = Session(
        index=None, structure=None, assets_by_id={}, corpus=None, connector=None,
        policy=guard_off_policy, corpus_content_hash="h", prompt_set_hash="p",
        knobs_resolved={}, db_id="sales_a", run_id="run-1",
    )
    turn = session.turn("customers", turn_index=2)
    for name, reset in PER_TURN_RESET.items():
        assert turn.get(name, "__absent__") == reset, (
            f"turn() does not clear {name!r}; it is a per-turn channel that outlives its turn"
        )


# ── the recorder itself ───────────────────────────────────────────────────────


def test_a_failure_in_stamp_is_not_swallowed(
    monkeypatch, two_schema_index, two_schema_assets, guard_off_policy
):
    """``stamp`` is the one node that must not be wrapped.

    ``wrap_node`` converts an exception into state for the *next* node to record, and for
    every other node that next node is ``stamp``. There is nothing after ``stamp``, so
    wrapping it turned "the recorder crashed" into a run that returned a state with no
    ``answer`` key and no reason — and a caller reading ``out["answer"]["record"]`` got a
    ``KeyError`` several frames from the cause.
    """
    def boom(state):
        raise RuntimeError("stamp exploded")

    monkeypatch.setattr(graph_mod, "stamp", boom)
    with pytest.raises(RuntimeError, match="stamp exploded"):
        graph_mod.compile_graph().invoke(
            _turn(),
            _config(two_schema_index, two_schema_assets, guard_off_policy, "t-stamp"),
        )
