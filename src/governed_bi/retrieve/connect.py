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

    # **Sorted, not ``next(iter(...))``.** ``remaining`` is a set of table-id *strings*, and
    # Python randomises string hashing per process unless ``PYTHONHASHSEED`` is pinned -- so the
    # greedy builder started from a different terminal in every process, produced a different
    # (equally valid, equally minimal) tree, and added different Steiner points. Those points go
    # into ``licensed``, which is what ``eval.datalake.table_coverage`` reads.
    #
    # Measured: the same corpus measured twice **in one process** gives a coverage delta of
    # exactly 0.0000, and the same corpus measured in two different processes moved by one
    # question of 114 (0.6316 vs 0.6228). A direct probe over one 4-terminal graph produced three
    # distinct Steiner sets across five hash seeds. So every cross-session comparison of a
    # coverage or licensing number carried this as noise, at roughly the size of the effects the
    # corpus work is trying to detect.
    #
    # ``key=str`` because terminals are typed ``Hashable`` and a mixed set has no natural order;
    # the sort only has to be *stable across processes*, not meaningful.
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

    :func:`connect` answers *"can these be joined"* with a yes or a no, and the no is the
    same value whether the terminals span two schemas that were never meant to be joined
    or genuinely need a path the graph does not have. That single value is why a pooled
    turn declined ``missing_join_path``: routing shortlists three schemas, pass two
    licenses tables from all three, and unrelated schemas share no edge — so the terminal
    set is disconnected *by construction* and the decline says nothing about the question.

    This is the same graph walk, reported as a partition instead of a verdict, so a caller
    can decide which component to keep. A node with no incident edge is its own component,
    which is what makes a single-table turn a component of one rather than a missing key.

    Deterministic: components are ordered by their lexicographically first member, so two
    runs over the same corpus partition identically.
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

    :mod:`~governed_bi.retrieve.structure` builds the edge set this module searches,
    so both halves must agree on which of ``(a, b)`` and ``(b, a)`` an edge *is*.
    Two spellings of that would not raise anywhere: the builder would emit one
    orientation, ``connect`` would look for the other, and every multi-table turn
    would decline ``missing_join_path`` -- which is the defect §2.8.2 was written
    about, reproduced one layer up.
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

    # **Sorted at both traversal points, and both were needed.** ``sources``, ``goals`` and every
    # ``adj`` value are sets of table-id strings, and Python randomises string hashing per
    # process. Two shortest paths of equal length are both correct, and which one BFS finds
    # decided which Steiner points landed in ``licensed`` — so the metric moved between runs of
    # the same corpus. Fixing only the seed in ``connect`` was measured and **did not** fix it:
    # the probe still produced three distinct Steiner sets across five hash seeds, because the
    # queue order and the neighbour order are two more places the tie is broken by hash.
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
