"""Bounded Steiner connectivity over an undirected join graph.

``resolve`` is total; ``connect`` is not. Exceeding ``max_points`` is a
refusal — never a truncated path presented as success.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from typing import Hashable

__all__ = ["ConnectResult", "canon_edge", "components", "connect"]


@dataclass(frozen=True)
class ConnectResult:
    """Outcome of a connect attempt.

    ``path`` holds the chosen undirected edges when the terminals are joined
    within the Steiner-point cap. ``added`` are intermediate nodes not in the
    original terminal set. ``declined`` is True on refusal (disconnected or
    over the cap); then ``added`` is empty.
    """

    path: tuple[tuple[Hashable, Hashable], ...]
    added: frozenset[Hashable]
    declined: bool


def connect(
    terminals: Set[Hashable],
    *,
    edges: Set[tuple[Hashable, Hashable]],
    max_points: int,
) -> ConnectResult:
    """Join ``terminals`` on ``edges`` with at most ``max_points`` Steiner nodes.

    Uses a greedy shortest-path joining tree (stdlib BFS only). Steiner points
    are intermediate nodes not in the original ``terminals``. If the tree would
    need more than ``max_points`` of them, or the terminals are not in one
    connected component, the result is declined.
    """
    terms = set(terminals)
    if len(terms) <= 1:
        return ConnectResult(path=(), added=frozenset(), declined=False)

    adj = _adjacency(edges)
    missing = [t for t in terms if t not in adj]
    if missing:
        # Isolated terminals with no incident edges cannot be joined (unless
        # there is only one terminal, already handled).
        return ConnectResult(path=(), added=frozenset(), declined=True)

    tree_nodes: set[Hashable] = set()
    tree_edges: list[tuple[Hashable, Hashable]] = []
    remaining = set(terms)

    # **Sorted, not ``next(iter(...))``.** ``remaining`` holds table-id strings and Python
    # randomises string hashing per process, so the greedy builder started from a different
    # terminal each process and added different (equally minimal) Steiner points -- which go
    # into ``licensed``, which ``eval.datalake.table_coverage`` reads. Measured on one corpus:
    # one process, coverage delta 0.0000; two processes, 0.6316 vs 0.6228 [retired] on one of
    # 114 questions. Both levels are void; the cross-process gap is the whole finding.
    # ``key=str`` because terminals are ``Hashable``; the sort must be stable, not meaningful.
    seed = min(remaining, key=str)
    tree_nodes.add(seed)
    remaining.remove(seed)

    while remaining:
        path = _nearest_path(adj, tree_nodes, remaining)
        if path is None:
            return ConnectResult(path=(), added=frozenset(), declined=True)
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            tree_edges.append(canon_edge(a, b))
            tree_nodes.add(b)
        remaining -= set(path)

    added = frozenset(tree_nodes - terms)
    if len(added) > max_points:
        return ConnectResult(path=(), added=frozenset(), declined=True)

    # Deduplicate edges while preserving a stable order.
    seen: set[tuple[Hashable, Hashable]] = set()
    unique: list[tuple[Hashable, Hashable]] = []
    for edge in tree_edges:
        if edge not in seen:
            seen.add(edge)
            unique.append(edge)

    return ConnectResult(path=tuple(unique), added=added, declined=False)


def components(
    nodes: Set[Hashable], *, edges: Set[tuple[Hashable, Hashable]]
) -> tuple[frozenset[Hashable], ...]:
    """Partition ``nodes`` by which connected component of ``edges`` each one sits in.

    The same graph walk as :func:`connect`, reported as a partition instead of a verdict.
    Needed because ``connect``'s single "no" cannot distinguish a genuinely missing path
    from a terminal set that spans shortlisted schemas and is therefore disconnected *by
    construction* — which is why pooled turns declined ``missing_join_path``. A node with
    no incident edge is its own component, so a single-table turn is a component of one.

    Deterministic: components ordered by their lexicographically first member.
    """
    adj = _adjacency(edges)
    remaining = set(nodes)
    out: list[frozenset[Hashable]] = []
    while remaining:
        seed = min(remaining, key=str)
        seen = {seed}
        queue: deque[Hashable] = deque([seed])
        while queue:
            node = queue.popleft()
            for neighbour in adj.get(node, ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        group = frozenset(seen & remaining)
        remaining -= seen
        out.append(group)
    return tuple(sorted(out, key=lambda g: min((str(n) for n in g), default="")))


def _adjacency(
    edges: Iterable[tuple[Hashable, Hashable]],
) -> dict[Hashable, set[Hashable]]:
    adj: dict[Hashable, set[Hashable]] = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    return adj


def canon_edge(a: Hashable, b: Hashable) -> tuple[Hashable, Hashable]:
    """One undirected edge, in a canonical order. **Exported, not private.**

    :mod:`~governed_bi.retrieve.structure` builds the edge set this module searches, so
    both must agree on which of ``(a, b)`` / ``(b, a)`` an edge *is*. Two spellings raise
    nowhere: the builder emits one orientation, ``connect`` looks for the other, and every
    multi-table turn declines ``missing_join_path``.
    """
    return (a, b) if str(a) <= str(b) else (b, a)


def _nearest_path(
    adj: Mapping[Hashable, Set[Hashable]],
    sources: Set[Hashable],
    goals: Set[Hashable],
) -> list[Hashable] | None:
    """BFS from every node in ``sources``; return a shortest path to any goal.

    The path includes the source endpoint (already in the tree) and the goal.
    Intermediate nodes are candidate Steiner points.
    """
    if not sources or not goals:
        return None

    # **Sorted at both traversal points, and both were needed.** ``sources``, ``goals`` and
    # ``adj`` values are sets of table-id strings under per-process hash randomisation, so
    # which equal-length shortest path BFS finds decided which Steiner points landed in
    # ``licensed``. Sorting only the seed in ``connect`` was measured and did not fix it:
    # queue order and neighbour order are two more places the tie is broken by hash.
    parent: dict[Hashable, Hashable | None] = {s: None for s in sources}
    queue: deque[Hashable] = deque(sorted(sources, key=str))

    found: Hashable | None = None
    while queue:
        node = queue.popleft()
        if node in goals and node not in sources:
            found = node
            break
        # A source that is also a remaining goal (should not happen) — skip.
        for neighbour in sorted(adj.get(node, ()), key=str):
            if neighbour in parent:
                continue
            parent[neighbour] = node
            if neighbour in goals:
                found = neighbour
                queue.clear()
                break
            queue.append(neighbour)

    if found is None:
        return None

    path: list[Hashable] = [found]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]])  # type: ignore[arg-type]
    path.reverse()
    return path
