"""Per-type retrieval budgets from the asset register (ADR 0005 §2.5).

Every type has an explicit budget — no ``dict.get(..., 0)`` default.
``resolve``/``connect`` pulls do not consume budget.
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from governed_bi.register.assets import ASSET_REGISTER, AssetType, Budget

__all__ = ["BudgetResult", "budget_for", "apply_budgets"]

#: One ranked hit: ``(asset_id, asset_type, score)``.
RankedHit = tuple[str, AssetType, float]

#: One structural addition: ``(asset_id, asset_type)``.
PulledIn = tuple[str, AssetType]


@dataclass(frozen=True, slots=True)
class BudgetResult:
    """Post-budget survivors, with ranked hits and pulled-in kept apart."""

    hits: list[RankedHit]
    pulled_in: list[PulledIn]
    #: ``{asset_type -> count of ranked hits discarded by the cap}``.
    dropped: dict[str, int] = field(default_factory=dict)
    #: Best score that did not survive, per type (tight cap vs noise tail).
    best_dropped_score: dict[str, float] = field(default_factory=dict)


def budget_for(asset_type: AssetType) -> Budget:
    """Return the register budget for ``asset_type``.

    Raises ``KeyError`` if the type is missing from the register — that is the
    import-time guard's job to prevent, and a silent ``0`` must not replace it.
    """
    return ASSET_REGISTER[asset_type].budget


def apply_budgets(
    hits: Sequence[RankedHit],
    *,
    pulled_in: Sequence[PulledIn],
) -> BudgetResult:
    """Cap ranked hits per type; keep every pulled-in asset outside the caps.

    Hits are sorted by score descending, then accepted in that order until the
    type's budget is exhausted:

    * ``int`` — keep at most that many ranked hits of the type
    * ``"all"`` — keep every ranked hit of the type
    * ``"n/a"`` — keep zero ranked hits of the type (still allowed in pulled_in)

    ``pulled_in`` is returned unchanged in membership and does not consume budget.

    What the caps discarded is returned in :attr:`BudgetResult.dropped` and
    :attr:`BudgetResult.best_dropped_score`, because a cap with no witness is indistinguishable
    from a retrieval that never found the asset.

    **The sort now breaks ties on the asset id.** It was ``key=lambda h: h[2]`` alone, while
    every other ordering in the retrieval path is ``(-score, str(id))`` — ``pass_two`` and
    ``semantic_search`` both say why, and ``retrieve/connect.py`` was given three explicit
    sorts for the same reason after a cross-process coverage tremor of one question in 114 was
    traced to hash order. Here the tie was resolved by ``hits_by_facet`` iteration order, so at
    the 8-table boundary two equal-scoring tables swapped on a dict ordering — and equal scores
    are no longer rare, because ``facets._within_facet_scale`` puts every channel's best hit at
    exactly 1.0.
    """
    ranked = sorted(hits, key=lambda h: (-h[2], str(h[0])))
    taken: dict[AssetType, int] = {}
    kept: list[RankedHit] = []
    dropped: dict[str, int] = {}
    best_dropped: dict[str, float] = {}

    def _drop(asset_type: AssetType, score: float) -> None:
        key = getattr(asset_type, "value", str(asset_type))
        dropped[key] = dropped.get(key, 0) + 1
        if key not in best_dropped or score > best_dropped[key]:
            best_dropped[key] = float(score)

    for asset_id, asset_type, score in ranked:
        cap = budget_for(asset_type)
        if cap == "n/a":
            _drop(asset_type, score)
            continue
        if cap == "all":
            kept.append((asset_id, asset_type, score))
            continue
        n = taken[asset_type] if asset_type in taken else 0
        if n < cap:
            kept.append((asset_id, asset_type, score))
            taken[asset_type] = n + 1
        else:
            _drop(asset_type, score)

    return BudgetResult(
        hits=kept,
        pulled_in=list(pulled_in),
        dropped=dropped,
        best_dropped_score=best_dropped,
    )
