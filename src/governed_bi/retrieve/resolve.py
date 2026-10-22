"""Total reference closure to a fixpoint. See ADR 0005.

``connect`` is a bounded choice and must report its path. This walk has no decline; the size bound lives in the caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Hashable


def resolve[T: Hashable](
    ids: Set[T],
    *,
    references: Mapping[T, Set[T]],
) -> set[T]:
    closure: set[T] = set(ids)
    frontier = list(ids)
    while frontier:
        current = frontier.pop()
        for neighbour in references.get(current, ()):
            if neighbour not in closure:
                closure.add(neighbour)
                frontier.append(neighbour)
    return closure
