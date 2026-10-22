"""The harness graph is not the served graph. ``pinned_schemas`` replaces the shortlist and is not a served input."""

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
        ("accept", "finish"),
        ("finish", "record"),
        ("record", "__end__"),
    }
)
MEASURED_ONLY_EDGES = frozenset({("__start__", "guard"), ("finish", "__end__")})


def _shapes() -> tuple[Any, Any]:
    served = build_graph(accept=lambda state, config: {}, record=lambda state: {}).compile()
    measured = build_graph().compile()
    return served.get_graph(), measured.get_graph()


def _edges(graph: Any) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.edges}


def test_the_two_graphs_differ_only_by_accept_and_record() -> None:
    """A node on only one side means the benchmark and the server run different pipelines."""
    served, measured = _shapes()

    assert set(served.nodes) - set(measured.nodes) == SERVED_ONLY_NODES
    assert set(measured.nodes) - set(served.nodes) == frozenset(), (
        "the measured graph grew a node the served graph does not have"
    )
    assert _edges(served) - _edges(measured) == SERVED_ONLY_EDGES
    assert _edges(measured) - _edges(served) == MEASURED_ONLY_EDGES


def test_the_served_graph_filters_state_the_harness_relies_on_injecting() -> None:
    """The served input is one key wide. A second key would hand the client a channel the graph trusts."""
    assert set(ServeInput.__annotations__) == {"messages"}, (
        "a key was added to the served graph's input surface. Every one of them is something "
        "an HTTP caller can now write into state"
    )
    assert set(ServeOutput.__annotations__) == {"messages", "answer"}


def test_pinned_schemas_is_reachable_only_from_the_measured_graph() -> None:
    """A pin replaces the ranked shortlist, so that config is not measuring its own router."""
    from governed_bi.serve.state import ServeState

    assert "pinned_schemas" in ServeState.__annotations__
    assert "pinned_schemas" not in ServeInput.__annotations__, (
        "pinned_schemas reached the served input surface; a client could now replace the "
        "router's shortlist, and the record would say routing ran"
    )


def test_an_config_that_replayed_a_shortlist_says_so_on_its_provenance_line() -> None:
    """Pinning is a note, not a mislabelled config: four channel nodes are model calls, so an unpinned shortlist moves.
    """
    from governed_bi.eval.provenance import reconciliation_lines
    from governed_bi.registry.config_profiles import config_profile

    profile = config_profile("v4")
    row = {
        "question_id": "q1",
        "corpus_content_hash": profile.corpus_content_hash,
        "settings_resolved": {"question_subset": profile.question_subset},
    }

    live = reconciliation_lines([row], profile)
    assert not any("routing_pinned" in line for line in live), (
        "a config that routed for itself was annotated as pinned"
    )

    pinned = reconciliation_lines([dict(row, routing_pinned=True)], profile)
    assert any("routing_pinned" in line for line in pinned), (
        f"a replayed shortlist is invisible on the provenance line: {pinned}"
    )
    assert any("1 of 1 turns replayed" in line for line in pinned), pinned
