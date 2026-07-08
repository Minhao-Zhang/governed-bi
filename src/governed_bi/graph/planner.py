"""Steiner-tree join planning (Server step 6).

Given the tables/columns a query needs, find the minimum-cost set of joins that
connects them over the inferred FK graph. Low-``confidence`` inferred joins get
a **cost penalty**; a below-threshold join in the chosen path **propagates to
the reliability stamp** (Server §"three points" #2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx


@dataclass(frozen=True)
class JoinPlan:
    join_ids: list[str]  # joins to apply, in order
    min_confidence: float  # lowest join confidence on the path (-> reliability stamp)


def plan_joins(graph: "nx.MultiDiGraph", required_tables: set[str]) -> JoinPlan:
    """Approximate a minimum-cost Steiner tree connecting ``required_tables``."""
    raise NotImplementedError("Steiner-tree planning pending; networkx approximation")
