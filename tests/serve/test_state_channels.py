"""A state channel must reset, accumulate, or settle when the next turn begins.

Membership in ``PER_TURN_RESET`` is not the same as the reset reaching the channel.
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
    merge_channels,
    merge_delta,
    settle_failure,
    settle_path_kind,
)

CRASHING_CHANNELS = ("channel_term", "channel_metric")


def _turn(**overrides: Any) -> dict[str, Any]:
    """A turn with the fifteen fields the record requires. Same shape as F's fixtures."""
    payload: dict[str, Any] = {
        "question": "customers", "thread_id": "thread-channels", "turn_index": 1,
        "run_id": "run-channels", "turn_id": "turn-channels", "question_id": "q-channels",
        "db_id": "sales_a", "attempt_id": "attempt-channels", "n_re_served": 0,
        "corpus_content_hash": "corpus-hash", "prompt_set_hash": "prompt-hash",
        "settings_resolved": {"route_top_n": 1}, "messages": [], "usage": [], "route_top_n": 1,
    }
    payload.update(overrides)
    return payload


def _crash(real: Any, *, on_turn: int | None) -> Any:
    """A channel node that raises, on one turn index or on all of them."""

    # `async def`, because the channel nodes are. A sync double here does not fail loudly: it
    # returns the real node's coroutine unawaited, `wrap_node` hands that to
    # `rail_observation`, and the turn dies on `'coroutine' object has no attribute 'get'` —
    async def node(state: dict, config: Any) -> dict:
        if on_turn is None or state.get("turn_index") == on_turn:
            raise ValueError("retrieval channel exploded")
        return await real(state, config)

    return node


@pytest.fixture
def crashing_channels(monkeypatch):
    """Make two of the five channel nodes raise. ``on_turn`` selects which turns."""

    def install(*, on_turn: int | None = None) -> None:
        patched = tuple(
            (name, _crash(fn, on_turn=on_turn) if name in CRASHING_CHANNELS else fn)
            for name, fn in graph_mod._CHANNEL_NODES
        )
        monkeypatch.setattr(graph_mod, "_CHANNEL_NODES", patched)

    return install


def _config(index: Any, assets: dict[str, Any], policy: Any, thread: str) -> dict[str, Any]:
    return {"configurable": {
        "thread_id": thread, "policy": policy, "index": index, "assets_by_id": assets,
    }}




def test_two_channels_crashing_in_one_super_step_still_produces_a_record(
    crashing_channels, two_schema_index, two_schema_assets, guard_off_policy
):
    """Two concurrent writes to one channel must not take the whole turn out."""
    crashing_channels()
    out = graph_mod.compile_graph().invoke(
        _turn(),
        _config(two_schema_index, two_schema_assets, guard_off_policy, "t-concurrent"),
    )

    answer = out.get("answer")
    assert answer is not None, (
        "the turn ended with no answer at all. Two retrieval-channel nodes crashed in one super-step and "
        "the un-reduced 'failure' channel raised InvalidUpdateError after they returned, "
        "which is past the point wrap_node can catch anything."
    )
    assert answer["outcome"] == "crashed", answer
    assert answer["record"]["failed_stage"] in CRASHING_CHANNELS, answer["record"]


def test_the_concurrently_dropped_failure_is_named_rather_than_discarded(
    crashing_channels, two_schema_index, two_schema_assets, guard_off_policy
):
    """``failed_stage`` holds one stage, so the second crash must still be legible."""
    crashing_channels()
    out = graph_mod.compile_graph().invoke(
        _turn(),
        _config(two_schema_index, two_schema_assets, guard_off_policy, "t-dropped"),
    )
    failure = out.get("failure") or {}
    detail = str(failure.get("detail") or "")
    other = [name for name in CRASHING_CHANNELS if name != failure.get("stage")]
    assert other[0] in detail, (
        f"two retrieval channels crashed and the record names only {failure.get('stage')!r}; "
        f"detail={detail!r} does not mention the other one"
    )




def test_a_crash_is_not_erased_by_the_node_that_runs_after_the_fan_in(
    crashing_channels, two_schema_index, two_schema_assets, guard_off_policy
):
    """``route`` is the fan-in, so it runs after a channel crash — and must not proceed."""
    crashing_channels()
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
    crashing_channels, two_schema_index, two_schema_assets, guard_off_policy
):
    """A crash must not make the whole thread unservable."""
    crashing_channels(on_turn=1)
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
    """The reset has to survive the reducers, not just appear in the dict."""
    reducers = {
        "channels": merge_channels,
        "path_kind": settle_path_kind,
        "failure": settle_failure,
        # that did not clear would carry turn one's `evicted` and `budget_dropped` into turn
        # two and report drops that never happened.
        "retrieved": merge_delta,
        "delivery": merge_delta,
    }
    stale: dict[str, Any] = {
        "path_kind": "crashed",
        "failure": {"stage": "channel_term", "error_type": "ValueError"},
        "channels": {"schema": {"channel": "schema", "queries": [], "hits": [1], "channels": {}}},
        # A carried-over abstention result is the previous turn's decision, with the previous
        # turn's evidence, filed against this turn's question — and because the policy writes
        # its reason into `terminal_reason`, a stale `withhold` explains a decline this turn
        "abstention": {
            "policy": "context_sufficiency_v1", "outcome": "withhold",
            "reason": "nothing_retrieved", "rules_evaluated": [], "evidence": {},
        },
        "guard": {"outcome": "blocked"}, "rewrite": {"outcome": "rewritten"},
        "retrieved": {"by_type": {"table": ["sales_a.customers"]}},
        "delivery": {"context_hash": "stale"}, "execution": {"attempts": [1], "terminal": "refused"},
        "answer": {"outcome": "crashed"}, "generated_sql": "SELECT 1",
        # A stale result table is the loudest possible carry-over: the next turn's answer would
        # render the previous turn's rows beside its own explanation.
        "result_table": {"columns": ["n"], "rows": [[1]], "row_count": 1, "truncated": False},
        "answer_text": "There are 9,590 restaurants in total.",
        # A carried-over result is the previous turn's opinion filed against this turn's SQL,
        # and the record would publish it as this turn's — an observer's judgement about a
        # statement it never saw.
        "query_vector": [0.1, 0.2, 0.3],
        # A carried-over clock makes turn two's `latency_sec` span turn one plus everything the
        # user did in between — the field would report a real number and mean nothing.
        "turn_started_at": 1_700_000_000.0,
        "terminal_reason": "missing_join_path", "schemas": ["ops_b"], "crossings": [{}],
        # A carried-over pin routes turn two to turn one's schemas and *succeeds*, so the run
        # produces a plausible answer against the wrong database with nothing recording that
        # routing never ran. Eval-only, which is why it has to be reset here rather than
        "pinned_schemas": ["ops_b"],
        "retrieved_tables": ["ops_b.sensors"], "clarification_requested": True,
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
            "reads it and writes it into its own record."
        )
        assert settled != RESET, f"{name!r} leaked the reset sentinel into state"




def test_the_budget_evidence_reaches_finish(
    monkeypatch, two_schema_index, two_schema_assets, guard_off_policy
):
    """Copying ``retrieved`` must keep the budget fields pass two adds beside the declared keys."""
    seen: list[dict[str, Any]] = []
    real_finish = graph_mod.finish

    def spy(state):
        seen.append(dict(state))
        return real_finish(state)

    monkeypatch.setattr(graph_mod, "finish", spy)
    out = graph_mod.compile_graph().invoke(
        # `route_top_n=1` keeps the shortlist on `sales_a`, which holds 15 tables against a
        # register cap of 8 — so the cap has to bite, rather than happening to.
        _turn(question="customer account for registered buyers"),
        _config(two_schema_index, two_schema_assets, guard_off_policy, "t-budget"),
    )

    routed = out.get("retrieved") or {}
    assert routed.get("budget_dropped", {}).get("table"), (
        "precondition: the table cap did not discard anything on this fixture, so this test "
        f"would pass with the witness deleted. by_type={sorted(routed.get('by_type') or {})}"
    )

    assert len(seen) == 1, "the spy must see exactly one finish"
    evidence = (seen[0].get("retrieved") or {}).get("budget_dropped")
    assert evidence == routed["budget_dropped"], (
        "the cap's witness did not survive to finish: resolve/connect rebuilt `retrieved` from "
        f"their own key list and dropped it. finish saw {evidence!r}"
    )
    assert (seen[0].get("retrieved") or {}).get("budget_best_dropped_score", {}).get("table"), (
        "the count survived and the best surviving-nothing score did not; a drop at 0.97 and a "
        "drop at 0.01 want opposite decisions and the count alone cannot tell them apart"
    )

    # The keys `resolve` and `connect` do decide are still theirs, so the merge rule did not
    # turn a narrowing write into an additive one.
    assert "pulled_in" in (seen[0].get("retrieved") or {})

    # **And it reaches the record, which is the only artifact anything downstream opens.**
    # Surviving to `finish` was one super-step, not a destination: `finish` projects a named list
    # `registry/record.py`, so the evidence was live in state and absent from every turn record,
    record = out["answer"]["record"]
    assert record["budget_dropped"] == routed["budget_dropped"], (
        "the cap's witness reached `finish` and not the record. `register/citations.py` states "
        "the requirement it exists for: a cap can discard a gold table, and without this in an "
        f"artifact the miss reads as retrieval never having found it. record={record!r}"
    )
    assert record["budget_best_dropped_score"], (
        "the count is in the record and the score is not; a drop at 0.97 and a drop at 0.01 "
        "want opposite decisions and the count alone cannot tell them apart"
    )


def test_the_budget_evidence_is_null_rather_than_absent_when_no_cap_bit(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """The other half, and the one that keeps the field."""
    from governed_bi.registry.record import record_keys

    out = graph_mod.compile_graph().invoke(
        # `route_top_n=1` and a question that reaches few tables: the caps must *not* bite.
        _turn(question="sensors"),
        _config(two_schema_index, two_schema_assets, guard_off_policy, "t-no-budget"),
    )
    routed = out.get("retrieved") or {}
    assert "budget_dropped" not in routed, (
        "precondition: a cap bit on this fixture, so this test is not measuring the under-budget "
        f"path. by_type={ {k: len(v) for k, v in (routed.get('by_type') or {}).items()} }"
    )

    record = out["answer"]["record"]
    assert {"budget_dropped", "budget_best_dropped_score"} <= record_keys(), (
        "the budget witness is not declared in the record register, so `project()` cannot emit "
        "it and no artifact can carry it"
    )
    assert record["budget_dropped"] is None and record["budget_best_dropped_score"] is None
    assert set(record) == record_keys(), sorted(set(record) ^ record_keys())


def test_the_two_delta_channels_are_reduced() -> None:
    """``retrieved`` and ``delivery`` carry :func:`merge_delta`, read off the annotation."""
    from typing import Annotated, get_args, get_origin, get_type_hints

    hints = get_type_hints(ServeState, include_extras=True)
    for name in ("retrieved", "delivery"):
        annotation = hints[name]
        assert get_origin(annotation) is Annotated, (
            f"`{name}` is no longer a reduced channel. LangGraph's default takes the last write "
            "whole, so a node writing what it changed silently deletes every key it did not "
            "name — which is how `budget_dropped` died one super-step after it was written, on "
            "every turn that hit a cap."
        )
        assert merge_delta in get_args(annotation)[1:], (
            f"`{name}` is reduced by something other than merge_delta: "
            f"{get_args(annotation)[1:]}. The rule is right-wins-per-top-level-key; a deeper "
            "merge would make `connect`'s narrowing write additive and put back the assets of a "
            "component it could not join."
        )


def test_a_narrowing_write_still_narrows(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """The risk a merge rule carries, stated as a test."""
    before = {
        "by_type": {"table": ["a.x", "b.y"]},
        "selected": {"a.x": {}, "b.y": {}},
        "schema_ranking": [("a", 1.0)],
    }
    narrowed = merge_delta(before, {"by_type": {"table": ["a.x"]}, "selected": {"a.x": {}}})

    assert narrowed["by_type"] == {"table": ["a.x"]}, narrowed
    assert set(narrowed["selected"]) == {"a.x"}, narrowed
    assert narrowed["schema_ranking"] == [("a", 1.0)], "an untouched key must be carried"




def test_every_declared_channel_is_classified_as_per_turn_or_not():
    """A new channel must be classified before it can be forgotten."""
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
    """A new executor path must be classified before ``terminal`` can read it."""
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


def test_a_turn_built_by_the_session_boundary_answers(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """The turn every real caller builds, through the graph, end to end."""
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
    # `channels` and `failure` are the two that raise rather than mis-report: `dict("reset")` and
    # `"reset".get(...)`. Reaching this line at all means neither did.
    assert isinstance(out.get("channels"), dict) and out["channels"], "the fan-out produced nothing"


def test_session_turn_writes_the_reset(guard_off_policy):
    """The boundary that mints a turn is the boundary that clears the last one."""
    from governed_bi.serve.session import Session

    session = Session(
        index=None, structure=None, assets_by_id={}, corpus=None, connector=None,
        policy=guard_off_policy, corpus_content_hash="h", prompt_set_hash="p",
        settings_resolved={}, db_id="sales_a", run_id="run-1",
    )
    turn = session.turn("customers", turn_index=2)
    for name, reset in PER_TURN_RESET.items():
        assert turn.get(name, "__absent__") == reset, (
            f"turn() does not clear {name!r}; it is a per-turn channel that outlives its turn"
        )




def test_a_failure_in_finish_is_not_swallowed(
    monkeypatch, two_schema_index, two_schema_assets, guard_off_policy
):
    """``finish`` is the one node that must not be wrapped."""
    def boom(state):
        raise RuntimeError("finish exploded")

    monkeypatch.setattr(graph_mod, "finish", boom)
    with pytest.raises(RuntimeError, match="finish exploded"):
        graph_mod.compile_graph().invoke(
            _turn(),
            _config(two_schema_index, two_schema_assets, guard_off_policy, "t-finish"),
        )


def test_a_node_timeout_is_attached_only_where_it_can_fire() -> None:
    """The bound exists, and it is not claimed where it would be a false promise."""
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
    assert "reflect" not in nodes, "the observer node is gone"
    # The fan-out stays out by decision, not by constraint: moving the clock inside wrap_node
    # removed the concurrent-sibling problem, but five simultaneous bounds against a shared
    # provider quota is an unmeasured change that affects results.
    assert not (timed & {name for name, _ in graph_mod._CHANNEL_NODES}), (
        "a retrieval channel carries a timeout; that is now possible but is an unmeasured config change"
    )
    assert "finish" not in timed, "finish is unwrapped and must stay so"
    for name in timed:
        assert name in _CANCELLABLE, (
            f"{name!r} claims a wrap_node timeout but its body is not natively async, so the "
            "await would be cancelled and the work would carry on in its thread"
        )


def test_the_node_timeouts_are_settable_by_a_deployment() -> None:
    """A setting reachable only from source is the defect the register exists to abolish."""
    import os

    from governed_bi.registry.settings import setting_default
    from governed_bi.serve.graph import _node_timeout
    from governed_bi.serve.nodes.agent_core import _agent_node_timeout, _hang_grace

    assert _node_timeout("agent_core") is None, (
        "agent_core owns agent_node_timeout_s internally; wrap must not double-bound it"
    )
    assert _node_timeout("guard") == float(setting_default("rail_node_timeout_s"))
    assert _node_timeout("route") is None, "a to_thread node must not claim a bound"
    assert _node_timeout("channel_term") is None, (
        "a retrieval channel carries a timeout again: possible now, but an unmeasured comparability change"
    )
    assert _agent_node_timeout({}) == float(setting_default("agent_node_timeout_s"))

    # The config-settable path, which is the whole reason the setting is a recorded setting.
    assert _agent_node_timeout({"settings_resolved": {"agent_node_timeout_s": 55.0}}) == 55.0

    assert _agent_node_timeout({"settings_resolved": {"agent_node_timeout_s": 0}}) is None

    # The grace is what lets the soft wall stay between-frames without losing the hang-stop.
    assert _hang_grace({}) == float(setting_default("llm_timeout_s"))
    assert _hang_grace({"settings_resolved": {"llm_timeout_s": 42.0}}) == 42.0

    os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"] = "12.5"
    os.environ["GOVERNED_BI_RAIL_NODE_TIMEOUT_S"] = "7"
    try:
        assert _agent_node_timeout({}) == 12.5
        # Env still wins over a resolved setting, as it does for every other node bound.
        assert _agent_node_timeout({"settings_resolved": {"agent_node_timeout_s": 55.0}}) == 55.0
        assert _node_timeout("guard") == 7.0
        os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"] = "0"
        assert _agent_node_timeout({}) is None, "'0' from a deployment must mean off, not 0s"
        os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"] = ""
        assert _agent_node_timeout({}) == float(setting_default("agent_node_timeout_s")), (
            "an empty env var is unset, not zero"
        )
    finally:
        del os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"]
        del os.environ["GOVERNED_BI_RAIL_NODE_TIMEOUT_S"]
