"""Project the corpus's join structure into an in-memory graph (networkx).

One consumer, one shape: :mod:`governed_bi.graph.planner` walks ``NODE_TABLE``
nodes over ``JOINS_TO`` edges to plan joins, find the join neighbourhood L4
licenses, and detect a missing cross-schema edge. That is the whole contract.

| Edge     | From -> To     | Sourced from                              |
|----------|----------------|-------------------------------------------|
| JOINS_TO | Table -> Table | ``join`` (on/cardinality/cost/confidence) |

This used to also project column, term and metric nodes with ``HAS_COLUMN``,
``REFERENCES``, ``BINDS_TO``, ``DERIVED_FROM`` and term-relation edges. Nothing
in the serve path ever walked them — only ``tests/test_graph.py`` did — while
:func:`build_graph` runs once per turn, so every turn paid to build about 60% of
a graph it would not read. The asset-to-asset graph the **API** serves is a
separate derivation in :func:`governed_bi.viz.presenter.knowledge_graph`, built
straight from the corpus; that one is the richer view, and it is the one to
extend if the audit surface needs more edge types.

The graph is a rebuildable projection, not a source of truth: it assumes the
corpus already passed ``validate_corpus`` (all references resolve). Join
endpoints are still guarded, because ``for_analyst()`` can drop an excluded table
while a surviving join still points at it, and networkx would otherwise
auto-create a bare, kind-less node — re-materializing the excluded asset in the
Analyst-facing graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from ..corpus.schemas import JoinAsset, TableAsset

if TYPE_CHECKING:
    from ..corpus import Corpus

# ── Node kinds (the ``kind`` node attribute) ──
NODE_TABLE = "table"

# ── Edge types (the ``type`` edge attribute, also the MultiDiGraph edge key) ──
EDGE_JOINS_TO = "JOINS_TO"


def build_graph(corpus: "Corpus") -> nx.MultiDiGraph:
    """Build the join graph from a parsed corpus. Rebuildable at any time."""
    g = nx.MultiDiGraph()

    for a in corpus.assets:
        if isinstance(a, TableAsset):
            g.add_node(
                a.id,
                kind=NODE_TABLE,
                physical_name=a.physical_name,
                schema=a.schema,
                row_count=a.row_count,
            )

    for a in corpus.assets:
        if isinstance(a, JoinAsset) and a.left_table in g and a.right_table in g:
            g.add_edge(
                a.left_table,
                a.right_table,
                key=a.id,
                type=EDGE_JOINS_TO,
                join_id=a.id,
                on=a.on,
                cardinality=a.cardinality.value if a.cardinality else None,
                cost=a.cost,
                confidence=a.confidence,
            )

    return g
