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
from governed_bi.serve.nodes.reflect import reflect_node
from governed_bi.serve.nodes.rewrite import rewrite_node
from governed_bi.serve.nodes.route_retrieve import connect_node, resolve_node, route_node
from governed_bi.serve.nodes.stamp import stamp
from governed_bi.serve.nodes.terminal import decline_node, refuse_node
from governed_bi.serve.state import ServeInput, ServeOutput, ServeState
from governed_bi.serve.wrap import wrap_node

__all__ = ["build_graph", "compile_graph"]

_FACET_NODES = (
    ("facet_schema", facet_schema_node),
    ("facet_term", facet_term_node),
    ("facet_metric", facet_metric_node),
    ("facet_entity", facet_entity_node),
    ("facet_example", facet_example_node),
)


def _after_accept(state: ServeState) -> Literal["guard", "stamp"]:
    """Accept soft-crashes must not enter ``guard`` on a prior turn's leftover channels."""
    if state.get("path_kind") == "crashed":
        return "stamp"
    return "guard"


def _after_guard(state: ServeState) -> Literal["refuse", "rewrite", "stamp"]:
    if state.get("path_kind") == "crashed":
        return "stamp"
    guard = state.get("guard") or {}
    if guard.get("outcome") == "blocked":
        return "refuse"
    return "rewrite"


def _after_rewrite(state: ServeState) -> Literal["negative_gate", "stamp"]:
    """Rewrite used to fall through to ``negative_gate`` even after a wrap crash."""
    if state.get("path_kind") == "crashed":
        return "stamp"
    return "negative_gate"


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


#: Rails that carry a node timeout. Natively async only, because ``wrap_node`` refuses a timeout
#: on a sync node: cancelling the ``await`` around ``asyncio.to_thread`` leaves the thread
#: running, so the bound would be a claim rather than a fact.
#:
#: ``narrate`` is excluded on purpose (same reason as ``reflect``): a timeout marks the turn
#: ``crashed``, and by then ``agent_core`` may already have answered. Model failures inside
#: narrate are swallowed; a wall-clock bound would still rewrite the outcome.
#:
#: The five facets are excluded by **decision, not constraint**: five concurrent bounds interact
#: with the shared provider quota in a way nobody has measured, and turning them on is a
#: comparability change that belongs to its own experiment. Adding them is a one-line edit here.
_CANCELLABLE = frozenset({"guard"})


def _node_timeout(name: str) -> float | None:
    """The node's wall clock in seconds, or ``None`` where it would be a false promise.

    Env-settable through the same names the model timeouts use, because a knob reachable only
    from code is the defect the register was written to abolish (``tests/conformance`` pins it).

    Plain seconds, not a ``TimeoutPolicy``: the bound is applied by ``wrap_node`` with
    ``asyncio.wait_for`` and never reaches LangGraph's node-timeout machinery. See
    ``wrap_node``'s docstring for the measurement that moved it.

    ``agent_core`` is excluded on purpose: its hang-stop lives inside the node so a timeout
    still projects the streamed ledger (``serve/nodes/agent_core.py``). Putting the bound in
    ``wrap_node`` would reduce the update to ``{failure, path_kind}`` again.
    """
    import os

    from governed_bi.register.knobs import knob_default

    if name == "agent_core":
        return None
    if name in _CANCELLABLE:
        var, knob = "GOVERNED_BI_RAIL_NODE_TIMEOUT_S", "rail_node_timeout_s"
    else:
        return None
    raw = os.environ.get(var)
    return float(raw) if raw else float(knob_default(knob))


def build_graph(*, accept: Any = None, record: Any = None) -> StateGraph:
    """Construct the uncompiled serve graph.

    Nested agent is checkpointed via the outer graph's saver through ``config``.
    ``accept`` (optional, before ``guard``) derives a turn from a client message.
    ``record`` (optional, after ``stamp``) appends to the audit log.
    """

    # `input_schema` / `output_schema` only when `accept` is present. That flag *is* the trust
    # boundary: with it a turn is derived from a client conversation, so nothing else the client
    # sends may reach state and nothing it did not ask for goes back (audit §4.3; see
    # `ServeInput` / `ServeOutput`). Without it the caller is `serve/__main__`, `eval/` or
    # `/chat`, which build the turn in-process and pass and read the whole of ServeState on
    # purpose — the eval harness projects its record out of channels no client sees.
    graph = (
        StateGraph(ServeState, input_schema=ServeInput, output_schema=ServeOutput)
        if accept is not None
        else StateGraph(ServeState)
    )

    def rail(name: str, fn: Any, **kw: Any) -> None:
        """Register a wrapped node, with a timeout where one can really fire.

        The clock is ``wrap_node``'s, not LangGraph's: measured on 1.2.10 an ``add_node``
        timeout's ``error_handler`` runs but does not save the run, because
        ``pregel/_executor.py``'s teardown re-raises the first task exception and the fast path
        that would suppress it is disabled by ``stream_eager``, ``subgraphs=True`` or
        ``"messages"`` / ``"custom"`` stream modes — the served surface submits three of those
        at once. Nothing here registers an ``error_handler``.
        """
        graph.add_node(name, wrap_node(name, fn, timeout=_node_timeout(name), **kw))

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
    # Not through `rail`. `stream=False`: the node emits its own single row only on the turns
    # where it judged something, so a default-off turn's event stream is unchanged. No timeout,
    # deliberately: a timeout marks the turn `crashed` and jumps to `stamp`, which would let an
    # **observer** fail a turn that had already answered. The model call is bounded by its own
    # `request_timeout` (`llm_utility_timeout_s`) and any exception is recorded as unmeasured.
    graph.add_node("reflect", wrap_node("reflect", reflect_node, stream=False))
    # Not through `rail`. Same observer rule as ``reflect``: a timeout or wrap crash here
    # would mark an already-answered turn ``crashed``. Model call failures are swallowed inside
    # the node; the utility ``request_timeout`` still bounds the provider call.
    graph.add_node("narrate", wrap_node("narrate", narrate_node))
    rail("refuse", refuse_node)
    rail("decline", decline_node)
    # Unwrapped: nothing after stamp can record a wrap crash.
    graph.add_node("stamp", stamp)

    def _fanout_passthrough(state: ServeState) -> dict[str, Any]:
        return {}

    # Not through `rail`: the node is `fanout` but its *stage* is `facet_schema`, and `rail`
    # uses one name for both. It does no work, so it needs no timeout.
    graph.add_node("fanout", wrap_node("facet_schema", _fanout_passthrough, stream=False))

    if accept is not None:
        graph.add_node("accept", wrap_node("accept", accept))
        graph.add_edge(START, "accept")
        graph.add_conditional_edges(
            "accept",
            _after_accept,
            {"guard": "guard", "stamp": "stamp"},
        )
    else:
        graph.add_edge(START, "guard")
    graph.add_conditional_edges(
        "guard",
        _after_guard,
        {"refuse": "refuse", "rewrite": "rewrite", "stamp": "stamp"},
    )
    graph.add_conditional_edges(
        "rewrite",
        _after_rewrite,
        {"negative_gate": "negative_gate", "stamp": "stamp"},
    )
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
    # Terminals skip narrate: refusal/decline wording is system copy. `reflect` is a plain edge
    # and not a conditional one, because an edge reading its verdict is exactly the control flow
    # the observer must not have.
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

    Every node goes through ``wrap_node``, which is ``async def``, so ``.invoke()`` raises
    ``TypeError: No synchronous function provided to "guard"``. The in-process callers — the
    CLI, ``eval/``, ``/chat`` and the tests — have no reason to become async, so they go
    through here.

    **That ``TypeError`` must keep being raised.** While the rails carried LangGraph node
    timeouts it was swallowed and ``.invoke()`` returned a complete ``outcome: "crashed"``
    record instead, so forgetting ``as_sync`` produced a whole arm of fabricated measurements.
    ``tests/serve/test_node_timeout_is_enforced_inside_the_wrapper.py`` pins it.

    ``/chat``'s handlers are sync ``def``, so Starlette runs them in a worker thread with no
    running loop and ``asyncio.run`` is safe. The served graph is **not** wrapped: the platform
    drives ``ainvoke`` itself.

    Known hazard, not fixed: ``asyncio.run`` builds and tears down an event loop per call while
    the model objects are process-wide, which matters for a provider client that caches a
    connection pool on its first loop.
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
        the platform's async one. It exists for the stream-event tests.
        """

        async def drain() -> list[Any]:
            return [chunk async for chunk in self._app.astream(*args, **kwargs)]

        return iter(asyncio.run(drain()))


def as_sync(app: Any) -> _SyncApp:
    """Wrap a compiled async graph for a sync caller. See :class:`_SyncApp`."""
    return _SyncApp(app)


def compile_graph(*, checkpointer: Any | None = None) -> _SyncApp:
    """Compile with an in-memory checkpointer by default (interrupt-ready).

    ``checkpointer=False`` compiles with **no** saver, because ``None`` already means "make me
    an ``InMemorySaver``" and otherwise "do not persist" is only sayable by bypassing this
    facade — which also bypasses ``as_sync``. A saver-less graph cannot interrupt, so
    ``ask_user`` is unavailable; that is the whole trade.

    **The default saver grows for as long as it lives and nothing evicts it.** Measured on a
    two-schema corpus: 101 KB after one turn, 844 KB after six, because ``usage`` and ``answer``
    accumulate and every superstep re-serialises them with ``knobs_resolved``. ``eval/harness.py``
    holds one compiled graph per worker for a whole arm, so a 1,351-question run retains on the
    order of 135 MB per worker unless it calls ``InMemorySaver.delete_thread`` — which it does,
    per question.
    """
    if checkpointer is False:
        return as_sync(build_graph().compile())
    saver = InMemorySaver() if checkpointer is None else checkpointer
    return as_sync(build_graph().compile(checkpointer=saver))
