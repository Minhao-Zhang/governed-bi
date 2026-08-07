"""Serve graph wiring (ADR 0005 §3.1).

LangGraph entry surface. Avoids ``from __future__ import annotations`` so a graph
loaded by file path keeps raw parameter annotations inspectable.
"""

import asyncio
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, TimeoutPolicy

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
from governed_bi.serve.nodes.reflect import reflect_node
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


#: Rails that may carry a node timeout. Two conditions, both measured, both narrowing.
#:
#: **Natively async.** A node whose body still runs through ``asyncio.to_thread`` (``wrap_node``)
#: would have its *await* cancelled and the thread left running, so the bound would be a claim
#: rather than a fact.
#:
#: **Alone in its super-step.** The five facets are not, and that is why they are absent despite
#: being natively async: with concurrent siblings a ``NodeTimeoutError`` surfaces at executor
#: teardown (``pregel/_loop.py`` ``__aexit__``) and never reaches the node's ``error_handler``.
#: Measured — a hung facet raised straight out of the graph and the turn produced no record,
#: while the identical hang on ``guard`` was handled and stamped. A timeout that trades a hang
#: for a missing record is not an improvement, so the fan-out keeps the hang until that is
#: solved. ``agent_core`` is handled separately in :func:`_node_timeout`; it is the node with no
#: other ceiling and it runs alone.
_CANCELLABLE = frozenset({"guard", "narrate"})


def _node_timeout(name: str) -> Any:
    """The node's wall clock, or ``None`` where a timeout would be a false promise.

    Env-settable like every other deployment knob, through the same names the model timeouts
    use. ``tests/conformance`` exists because a knob reachable only from code is the defect the
    register was written to abolish.
    """
    import os

    from governed_bi.register.knobs import knob_default

    if name == "agent_core":
        var, knob = "GOVERNED_BI_AGENT_NODE_TIMEOUT_S", "agent_node_timeout_s"
    elif name in _CANCELLABLE:
        var, knob = "GOVERNED_BI_RAIL_NODE_TIMEOUT_S", "rail_node_timeout_s"
    else:
        return None
    raw = os.environ.get(var)
    seconds = float(raw) if raw else float(knob_default(knob))
    return TimeoutPolicy(run_timeout=seconds)


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

    def rail(name: str, fn: Any, **kw: Any) -> None:
        """Register a wrapped node, with a timeout where one can really fire.

        **The ``error_handler`` is what makes the timeout safe to add.** LangGraph enforces
        ``run_timeout`` *outside* the node function, so ``wrap_node``'s ``except`` never sees it
        — measured: a hung facet raised ``NodeTimeoutError`` straight out of the graph, ``stamp``
        never ran, and the turn produced **no record at all**. That is the one direction this
        engine must not fail in, and it would have been introduced by the fix for a hang.

        The handler is only reachable on a timeout: ``wrap_node`` already turns every ordinary
        exception into a ``crashed`` update and returns normally, so nothing else propagates far
        enough to reach it. That is why it can name ``NodeTimeoutError`` without being handed the
        exception — this LangGraph version passes the handler a state, not an error.
        """
        timeout = _node_timeout(name)
        if timeout is None:
            graph.add_node(name, wrap_node(name, fn, **kw))
            return

        def timed_out(_state: ServeState, _name: str = name) -> Any:
            # ``goto="stamp"`` and not a bare update: the handler replaces the node, so the
            # node's own outgoing edge does not run and the turn would end unstamped —
            # `path_kind: crashed` in state and no record anywhere, which is the failure this
            # engine exists to prevent. Measured before adding it: `answer` was None.
            return Command(
                update={
                    "failure": {"stage": _name, "error_type": "NodeTimeoutError"},
                    "path_kind": "crashed",
                },
                goto="stamp",
            )

        graph.add_node(
            name, wrap_node(name, fn, **kw), timeout=timeout, error_handler=timed_out
        )

    rail("guard", guard_node)
    rail("rewrite", rewrite_node)
    rail("negative_gate", negative_node)
    for name, fn in _FACET_NODES:
        rail(name, fn)
    rail("route", route_node)
    rail("resolve", resolve_node)
    rail("connect", connect_node)
    rail("assemble", assemble_node)
    rail("agent_core", agent_core_node)
    # **Not through ``rail``, and both differences are the point.**
    #
    # ``stream=False``: ``wrap_node`` emits a start and a resolve row for every node it wraps,
    # so an observer that ships disabled would still have put two rows per turn on the
    # timeline. The node emits its own single row, only on the turns where it judged something,
    # which is what keeps a default-off turn's event stream identical to what it was.
    #
    # **No timeout**, deliberately, though it is natively async and alone in its super-step —
    # the two conditions ``_CANCELLABLE`` requires. A ``TimeoutPolicy`` fires *outside* the node
    # and its handler marks the turn ``crashed`` and jumps to ``stamp``, so giving this node one
    # would let an **observer** fail a turn that had already answered. That is the one thing it
    # must never do. The model call is bounded by the model's own ``request_timeout``
    # (``llm_utility_timeout_s``), and any exception it raises is caught and recorded as an
    # unmeasured verdict.
    graph.add_node("reflect", wrap_node("reflect", reflect_node, stream=False))
    rail("narrate", narrate_node)
    rail("refuse", refuse_node)
    rail("decline", decline_node)
    # Unwrapped: nothing after stamp can record a wrap crash.
    graph.add_node("stamp", stamp)

    def _fanout_passthrough(state: ServeState) -> dict[str, Any]:
        return {}

    # Not through `rail`: the node is `fanout` but its *stage* is `facet_schema`, and `rail`
    # uses one name for both. stream=False means it emits nothing anyway, and it does no work,
    # so it needs no timeout.
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
    # Terminals skip narrate: refusal/decline wording is system copy. ``reflect`` sits between
    # the agent and the narrator as a plain edge and not a conditional one, because a
    # conditional edge reading its verdict is exactly the control flow it must not have.
    graph.add_edge("agent_core", "reflect")
    graph.add_edge("reflect", "narrate")
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
