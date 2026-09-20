"""The harness measures a different graph object from the one the server runs. Pin the delta.

``serve/graph.py::build_graph`` builds two shapes. With ``accept`` it is
``StateGraph(ServeState, input_schema=ServeInput, output_schema=ServeOutput)`` plus an
``accept`` node wired from START and a ``record`` node before END; without, it is a plain
``StateGraph(ServeState)`` — full state in, full state out. ``langgraph.json`` serves
``api/graph_app.py:make_graph``, which passes both. ``compile_graph`` and ``compile_durable``
pass neither, and they are what ``eval/harness.py`` and ``eval/datalake.py`` use.

**This file does not close that gap.** Closing it means the harness loses the four state keys
it injects — ``evidence``, ``knobs_resolved``, ``facet_route_hits``, ``pinned_schemas`` — and
``ServeOutput``'s two keys are not what ``project_turn`` reads, so the harness would have to
read the checkpoint instead of the return value. That is a redesign of the path every number
comes from, and it buys nothing until someone re-runs. What this file does is make the
divergence **declared**: the delta is written down, and a *new* one fails here rather than
being discovered the next time somebody asks what the benchmark measured.

The one difference that reaches a number is ``pinned_schemas``: ``eval/replay.py`` writes it,
``route_retrieve.py`` honours it by **replacing** the score-ranked shortlist outright, and the
served graph's ``input_schema`` drops it. It is set on 1,345 of 1,351 rows of the v4, v4_reflect
and v5 artifacts, so those arms' routing is v3_fold's. ``eval/provenance.py`` reports that on
the arm's provenance line for the same reason this test exists: a reader must not be able to
quote a pinned arm's routing as the arm's own.
"""

from __future__ import annotations

from typing import Any

from governed_bi.serve.graph import build_graph
from governed_bi.serve.state import ServeInput, ServeOutput

#: What the served graph has that the measured one does not. Both nodes and the five edges
#: that wire them; nothing else may differ.
SERVED_ONLY_NODES = frozenset({"accept", "record"})
SERVED_ONLY_EDGES = frozenset(
    {
        ("__start__", "accept"),
        ("accept", "guard"),
        ("accept", "stamp"),
        ("stamp", "record"),
        ("record", "__end__"),
    }
)
MEASURED_ONLY_EDGES = frozenset({("__start__", "guard"), ("stamp", "__end__")})


def _shapes() -> tuple[Any, Any]:
    served = build_graph(accept=lambda state, config: {}, record=lambda state: {}).compile()
    measured = build_graph().compile()
    return served.get_graph(), measured.get_graph()


def _edges(graph: Any) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.edges}


def test_the_two_graphs_differ_only_by_accept_and_record() -> None:
    """Every other node and edge is shared, and a new divergence fails here.

    This is the assertion the whole file is for. A node that existed on only one side would
    mean the benchmark exercised a pipeline the server does not run, or the reverse — and
    nothing else in the tree compares the two.
    """
    served, measured = _shapes()

    assert set(served.nodes) - set(measured.nodes) == SERVED_ONLY_NODES
    assert set(measured.nodes) - set(served.nodes) == frozenset(), (
        "the measured graph grew a node the served graph does not have"
    )
    assert _edges(served) - _edges(measured) == SERVED_ONLY_EDGES
    assert _edges(measured) - _edges(served) == MEASURED_ONLY_EDGES


def test_the_served_graph_filters_state_the_harness_relies_on_injecting() -> None:
    """``ServeInput`` is one key. That is a control, and it is why the two shapes exist.

    Asserted as an inventory rather than by driving a turn: the point is that the *surface*
    is one key wide, and a second key added to it hands a client a channel the graph trusts.
    ``licensed`` is the one that matters — ``serve/delivery.py`` derives ``ToolBounds.licensed``
    straight from it, so a caller who could write it could widen what SQL the guard accepts.
    """
    assert set(ServeInput.__annotations__) == {"messages"}, (
        "a key was added to the served graph's input surface. Every one of them is something "
        "an HTTP caller can now write into state"
    )
    assert set(ServeOutput.__annotations__) == {"messages", "answer"}


def test_pinned_schemas_is_reachable_only_from_the_measured_graph() -> None:
    """The one divergence that moves a number, named so it cannot be forgotten.

    ``route_retrieve.py`` honours ``pinned_schemas`` by replacing the ranked shortlist
    outright, so an arm that carries it is not measuring its own router. It is absent from
    ``ServeInput``, which is what keeps it off every served turn.
    """
    from governed_bi.serve.state import ServeState

    assert "pinned_schemas" in ServeState.__annotations__
    assert "pinned_schemas" not in ServeInput.__annotations__, (
        "pinned_schemas reached the served input surface; a client could now replace the "
        "router's shortlist, and the record would say routing ran"
    )


def test_an_arm_that_replayed_a_shortlist_says_so_on_its_provenance_line() -> None:
    """The divergence that reaches a number has to be visible in the report.

    ``pinned_schemas`` contradicts nothing in ``arms.toml`` — the file does not claim an arm
    routed for itself — so ``reconcile`` is silent about it and the artifact reads as
    agreeing. That silence is the problem: the arm measures generation against a router that
    did not run, and the reader is the only one who could notice.

    A note rather than a problem, because the arm is not mislabelled. The justification for
    pinning is real: four of the five facet nodes are model calls, so two runs of one question
    hand ``route`` different hits, and an A/B that lets the shortlist move cannot attribute
    its own delta. What was missing is that it was never said out loud.
    """
    from governed_bi.eval.provenance import reconciliation_lines
    from governed_bi.register.arm_profiles import arm_profile

    profile = arm_profile("v4")
    row = {
        "question_id": "q1",
        "corpus_content_hash": profile.corpus_content_hash,
        "knobs_resolved": {"question_subset": profile.question_subset},
    }

    live = reconciliation_lines([row], profile)
    assert not any("routing_pinned" in line for line in live), (
        "an arm that routed for itself was annotated as pinned"
    )

    pinned = reconciliation_lines([dict(row, routing_pinned=True)], profile)
    assert any("routing_pinned" in line for line in pinned), (
        f"a replayed shortlist is invisible on the provenance line: {pinned}"
    )
    assert any("1 of 1 turns replayed" in line for line in pinned), pinned
