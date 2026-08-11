"""A node timeout must stamp the turn and resolve its rail, under every stream mode.

This replaces a test of the ``error_handler`` design, which was removed because it did not
work. The history is worth keeping, because the replacement is only obviously right once you
know what the original got wrong.

``add_node(..., timeout=..., error_handler=...)`` enforces the bound *outside* the node
function, so ``wrap_node``'s ``except`` never saw it and a handler was registered to catch the
``NodeTimeoutError`` instead. Measured on langgraph 1.2.10, that handler **runs, updates state,
and still loses the run**: ``pregel/_executor.py``'s teardown re-raises the first task exception
without consulting the handled set, and the runner's single-task fast path that would have
suppressed it is disabled by ``stream_eager``, ``subgraphs=True``, or ``"messages"`` /
``"custom"`` among the stream modes. The UI submits ``["values", "messages", "custom"]`` with
``streamSubgraphs``, so the handler protected exactly one caller: ``ainvoke``, which is what the
original measurement used.

It failed a second way that no exception would have revealed. A node killed from outside never
returns, so ``wrap_node._end`` never runs, so the rail emitted ``start`` and no resolve — the
timeline showed the stage running forever while ``stamp`` reported a crash.

So the clock moved inside. Both properties are pinned below, and the stream-mode parametrisation
is the point: the old design passed the first case and failed the rest.
"""

import asyncio
import operator
from typing import Annotated, Any

import pytest
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from governed_bi.serve.wrap import wrap_node


class _RailState(TypedDict, total=False):
    """Module scope, not function scope: LangGraph resolves a state schema's annotations with
    ``get_type_hints``, which sees module globals only — a locally-defined class with locally
    imported names raises ``NameError: name 'Annotated' is not defined`` at ``add_node``."""

    question: str
    turn_id: str
    path_kind: str
    failure: dict
    trace: Annotated[list, operator.add]


async def _slow(_state: dict) -> dict:
    await asyncio.sleep(30)
    return {"never": True}


def _custom_events(node: Any, state: dict) -> list[dict[str, Any]]:
    """Drive one wrapped node through a real graph and collect its ``custom`` payloads.

    ``emit`` writes to ``get_stream_writer()``, which needs a live runnable context and
    silently no-ops without one — so calling the wrapped node directly would collect nothing
    and the assertion would pass for the wrong reason.
    """
    builder = StateGraph(_RailState)
    builder.add_node("n", node)
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    graph = builder.compile()

    async def drive() -> list[dict[str, Any]]:
        return [c async for c in graph.astream(state, stream_mode="custom")]

    return asyncio.run(drive())


def test_a_timeout_becomes_an_ordinary_crashed_update() -> None:
    """Not an escaping exception. The wrapper owns it, so the turn survives to be stamped."""
    wrapped = wrap_node("guard", _slow, timeout=0.05)
    update = asyncio.run(wrapped({"question": "q"}))

    assert update["path_kind"] == "crashed", update
    assert update["failure"]["stage"] == "guard", update
    # The real class, not a hard-coded name. `asyncio.wait_for` raises `TimeoutError`; the old
    # handler asserted `"NodeTimeoutError"` without being told what actually failed.
    assert update["failure"]["error_type"] == "TimeoutError", update


def test_a_timeout_resolves_its_rail_instead_of_leaving_it_running() -> None:
    """The half the `error_handler` could not do at all, in any stream mode."""
    events = _custom_events(
        wrap_node("narrate", _slow, timeout=0.05), {"question": "q", "turn_id": "t1"}
    )

    steps = [(e.get("step"), e.get("status")) for e in events if e.get("kind") == "rail"]
    assert ("narrate", "start") in steps, steps
    resolved = [s for s in steps if s[0] == "narrate" and s[1] != "start"]
    assert resolved, (
        "the timed-out rail emitted `start` and never resolved — the timeline shows it "
        f"running forever. events: {steps}"
    )


@pytest.mark.parametrize(
    "stream_mode,subgraphs",
    [
        ("values", False),
        # The three that disabled the runner's fast path and so defeated the old handler.
        ("custom", False),
        ("messages", False),
        ("values", True),
    ],
)
def test_the_turn_is_stamped_under_every_stream_mode(stream_mode: str, subgraphs: bool) -> None:
    """The parametrisation IS the regression. The old design passed row one and failed rows 2-4."""

    async def slow(_state: _RailState) -> dict:
        await asyncio.sleep(30)
        return {"trace": ["slow-body"]}

    def stamp_like(_state: _RailState) -> dict:
        return {"trace": ["stamped"]}

    builder = StateGraph(_RailState)
    builder.add_node("slow", wrap_node("guard", slow, stream=False, timeout=0.05))
    builder.add_node("stamp_like", stamp_like)
    builder.add_edge(START, "slow")
    builder.add_edge("slow", "stamp_like")
    builder.add_edge("stamp_like", END)
    graph = builder.compile()

    async def drive() -> list[Any]:
        return [
            c
            async for c in graph.astream(
                {"trace": [], "path_kind": ""}, stream_mode=stream_mode, subgraphs=subgraphs
            )
        ]

    # No exception is the assertion. Under the old design three of these four raised
    # `NodeTimeoutError` out of `astream` at executor teardown and the turn was never stamped.
    asyncio.run(drive())

    final = asyncio.run(graph.ainvoke({"trace": [], "path_kind": ""}))
    assert "stamped" in final["trace"], final
    assert final["path_kind"] == "crashed", final


@pytest.mark.parametrize("is_async", [True, False], ids=["async", "sync"])
def test_a_node_that_returns_no_mapping_crashes_inside_the_wrapper(is_async: bool) -> None:
    """Audit C7 — the wrapper's own promise, broken by the wrapper.

    ``_end`` and ``_without_cleared_clock`` run *after* the ``except``, and both subscript the
    update. So a node returning ``None`` — or anything that is not a mapping — raised from the
    wrapper itself: no ``crashed`` marker, no ``answer``, no ``final`` event, and the exception
    left the graph. "Every failure routes through ``stamp``" is exactly what this wrapper exists
    for, and this was the one path around it.

    Parametrised over both shapes because the fix nearly missed one: ``_body``'s async branch
    returned early, before the check, so the first version covered sync nodes only. The two
    branches now share one shape check.
    """
    from governed_bi.serve.wrap import wrap_node

    if is_async:
        async def node(state):  # type: ignore[no-untyped-def]
            return None
    else:
        def node(state):  # type: ignore[no-untyped-def]
            return None

    out = asyncio.run(wrap_node("guard", node)({"turn_id": "t", "turn_index": 1}))

    assert out.get("path_kind") == "crashed", (
        f"a node returning None escaped the wrapper: {out!r}. Nothing downstream records the "
        "turn, because stamp never runs."
    )
    assert (out.get("failure") or {}).get("error_type") == "TypeError"
    assert (out.get("failure") or {}).get("stage") == "guard"


def test_a_sync_node_cannot_be_given_a_timeout() -> None:
    """Refused at build time, because cancelling the await would not stop the thread.

    ``wrap_node`` runs a still-synchronous node through ``asyncio.to_thread``. Cancelling that
    await abandons the result; the thread keeps running, holding its connection and its slot.
    A turn recorded as timed out while its node is still executing is a false measurement, so
    the wiring is refused rather than silently weakened.
    """

    def sync_node(_state: dict) -> dict:
        return {}

    with pytest.raises(ValueError, match="sync and cannot carry a timeout"):
        wrap_node("guard", sync_node, timeout=1.0)


def test_a_raw_sync_invoke_fails_loudly_instead_of_fabricating_a_crashed_turn() -> None:
    """Forgetting ``as_sync`` must raise, not return a full record for a turn that never ran.

    This is the worst failure the timeout rewiring fixed, and it is here rather than beside
    ``_SyncApp`` because the timeouts caused it. While the rails carried LangGraph node
    timeouts, ``pregel/_retry.py`` raised ``sync_timeout_unsupported`` on the sync path for any
    node with a timeout — before the async check, and regardless of whether the node was sync.
    ``guard`` is first and carried one, so ``rail``'s ``error_handler`` caught it and
    ``.invoke()`` returned ``outcome: "crashed"``, ``failed_stage: "guard"``,
    ``error_type: "ValueError"`` with a complete audit record and **no exception**.

    An eval driver that forgot ``as_sync`` would have produced 1,351 fully-formed fictional
    rows and no signal that anything was wrong. Nothing else in the suite would catch that.
    """
    from langgraph.checkpoint.memory import InMemorySaver

    from governed_bi.serve.graph import build_graph

    app = build_graph().compile(checkpointer=InMemorySaver())
    turn = {
        "question": "how many rows", "turn_index": 1, "thread_id": "t1", "run_id": "r",
        "turn_id": "t", "question_id": "q", "db_id": "d", "corpus_content_hash": "c",
        "prompt_set_hash": "p", "knobs_resolved": {}, "identity": {}, "n_re_served": 0,
    }

    with pytest.raises(TypeError, match="No synchronous function provided"):
        app.invoke(turn, {"configurable": {"thread_id": "t1"}})


def test_no_error_handler_nodes_remain_in_the_graph() -> None:
    """LangGraph materialises one node per ``error_handler``; we register none.

    They rendered as unreachable islands in every graph diagram and were repeatedly mistaken
    for orphans. They are gone because the mechanism they served did not work, not because the
    drawing was untidy.
    """
    from governed_bi.serve.graph import build_graph

    nodes = list(build_graph().compile().get_graph().nodes)
    assert [n for n in nodes if "error_handler" in n] == [], nodes
