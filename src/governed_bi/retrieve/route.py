"""Schema scoring from faceted hits.

Each facet casts one vote per schema — its strongest hit there — and votes are
summed. Volume inside a single facet does not accumulate; agreement across
facets does.
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

    For each schema: sum, over facets, of the max hit score of that facet for
    that schema. Returns ``(schema, score)`` pairs suitable for ``dict(...)``.

    ``weights`` multiplies a facet's vote, defaulting to 1.0 for any facet not named — so
    omitting it, or passing the shipped 1.0/1.0, is exactly the previous behaviour and the
    sealed contract in ``tests/retrieve/test_scoring_contract.py`` is unaffected.

    **It exists because ``facet_weight_schema`` and ``facet_weight_other`` did not.** Both are
    declared ``Role.comparability`` knobs in ``register/knobs.py`` — the first with the
    rationale *"the schema facet's vote arguably deserves more ... but no data supports a
    multiplier"* — and no code read either of them, because this function took no weights at
    all. A knob stamped into run identity that cannot change a result is not a knob; it is a
    claim about the run that happens to be false. Wiring it at 1.0 changes nothing today and
    makes the multiplier expressible when data does support one.
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
