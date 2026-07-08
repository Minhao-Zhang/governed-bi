"""Steiner-tree join planning (Server step 6).

Given the tables a query needs, find the minimum-cost set of joins that connects
them over the inferred FK graph. Low-``confidence`` inferred joins get a **cost
penalty**; the lowest join confidence on the chosen path **propagates to the
reliability stamp** (Server "three points" #2).

The plan is an approximate minimum Steiner tree over the undirected projection of
the ``JOINS_TO`` edges. Intermediate tables the query did not ask for may appear
as Steiner points (e.g. connecting ``customers`` and ``rootbeer`` pulls in
``transaction``).

Cost model + its role: the planning weight is a **tunable heuristic**,
``cost * (1 + LOW_CONFIDENCE_PENALTY * (1 - confidence))`` (tune the penalty and
per-join ``cost`` on the eval). The plan currently feeds two consumers: L4
licensing (its Steiner points widen the term-semantics scope, alongside the FK
``join_neighborhood`` below) and the reliability stamp (``min_confidence``). A
plan-consuming SQL generator that emits the joined query is the LLM seam; today
the deterministic template generator handles the single-metric path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import networkx as nx
from networkx.algorithms.approximation import steiner_tree

from .projection import EDGE_JOINS_TO, NODE_TABLE

# How hard a low-confidence join is penalized in the cost used for planning.
# effective_weight = cost * (1 + penalty * (1 - confidence)); confidence 1.0 keeps
# the raw cost, confidence 0.0 doubles it at penalty 1.0. Tune on the eval.
LOW_CONFIDENCE_PENALTY = 1.0


@dataclass(frozen=True)
class JoinPlan:
    join_ids: list[str]  # joins to apply, in order
    min_confidence: float  # lowest join confidence on the path (-> reliability stamp)


def _join_graph(graph: nx.MultiDiGraph) -> nx.Graph:
    """Undirected weighted projection of the ``JOINS_TO`` edges.

    Parallel joins between the same pair of tables collapse to the cheapest edge
    (after the confidence penalty). Weight, ``join_id``, and ``confidence`` ride
    on each surviving edge so the plan can be recovered from the Steiner tree.
    """
    ug = nx.Graph()
    for node, data in graph.nodes(data=True):
        if data.get("kind") == NODE_TABLE:
            ug.add_node(node)

    for u, v, data in graph.edges(data=True):
        if data.get("type") != EDGE_JOINS_TO:
            continue
        cost = data.get("cost") if data.get("cost") is not None else 1.0
        confidence = data.get("confidence") if data.get("confidence") is not None else 1.0
        weight = cost * (1.0 + LOW_CONFIDENCE_PENALTY * (1.0 - confidence))
        if ug.has_edge(u, v) and ug[u][v]["weight"] <= weight:
            continue
        ug.add_edge(u, v, weight=weight, join_id=data["join_id"], confidence=confidence)
    return ug


def join_neighborhood(
    graph: nx.MultiDiGraph, table_ids: set[str], *, hops: int = 1
) -> set[str]:
    """The FK join-neighborhood of ``table_ids``: every table reachable within
    ``hops`` undirected ``JOINS_TO`` edges, INCLUDING the valid input ids.

    Builds an undirected adjacency over the ``JOINS_TO`` edges among ``NODE_TABLE``
    nodes and BFS from each input id up to ``hops`` hops. Input ids that are not
    table nodes in the graph are ignored (they contribute nothing). Deterministic.

    Used by the server to decouple L4 licensing from retrieval recall: a table the
    lexical retriever missed but the answer legitimately needs (a near FK neighbor
    of a retrieved table) is still licensed. This is safe because L3 guards every
    column independently; see ``server.flow._licensed_tables``.
    """
    ug = _join_graph(graph)
    reached = {t for t in table_ids if t in ug}
    frontier = set(reached)
    for _ in range(max(hops, 0)):
        nxt: set[str] = set()
        for node in frontier:
            for neighbor in ug.neighbors(node):
                if neighbor not in reached:
                    nxt.add(neighbor)
        reached |= nxt
        frontier = nxt
        if not frontier:
            break
    return reached


def plan_joins(graph: nx.MultiDiGraph, required_tables: set[str]) -> JoinPlan:
    """Approximate a minimum-cost Steiner tree connecting ``required_tables``.

    ``required_tables`` are table-asset ids. Raises ``ValueError`` if any is not a
    table node in the graph, or if the required tables span disconnected join
    components (no join path links them).
    """
    required = set(required_tables)
    ug = _join_graph(graph)

    missing = required - set(ug.nodes)
    if missing:
        raise ValueError(f"not table nodes in the join graph: {sorted(missing)}")

    # Zero or one table needs no joins; the reliability stamp is unaffected.
    if len(required) <= 1:
        return JoinPlan(join_ids=[], min_confidence=1.0)

    # Restrict planning to the connected component of the required tables. This
    # both enforces "required tables must be connected" and avoids a networkx
    # crash: steiner_tree's default (mehlhorn) indexes shortest paths for every
    # node in the graph, so an unrelated disconnected table (another fact cluster,
    # a non-FK lookup) would raise KeyError if left in.
    component = nx.node_connected_component(ug, next(iter(required)))
    if not required <= component:
        raise ValueError(
            f"required tables are not connected in the join graph: {sorted(required)}"
        )

    tree = steiner_tree(ug.subgraph(component), list(required), weight="weight")

    # Recover an incremental join order: BFS from a deterministic start so each
    # emitted join attaches a new table to the already-connected set.
    start = sorted(required)[0]
    visited = {start}
    order: list[str] = []
    confidences: list[float] = []
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in sorted(tree.neighbors(node)):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            edge = tree[node][neighbor]
            order.append(edge["join_id"])
            confidences.append(edge["confidence"])
            queue.append(neighbor)

    return JoinPlan(join_ids=order, min_confidence=min(confidences) if confidences else 1.0)
