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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx

    from ..corpus import Corpus


def build_graph(corpus: "Corpus") -> "nx.MultiDiGraph":
    """Build the property graph from a parsed corpus. Rebuildable at any time.

    Pass the ``Corpus.for_server()`` view so ``governance.excluded`` assets are
    already absent (D6): the server-facing graph must not surface excluded
    tables/columns, same as retrieval and the presented schema.
    """
    raise NotImplementedError("graph projection pending; uses networkx.MultiDiGraph")
