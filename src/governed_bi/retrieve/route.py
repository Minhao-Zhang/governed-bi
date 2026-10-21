"""Schema scoring from faceted hits.

Each channel casts one vote per schema (its strongest hit); votes are summed.
Optional ``weights`` multiply a channel's vote (default 1.0).
"""


from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Hashable


def route[F: Hashable, S: Hashable](
    hits: Iterable[tuple[F, S, float]],
    *,
    weights: Mapping[F, float] | None = None,
) -> list[tuple[S, float]]:
    """Aggregate ``(channel, schema, score)`` hits into per-schema totals.

    Per schema: sum over channels of that channel's max hit score. Returns
    ``(schema, score)`` pairs. ``weights`` multiplies a channel's vote (default 1.0).
    """
    # schema → channel → max score seen
    by_schema: dict[S, dict[F, float]] = defaultdict(dict)
    for channel, schema, score in hits:
        channel_scores = by_schema[schema]
        prev = channel_scores.get(channel)
        if prev is None or score > prev:
            channel_scores[channel] = score

    scale = weights or {}
    return [
        (
            schema,
            sum(float(scale.get(channel, 1.0)) * score for channel, score in channel_scores.items()),
        )
        for schema, channel_scores in by_schema.items()
    ]
