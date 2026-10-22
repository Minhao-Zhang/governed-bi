"""A node timeout finishes the turn inside the wrapper and resolves its rail.

LangGraph's node timeout and ``error_handler`` sit outside the node, so the wrapper never sees
the failure and the rail emits ``start`` with no resolve. The clock lives in the wrapper, and
the stream-mode cases are the ones an outside handler does not cover.
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
    """Drive the node on a compiled graph: ``emit`` no-ops without a live stream writer."""
    builder = StateGraph(_RailState)
    builder.add_node("n", node)
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    graph = builder.compile()

    async def drive() -> list[dict[str, Any]]:
        return [c async for c in graph.astream(state, stream_mode="custom")]

    return asyncio.run(drive())


def test_a_timeout_becomes_an_ordinary_crashed_update() -> None:
    """Not an escaping exception. The wrapper owns it, so the turn survives to be written."""
    wrapped = wrap_node("guard", _slow, timeout=0.05)
    update = asyncio.run(wrapped({"question": "q"}))

    assert update["path_kind"] == "crashed", update
    assert update["failure"]["stage"] == "guard", update
    # The real class, not a hard-coded name. `asyncio.wait_for` raises `TimeoutError`; the old
    # handler asserted `"NodeTimeoutError"` without being told what failed.
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
    """``custom``, ``messages``, and subgraphs are the modes an outside handler does not cover."""

    async def slow(_state: _RailState) -> dict:
        await asyncio.sleep(30)
        return {"trace": ["slow-body"]}

    def finish_like(_state: _RailState) -> dict:
        return {"trace": ["stamped"]}

    builder = StateGraph(_RailState)
    builder.add_node("slow", wrap_node("guard", slow, stream=False, timeout=0.05))
    builder.add_node("finish_like", finish_like)
    builder.add_edge(START, "slow")
    builder.add_edge("slow", "finish_like")
    builder.add_edge("finish_like", END)
    graph = builder.compile()

    async def drive() -> list[Any]:
        return [
            c
            async for c in graph.astream(
                {"trace": [], "path_kind": ""}, stream_mode=stream_mode, subgraphs=subgraphs
            )
        ]

    # No exception is the assertion. Under the old design three of these four raised
    # `NodeTimeoutError` out of `astream` at executor teardown and the turn was never written.
    asyncio.run(drive())

    final = asyncio.run(graph.ainvoke({"trace": [], "path_kind": ""}))
    assert "stamped" in final["trace"], final
    assert final["path_kind"] == "crashed", final


@pytest.mark.parametrize("is_async", [True, False], ids=["async", "sync"])
def test_a_node_that_returns_no_mapping_crashes_inside_the_wrapper(is_async: bool) -> None:
    """A node returning ``None`` is a crash inside the wrapper, on both the sync and async branches."""
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
        "turn, because finish never runs."
    )
    assert (out.get("failure") or {}).get("error_type") == "TypeError"
    assert (out.get("failure") or {}).get("stage") == "guard"


def test_a_sync_node_cannot_be_given_a_timeout() -> None:
    """A sync node cannot take a timeout: cancelling ``to_thread`` does not stop the thread."""

    def sync_node(_state: dict) -> dict:
        return {}

    with pytest.raises(ValueError, match="sync and cannot carry a timeout"):
        wrap_node("guard", sync_node, timeout=1.0)


def test_a_raw_sync_invoke_fails_loudly_instead_of_fabricating_a_crashed_turn() -> None:
    """A raw sync ``invoke`` raises instead of returning a crashed record for a turn that never ran."""
    from langgraph.checkpoint.memory import InMemorySaver

    from governed_bi.serve.graph import build_graph

    app = build_graph().compile(checkpointer=InMemorySaver())
    turn = {
        "question": "how many rows", "turn_index": 1, "thread_id": "t1", "run_id": "r",
        "turn_id": "t", "question_id": "q", "db_id": "d", "corpus_content_hash": "c",
        "prompt_set_hash": "p", "settings_resolved": {}, "identity": {}, "n_re_served": 0,
    }

    with pytest.raises(TypeError, match="No synchronous function provided"):
        app.invoke(turn, {"configurable": {"thread_id": "t1"}})


def test_no_error_handler_nodes_remain_in_the_graph() -> None:
    """The graph registers no ``error_handler`` nodes."""
    from governed_bi.serve.graph import build_graph

    nodes = list(build_graph().compile().get_graph().nodes)
    assert [n for n in nodes if "error_handler" in n] == [], nodes
