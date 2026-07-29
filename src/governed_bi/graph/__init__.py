"""Graph projection + join planning.

The graph is a **derived, rebuildable projection** of the YAML corpus (D9),
never authored directly. BIRD uses an in-memory ``networkx`` graph for
Steiner-tree join planning; Neo4j is the optional enterprise-scale projection.

- ``projection``: build the property graph from parsed assets.
- ``planner``: Steiner-tree join planning over the inferred FK graph.
"""

from __future__ import annotations

from .planner import (
    JoinPlan,
    MissingJoinPath,
    detect_missing_join_path,
    join_neighborhood,
    plan_joins,
)
from .projection import EDGE_JOINS_TO, NODE_TABLE, build_graph

__all__ = [
    "build_graph",
    "JoinPlan",
    "MissingJoinPath",
    "detect_missing_join_path",
    "join_neighborhood",
    "plan_joins",
    "EDGE_JOINS_TO",
    "NODE_TABLE",
]
