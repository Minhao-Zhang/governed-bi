"""Reference closure over hit assets.

A total function of the hit set: keep following outgoing references until
fixpoint. Idempotent — ``resolve(resolve(x)) == resolve(x)``.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Hashable


def resolve(
    ids: Set[Hashable],
    *,
    references: Mapping[Hashable, Set[Hashable]],
) -> set[Hashable]:
    """Return the reference closure of ``ids`` under ``references``.

    ``references`` maps each asset id to the set of ids it points at. Missing
    keys are treated as having no outgoing refs. The input ids are always
    retained, even when absent from ``references``.
    """
    closure: set[Hashable] = set(ids)
    frontier = list(ids)
    while frontier:
        current = frontier.pop()
        for neighbour in references.get(current, ()):
            if neighbour not in closure:
                closure.add(neighbour)
                frontier.append(neighbour)
    return closure
