"""Project the YAML corpus into an in-memory property graph (networkx).

Edges (all derived from YAML; Neo4j never authored) per ``docs/asset-schemas.md``:

| Edge          | From -> To                | Sourced from            |
|---------------|---------------------------|-------------------------|
| HAS_COLUMN    | Table -> Column           | inline ``columns[]``    |
| JOINS_TO      | Table -> Table            | ``join`` (on/card/cost) |
| REFERENCES    | Column -> Column          | ``column.references``   |
| BINDS_TO      | Term -> Metric/Table/Col  | ``term.binding``        |
| SYNONYM_OF /  | Term -> Term              | ``term.related_terms``  |
| BROADER_THAN /|                           |                         |
| USES          |                           |                         |
| DERIVED_FROM  | Metric -> Table/Column    | ``metric.base_table``   |

The graph is a rebuildable projection, not a source of truth: it assumes the
corpus already passed ``validate_corpus`` (all references resolve), so edges are
added unconditionally. Feeding an unvalidated corpus with a dangling reference
would create a bare target node; validate first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from ..corpus.ids import derive_column_id
from ..corpus.schemas import JoinAsset, MetricAsset, TableAsset, TermAsset

if TYPE_CHECKING:
    from ..corpus import Corpus

# ── Node kinds (the ``kind`` node attribute) ──
NODE_TABLE = "table"
NODE_COLUMN = "column"
NODE_TERM = "term"
NODE_METRIC = "metric"

# ── Edge types (the ``type`` edge attribute, also the MultiDiGraph edge key) ──
EDGE_HAS_COLUMN = "HAS_COLUMN"
EDGE_JOINS_TO = "JOINS_TO"
EDGE_REFERENCES = "REFERENCES"
EDGE_BINDS_TO = "BINDS_TO"
EDGE_DERIVED_FROM = "DERIVED_FROM"
# Term -> term edges use the relation value upper-cased (SYNONYM_OF / BROADER_THAN / USES).


def build_graph(corpus: "Corpus") -> nx.MultiDiGraph:
    """Build the property graph from a parsed corpus. Rebuildable at any time.

    Pass the ``Corpus.for_server()`` view so ``governance.excluded`` assets are
    already absent (D6): the server-facing graph must not surface excluded
    tables/columns, same as retrieval and the presented schema.
    """
    g = nx.MultiDiGraph()

    # Pass 1: create every node (tables + inline columns, terms, metrics) so the
    # cross-asset edges in pass 2 never auto-create a bare, kind-less node.
    for a in corpus.assets:
        if isinstance(a, TableAsset):
            g.add_node(
                a.id,
                kind=NODE_TABLE,
                physical_name=a.physical_name,
                schema=a.schema,
                row_count=a.row_count,
            )
            for col in a.columns:
                col_id = derive_column_id(a.id, col.physical_name)
                g.add_node(
                    col_id,
                    kind=NODE_COLUMN,
                    physical_name=col.physical_name,
                    table=a.id,
                    role=col.role.value if col.role else None,
                    reliability=col.reliability.status.value,
                    excluded=col.governance.excluded,
                )
                g.add_edge(a.id, col_id, key=EDGE_HAS_COLUMN, type=EDGE_HAS_COLUMN)
        elif isinstance(a, TermAsset):
            g.add_node(a.id, kind=NODE_TERM, name=a.name)
        elif isinstance(a, MetricAsset):
            g.add_node(
                a.id,
                kind=NODE_METRIC,
                name=a.name,
                base_table=a.base_table,
                expression=a.expression,
            )

    # Pass 2: cross-asset edges. Endpoints are guarded: for_server() can drop an
    # excluded table/column while a surviving FK reference or join still points at
    # it, and networkx would otherwise auto-create a bare, kind-less node,
    # re-materializing the excluded asset in the server-facing graph. Skipping such
    # an edge keeps the graph free of both phantom nodes and excluded assets.
    def _edge(u: str, v: str, **attrs: object) -> None:
        if u in g and v in g:
            g.add_edge(u, v, **attrs)

    for a in corpus.assets:
        if isinstance(a, TableAsset):
            for col in a.columns:
                if col.references:
                    col_id = derive_column_id(a.id, col.physical_name)
                    _edge(col_id, col.references, key=EDGE_REFERENCES, type=EDGE_REFERENCES)
        elif isinstance(a, JoinAsset):
            _edge(
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
        elif isinstance(a, TermAsset):
            if a.binding:
                _edge(a.id, a.binding.asset_id, key=EDGE_BINDS_TO, type=EDGE_BINDS_TO)
            for rel in a.related_terms:
                edge_type = rel.relation.value.upper()  # e.g. "uses" -> "USES"
                _edge(a.id, rel.id, key=edge_type, type=edge_type, relation=rel.relation.value)
        elif isinstance(a, MetricAsset):
            _edge(a.id, a.base_table, key=EDGE_DERIVED_FROM, type=EDGE_DERIVED_FROM)

    return g
