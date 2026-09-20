"""Reference closure over hit assets.

A total function of the hit set: keep following outgoing references until fixpoint.
Idempotent — ``resolve(resolve(x)) == resolve(x)``.

**Total, and ``connect`` is not** — ``connect.py``'s own docstring draws that line, and both
acceptance contracts assert it (``tests/retrieve/test_scoring_contract.py`` property 4,
``tests/retrieve/test_structure_contract.py``). The distinction is principled rather than
historical: ``connect`` *searches*, so stopping early means something, and it returns a
``ConnectResult`` carrying ``declined``. This walks to a fixpoint, where stopping early buys
nothing a caller can use. **So the size bound on the closure lives in the caller**
(``serve/nodes/route_retrieve.py::resolve_node``, knob ``max_resolve_additions``), which is
also where every other retrieval bound is read and where a decline can be turned into a
terminal reason.

**Why the caller bounds it at all**, recorded here because this is where a reader looks for
the closure's cost. Every table the closure reaches enters ``licensed``, which is what the
TABLES layer accepts, and every asset it reaches enters ``pulled_in``, which is what the
prompt renders. So a corpus author widens both by adding one ``references`` edge, with no
access-policy change and nothing to review against. Measured on ``../BIRD-corpus`` (13,304
assets, 57 schemas) before a bound was chosen:

* from one asset: median 15 additions, p90 42, p99 118, max 181. The largest fan-outs are
  ``few_shot`` (181, it links every table its SQL names) and ``join`` (130) — at depth 1. A
  depth bound would be the wrong axis.
* from a realistic seed (``route_top_n`` schemas, ``table`` budget 8): median 52-69,
  p99 142-218, max 279.
* from every table of the three largest schemas (144 tables): **1,480 additions**, 11% of the
  corpus — so the pathological shape is reachable on the corpus that ships, and the only
  thing in front of it is the ``table`` budget of 8, whose placement ``govern/bounds.py``
  already records as costing a wrong refusal.
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
