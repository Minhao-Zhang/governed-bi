"""Per-type retrieval budgets, read from the asset register.

Budgets live as a column of :data:`~governed_bi.register.assets.ASSET_REGISTER`.
Looking them up via ``dict.get(cls, 0)`` is the defect that made
``NegativeExampleAsset`` structurally unreachable in v1: the type existed, the
budget table forgot it, and the default zero deleted every hit of that type
with no record. Every type has an explicit budget here — including the literals
``"all"`` and ``"n/a"`` — so there is no default to fall through to.

Applied after pass two and dedup (ADR 0005 §2.5). Assets pulled in by
``resolve`` / ``connect`` do not consume budget and stay distinguishable from
ranked hits.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    """
    ranked = sorted(hits, key=lambda h: h[2], reverse=True)
    taken: dict[AssetType, int] = {}
    kept: list[RankedHit] = []

    for asset_id, asset_type, score in ranked:
        cap = budget_for(asset_type)
        if cap == "n/a":
            continue
        if cap == "all":
            kept.append((asset_id, asset_type, score))
            continue
        n = taken[asset_type] if asset_type in taken else 0
        if n < cap:
            kept.append((asset_id, asset_type, score))
            taken[asset_type] = n + 1

    return BudgetResult(hits=kept, pulled_in=list(pulled_in))
