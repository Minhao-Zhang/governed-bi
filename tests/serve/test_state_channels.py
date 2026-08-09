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

    # `async def`, because the facet nodes are. A sync double here does not fail loudly: it
    # returns the real node's coroutine unawaited, `wrap_node` hands that to
    # `rail_observation`, and the turn dies on `'coroutine' object has no attribute 'get'` —
    # a stack trace about the double, three frames away from anything real.
    async def node(state: dict, config: Any) -> dict:
        if on_turn is None or state.get("turn_index") == on_turn:
            raise ValueError("facet exploded")
        return await real(state, config)

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
        # A stale result table is the loudest possible carry-over: the next turn's answer would
        # render the previous turn's rows beside its own explanation.
        "result_table": {"columns": ["n"], "rows": [[1]], "row_count": 1, "truncated": False},
        "answer_text": "There are 9,590 restaurants in total.",
        # A carried-over verdict is the previous turn's opinion filed against this turn's SQL,
        # and the record would publish it as this turn's — an observer's judgement about a
        # statement it never saw.
        "reflect_verdict": {"verdict": "wrong", "reason": "counted the wrong table"},
        # A carried-over query vector would score the *previous* question's semantics against
        # this turn's candidates — a wrong ranking with nothing anywhere disagreeing.
        "query_vector": [0.1, 0.2, 0.3],
        # A carried-over clock makes turn two's `latency_sec` span turn one plus everything the
        # user did in between — the field would report a real number and mean nothing.
        "turn_started_at": 1_700_000_000.0,
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


def test_every_executor_path_is_classified_as_answering_or_introspecting():
    """A new executor path must be classified before ``terminal`` can read it.

    ``INTROSPECTION_PATHS`` is stated as the complement of "can answer the question", so a
    path added to ``EXECUTOR_PATHS`` and forgotten counts as answering. That is the right
    default — under-recording an answer is this repository's recurring failure mode — but it
    is only right if forgetting is caught. It is caught here.

    The distinction is load-bearing in three places: ``execution_from_attempts``' ``terminal``,
    ``stamp``'s outcome, and ``agent_core``'s ``generated_sql``. A ``sample`` row counted as
    answering makes a turn whose every ``run_query`` was refused record ``answered``, and makes
    a ``SELECT DISTINCT`` over one column the statement an eval re-executes as the answer.
    """
    from governed_bi.govern.ledger import EXECUTOR_PATHS
    from governed_bi.serve.ledger import INTROSPECTION_PATHS

    #: The judgement, made once. ``agent`` writes the analyst's SQL; ``graded`` is the graded
    #: delivery retry of the same statement (ADR 0006 §5), so both answer. ``sample`` and
    #: ``profile`` describe a column to the model and answer nothing.
    answering = {"agent", "graded"}
    assert answering | INTROSPECTION_PATHS == set(EXECUTOR_PATHS), (
        f"unclassified executor path(s): "
        f"{sorted(set(EXECUTOR_PATHS) - answering - INTROSPECTION_PATHS)}. Decide whether the "
        "path can answer the question; if it cannot, add it to INTROSPECTION_PATHS."
    )
    assert not (answering & INTROSPECTION_PATHS)


def test_a_turn_built_by_the_session_seam_actually_answers(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """The turn every real caller builds, through the graph, end to end.

    **This is the test whose absence let a working entry point break.** Every other fixture in
    this suite hand-builds a turn dict, so ``Session.turn`` — the only way in for
    ``python -m governed_bi.serve``, ``POST /chat`` and the LangGraph Server node — was never
    the thing under test. When ``turn()`` started writing the ``RESET`` sentinel, that sentinel
    became the **first** write to each reduced channel, and
    :func:`~governed_bi.serve.state._cleared` records what LangGraph does with a first write.
    Result: ``outcome: "crashed"`` on every turn, and 387 green tests.

    Asserted at the level a user sees, deliberately: not "the reducer normalises the sentinel"
    but "a question asked the way the CLI asks it is not reported as a crash".
    """
    from governed_bi.serve.session import from_assets

    session = from_assets(
        list(two_schema_assets.values()), connector=None, policy=guard_off_policy,
        db_id="ops_b", corpus_content_hash_="corpus-hash",
    )
    config = session.configurable()
    config["configurable"]["thread_id"] = "t-seam"
    out = graph_mod.compile_graph().invoke(
        {**session.turn("sensors voltage reading per device"), "route_top_n": 1}, config
    )

    assert out["answer"]["outcome"] != "crashed", (
        f"a turn built by Session.turn reports a crash. failed_stage="
        f"{out['answer']['record'].get('failed_stage')!r} error_type="
        f"{out['answer']['record'].get('error_type')!r} path_kind={out.get('path_kind')!r}"
    )
    assert out.get("path_kind") != RESET, (
        f"path_kind is the literal sentinel {out.get('path_kind')!r}. LangGraph assigns a "
        "channel's first value without calling the reducer, so the sentinel has to be inert "
        "wherever it lands rather than only where a reducer expects it."
    )
    assert out.get("failure") in (None, RESET) or isinstance(out.get("failure"), dict), (
        f"failure holds {out.get('failure')!r}"
    )
    # `facets` and `failure` are the two that raise rather than mis-report: `dict("reset")` and
    # `"reset".get(...)`. Reaching this line at all means neither did.
    assert isinstance(out.get("facets"), dict) and out["facets"], "the fan-out produced nothing"


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


def test_a_node_timeout_is_attached_only_where_it_can_fire() -> None:
    """The bound exists, and it is not claimed where it would be a false promise.

    A node whose *body* still runs through ``asyncio.to_thread`` only has its ``await``
    cancelled; the thread keeps going. Claiming a ceiling there reports a stop that did not
    happen, so ``wrap_node`` refuses the wiring outright and only natively-async nodes carry a
    bound.

    ``agent_core`` is the node this is for: ``llm_timeout_s`` bounds one of its provider calls,
    the loop makes several, and nothing else bounds the node.

    **The bound is no longer LangGraph's.** ``add_node(timeout=...)`` enforced it outside the
    node function, which needed an ``error_handler`` to catch — and that handler was measured
    not to save the run under the stream modes the served path uses. ``wrap_node`` owns the
    clock now, so the assertion reads the wrapper's configuration rather than the node's.
    """
    from governed_bi.serve.graph import _CANCELLABLE, _node_timeout, build_graph

    nodes = build_graph().nodes
    # Nothing carries a LangGraph node timeout any more; if one reappears, the error_handler
    # question comes back with it.
    assert not [n for n, node in nodes.items() if getattr(node, "timeout", None) is not None], (
        "a node carries a LangGraph-enforced timeout again — it cannot be caught by wrap_node "
        "and its handler does not save the run under custom/messages/subgraph streaming"
    )

    timed = {name for name in nodes if _node_timeout(name) is not None}
    assert "agent_core" not in timed, (
        "agent_core's hang-stop must live inside the node so a timeout keeps the ledger; "
        "wrap_node would discard streamed attempts"
    )
    assert _CANCELLABLE <= timed, f"a cancellable rail carries no bound: {_CANCELLABLE - timed}"
    assert "narrate" not in timed, (
        "narrate carries a node timeout again — that marks an answered turn crashed"
    )
    assert "reflect" not in timed, "reflect must stay an observer that cannot fail the turn"
    # The fan-out stays out by decision, not by constraint: moving the clock inside wrap_node
    # removed the concurrent-sibling problem, but five simultaneous bounds against a shared
    # provider quota is an unmeasured comparability change.
    assert not (timed & {name for name, _ in graph_mod._FACET_NODES}), (
        "a facet carries a timeout; that is now possible but is an unmeasured arm change"
    )
    assert "stamp" not in timed, "stamp is unwrapped and must stay so"
    for name in timed:
        assert name in _CANCELLABLE, (
            f"{name!r} claims a wrap_node timeout but its body is not natively async, so the "
            "await would be cancelled and the work would carry on in its thread"
        )


def test_the_node_timeouts_are_settable_by_a_deployment() -> None:
    """A knob reachable only from source is the defect the register exists to abolish.

    Rail bounds are read from the environment before falling back to the declared default.
    ``agent_core``'s bound is settable the same way but lives on the node, not ``wrap_node``.

    ``agent_node_timeout_s`` must also be reachable from ``knobs_resolved``, because it is
    ``Role.comparability``: it took no state at all, so two arms declaring different values
    recorded two configurations that had behaved identically. And ``0`` must mean *no wall*
    rather than a zero-second deadline that fails every turn on its first frame.
    """
    import os

    from governed_bi.register.knobs import knob_default
    from governed_bi.serve.graph import _node_timeout
    from governed_bi.serve.nodes.agent_core import _agent_node_timeout, _hang_grace

    assert _node_timeout("agent_core") is None, (
        "agent_core owns agent_node_timeout_s internally; wrap must not double-bound it"
    )
    assert _node_timeout("guard") == float(knob_default("rail_node_timeout_s"))
    assert _node_timeout("route") is None, "a to_thread node must not claim a bound"
    assert _node_timeout("facet_term") is None, (
        "a facet carries a timeout again: possible now, but an unmeasured comparability change"
    )
    assert _agent_node_timeout({}) == float(knob_default("agent_node_timeout_s"))

    # The arm-settable path, which is the whole reason the knob is Role.comparability.
    assert _agent_node_timeout({"knobs_resolved": {"agent_node_timeout_s": 55.0}}) == 55.0

    # `0` disables rather than kills. Both surfaces, because both used to parse it as a deadline.
    assert _agent_node_timeout({"knobs_resolved": {"agent_node_timeout_s": 0}}) is None

    # The grace is what lets the soft wall stay between-frames without losing the hang-stop.
    assert _hang_grace({}) == float(knob_default("llm_timeout_s"))
    assert _hang_grace({"knobs_resolved": {"llm_timeout_s": 42.0}}) == 42.0

    os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"] = "12.5"
    os.environ["GOVERNED_BI_RAIL_NODE_TIMEOUT_S"] = "7"
    try:
        assert _agent_node_timeout({}) == 12.5
        # Env still wins over a resolved knob, as it does for every other node bound.
        assert _agent_node_timeout({"knobs_resolved": {"agent_node_timeout_s": 55.0}}) == 12.5
        assert _node_timeout("guard") == 7.0
        os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"] = "0"
        assert _agent_node_timeout({}) is None, "'0' from a deployment must mean off, not 0s"
        os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"] = ""
        assert _agent_node_timeout({}) == float(knob_default("agent_node_timeout_s")), (
            "an empty env var is unset, not zero"
        )
    finally:
        del os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"]
        del os.environ["GOVERNED_BI_RAIL_NODE_TIMEOUT_S"]
