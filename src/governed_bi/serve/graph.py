"""Serve graph wiring (ADR 0005 §3.1).

LangGraph entry surface. Avoids ``from __future__ import annotations`` so a graph
loaded by file path keeps raw parameter annotations inspectable.
"""

import asyncio
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.nodes.assemble import assemble_node
from governed_bi.serve.nodes.facets import (
    facet_entity_node,
    facet_example_node,
    facet_metric_node,
    facet_schema_node,
    facet_term_node,
)
from governed_bi.serve.nodes.guard import guard_node
from governed_bi.serve.nodes.narrate import narrate_node
from governed_bi.serve.nodes.negative import negative_node
from governed_bi.serve.nodes.rewrite import rewrite_node
from governed_bi.serve.nodes.route_retrieve import connect_node, resolve_node, route_node
from governed_bi.serve.nodes.stamp import stamp
from governed_bi.serve.nodes.terminal import decline_node, refuse_node
from governed_bi.serve.state import ServeInput, ServeState
from governed_bi.serve.wrap import wrap_node

__all__ = ["build_graph", "compile_graph"]

_FACET_NODES = (
    ("facet_schema", facet_schema_node),
    ("facet_term", facet_term_node),
    ("facet_metric", facet_metric_node),
    ("facet_entity", facet_entity_node),
    ("facet_example", facet_example_node),
)


def _after_guard(state: ServeState) -> Literal["refuse", "rewrite", "stamp"]:
    if state.get("path_kind") == "crashed":
        return "stamp"
    guard = state.get("guard") or {}
    if guard.get("outcome") == "blocked":
        return "refuse"
    return "rewrite"


def _after_negative(state: ServeState) -> Literal["decline", "fanout", "stamp"]:
    if state.get("path_kind") == "crashed":
        return "stamp"
    negative = state.get("negative") or {}
    if negative.get("outcome") == "hit":
        return "decline"
    return "fanout"


def _after_route(state: ServeState) -> Literal["decline", "resolve", "stamp"]:
    if state.get("path_kind") == "crashed":
        return "stamp"
    if state.get("path_kind") == "decline":
        return "decline"
    return "resolve"


def _after_connect(state: ServeState) -> Literal["decline", "assemble", "stamp"]:
    if state.get("path_kind") == "crashed":
        return "stamp"
    if state.get("path_kind") == "decline":
        return "decline"
    return "assemble"


def _skip_if_terminal(state: ServeState) -> Literal["stamp", "continue"]:
    if state.get("path_kind") in ("refuse", "decline", "crashed"):
        return "stamp"
    return "continue"


def build_graph(*, accept: Any = None, record: Any = None) -> StateGraph:
    """Construct the uncompiled serve graph.

    Nested agent is checkpointed via the outer graph's saver through ``config``.
    ``accept`` (optional, before ``guard``) derives a turn from a client message.
    ``record`` (optional, after ``stamp``) appends to the audit log.
    """

    # `input_schema` only when `accept` is present. That flag *is* the trust boundary: with it,
    # a turn is derived from a client conversation and nothing else the client sends may reach
    # state (audit §4.3); without it the caller is `serve/__main__`, `eval/` or `/chat`, which
    # build the turn in-process and pass the whole of ServeState on purpose.
    graph = (
        StateGraph(ServeState, input_schema=ServeInput)
        if accept is not None
        else StateGraph(ServeState)
    )

    graph.add_node("guard", wrap_node("guard", guard_node))
    graph.add_node("rewrite", wrap_node("rewrite", rewrite_node))
    graph.add_node("negative_gate", wrap_node("negative_gate", negative_node))
    for name, fn in _FACET_NODES:
        graph.add_node(name, wrap_node(name, fn))
    graph.add_node("route", wrap_node("route", route_node))
    graph.add_node("resolve", wrap_node("resolve", resolve_node))
    graph.add_node("connect", wrap_node("connect", connect_node))
    graph.add_node("assemble", wrap_node("assemble", assemble_node))
    graph.add_node("agent_core", wrap_node("agent_core", agent_core_node))
    graph.add_node("narrate", wrap_node("narrate", narrate_node))
    graph.add_node("refuse", wrap_node("refuse", refuse_node))
    graph.add_node("decline", wrap_node("decline", decline_node))
    # Unwrapped: nothing after stamp can record a wrap crash.
    graph.add_node("stamp", stamp)

    def _fanout_passthrough(state: ServeState) -> dict[str, Any]:
        return {}

    # stream=False: passthrough must not emit a phantom facet_schema row.
    graph.add_node("fanout", wrap_node("facet_schema", _fanout_passthrough, stream=False))

    if accept is not None:
        graph.add_node("accept", wrap_node("accept", accept))
        graph.add_edge(START, "accept")
        graph.add_edge("accept", "guard")
    else:
        graph.add_edge(START, "guard")
    graph.add_conditional_edges(
        "guard",
        _after_guard,
        {"refuse": "refuse", "rewrite": "rewrite", "stamp": "stamp"},
    )
    graph.add_edge("rewrite", "negative_gate")
    graph.add_conditional_edges(
        "negative_gate",
        _after_negative,
        {"decline": "decline", "fanout": "fanout", "stamp": "stamp"},
    )
    for name, _ in _FACET_NODES:
        graph.add_edge("fanout", name)
        graph.add_edge(name, "route")

    graph.add_conditional_edges(
        "route",
        _after_route,
        {"decline": "decline", "resolve": "resolve", "stamp": "stamp"},
    )
    graph.add_conditional_edges(
        "resolve",
        _skip_if_terminal,
        {"stamp": "stamp", "continue": "connect"},
    )
    graph.add_conditional_edges(
        "connect",
        _after_connect,
        {"decline": "decline", "assemble": "assemble", "stamp": "stamp"},
    )
    graph.add_conditional_edges(
        "assemble",
        _skip_if_terminal,
        {"stamp": "stamp", "continue": "agent_core"},
    )
    # Terminals skip narrate: refusal/decline wording is system copy.
    graph.add_edge("agent_core", "narrate")
    graph.add_edge("narrate", "stamp")
    graph.add_edge("refuse", "stamp")
    graph.add_edge("decline", "stamp")
    if record is not None:
        graph.add_node("record", record)
        graph.add_edge("stamp", "record")
        graph.add_edge("record", END)
    else:
        graph.add_edge("stamp", END)
    return graph


class _SyncApp:
    """A sync front for an async graph, for the callers that are not going async.

    Every node goes through ``wrap_node``, which is ``async def`` — the only shape LangGraph
    will attach a node timeout to (``TimeoutPolicy`` refuses a sync node outright: "sync Python
    execution cannot be safely cancelled in-process"). That makes ``.invoke()`` raise
    ``TypeError: No synchronous function provided``, and the in-process callers — the CLI,
    ``eval/``, ``/chat`` and the tests — have no reason to become async.

    ``/chat``'s handlers are sync ``def``, so Starlette runs them in a worker thread with no
    running loop, and ``asyncio.run`` is safe there. The served graph is **not** wrapped: the
    platform drives ``ainvoke`` itself, which is the path this exists to leave alone.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return asyncio.run(self._app.ainvoke(*args, **kwargs))

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        """Drained, then replayed. Order is preserved; incrementality is not.

        No production caller streams through this — ``/chat`` blocks and the live surface is
        the platform's async one. It exists so the stream-event tests keep asserting the same
        ordered timeline they always did.
        """

        async def drain() -> list[Any]:
            return [chunk async for chunk in self._app.astream(*args, **kwargs)]

        return iter(asyncio.run(drain()))


def as_sync(app: Any) -> _SyncApp:
    """Wrap a compiled async graph for a sync caller. See :class:`_SyncApp`."""
    return _SyncApp(app)


def compile_graph(*, checkpointer: Any | None = None) -> _SyncApp:
    """Compile with an in-memory checkpointer by default (interrupt-ready)."""
    saver = InMemorySaver() if checkpointer is None else checkpointer
    return as_sync(build_graph().compile(checkpointer=saver))
