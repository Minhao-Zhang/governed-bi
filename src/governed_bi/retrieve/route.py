"""Schema scoring from faceted hits.

Each facet casts one vote per schema (its strongest hit); votes are summed.
Optional ``weights`` multiply a facet's vote (default 1.0).
"""


from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Hashable


def route(
    hits: Iterable[tuple[Hashable, Hashable, float]],
    *,
    weights: Mapping[Hashable, float] | None = None,
) -> list[tuple[Hashable, float]]:
    """Aggregate ``(facet, schema, score)`` hits into per-schema totals.

    Per schema: sum over facets of that facet's max hit score. Returns
    ``(schema, score)`` pairs. ``weights`` multiplies a facet's vote (default 1.0).
    """
    # schema → facet → max score seen
    by_schema: dict[Hashable, dict[Hashable, float]] = defaultdict(dict)
    for facet, schema, score in hits:
        facet_scores = by_schema[schema]
        prev = facet_scores.get(facet)
        if prev is None or score > prev:
            facet_scores[facet] = score

    scale = weights or {}
    return [
        (
            schema,
            sum(float(scale.get(facet, 1.0)) * score for facet, score in facet_scores.items()),
        )
        for schema, facet_scores in by_schema.items()
    ]
