"""Serve graph wiring (ADR 0005 §3.1).

LangGraph entry surface. This module deliberately avoids
``from __future__ import annotations`` because a graph loaded by file path
must keep raw parameter annotations inspectable.
"""

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
from governed_bi.serve.nodes.negative import negative_node
from governed_bi.serve.nodes.rewrite import rewrite_node
from governed_bi.serve.nodes.route_retrieve import connect_node, resolve_node, route_node
from governed_bi.serve.nodes.stamp import stamp
from governed_bi.serve.nodes.terminal import decline_node, refuse_node
from governed_bi.serve.state import ServeState
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


def build_graph(*, accept: Any = None) -> StateGraph:
    """Construct the uncompiled serve graph.

    **``agent_checkpointer`` is gone, and it never did anything.** It was passed to the nested
    ``create_agent``, and three files carried comments explaining that HITL needed it and that
    "two savers is worse than none: the interrupt is written to one and looked for in the
    other". A probe falsifies all of it — inside a node, ``CONFIG_KEY_CHECKPOINTER`` is the
    *outer* saver, the agent's own saver ends with zero checkpoints, and the outer one has
    three. LangGraph propagates the checkpointer through ``config`` into a graph invoked inside
    a node, under its own namespace. The nested agent was always checkpointed by the graph's
    saver; the parameter was dead code documented as load-bearing, which is worse than either.

    ``accept`` is an optional node placed **before** ``guard``, so ``START -> accept ->
    guard``. It exists for one caller: a server whose client sends only a message. The
    record requires fifteen fields and ``guard`` subscripts ``state["question"]``, so
    something has to derive a turn from the conversation — and per ADR 0007 §2 that
    something must be **server-side**, because ``run_id``, ``corpus_content_hash`` and
    ``knobs_resolved`` are the run's own claims about itself and every quotability gate
    reads them. A client that could set them could make two corpora report as one.

    Passing nothing keeps ``START -> guard``, which is what a caller who builds its own
    turn (``eval/harness.py``, ``python -m governed_bi.serve``) already does correctly.
    """

    graph = StateGraph(ServeState)

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
    graph.add_node("refuse", wrap_node("refuse", refuse_node))
    graph.add_node("decline", wrap_node("decline", decline_node))
    # **``stamp`` is the one node that must not be wrapped.** ``wrap_node`` turns an
    # exception into ``{"failure": ..., "path_kind": "crashed"}`` so the turn is *recorded* by
    # the next node — and for every other node that next node is ``stamp``. There is nothing
    # after ``stamp``, so wrapping it converted "the recorder crashed" into a run that
    # reported no ``answer`` at all and no reason: ``graph.invoke`` returned a state with the
    # key absent, and a caller reading ``out["answer"]["record"]`` got a ``KeyError`` several
    # frames from the cause. Unwrapped, the traceback names the line.
    graph.add_node("stamp", stamp)

    def _fanout_passthrough(state: ServeState) -> dict[str, Any]:
        return {}

    graph.add_node("fanout", wrap_node("facet_schema", _fanout_passthrough))

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
    graph.add_edge("agent_core", "stamp")
    graph.add_edge("refuse", "stamp")
    graph.add_edge("decline", "stamp")
    graph.add_edge("stamp", END)
    return graph


def compile_graph(*, checkpointer: Any | None = None):
    """Compile with an in-memory checkpointer by default (interrupt-ready).

    One saver, and it reaches the nested agent through ``config`` rather than through a
    constructor argument. See :func:`build_graph` for the probe that established that.
    """
    saver = InMemorySaver() if checkpointer is None else checkpointer
    return build_graph().compile(checkpointer=saver)
