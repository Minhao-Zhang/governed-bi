"""Schema scoring from faceted hits.

Each facet casts one vote per schema — its strongest hit there — and votes are
summed. Volume inside a single facet does not accumulate; agreement across
facets does.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Hashable


def route(
    hits: Iterable[tuple[Hashable, Hashable, float]],
) -> list[tuple[Hashable, float]]:
    """Aggregate ``(facet, schema, score)`` hits into per-schema totals.

    For each schema: sum, over facets, of the max hit score of that facet for
    that schema. Returns ``(schema, score)`` pairs suitable for ``dict(...)``.
    """
    # schema → facet → max score seen
    by_schema: dict[Hashable, dict[Hashable, float]] = defaultdict(dict)
    for facet, schema, score in hits:
        facet_scores = by_schema[schema]
        prev = facet_scores.get(facet)
        if prev is None or score > prev:
            facet_scores[facet] = score

    return [(schema, sum(facet_scores.values())) for schema, facet_scores in by_schema.items()]
