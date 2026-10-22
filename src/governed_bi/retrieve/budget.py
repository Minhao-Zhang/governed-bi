"""Every type has an explicit cap. Closure pulls do not consume it. See ADR 0005."""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from governed_bi.registry.assets import ASSET_REGISTER, AssetType, Budget

__all__ = ["BudgetResult", "budget_for", "apply_budgets"]

RankedHit = tuple[str, AssetType, float]

PulledIn = tuple[str, AssetType]


@dataclass(frozen=True, slots=True)
class BudgetResult:
    hits: list[RankedHit]
    pulled_in: list[PulledIn]
    #: Discarded ranked hits, so a cap is not the same fact as a miss.
    dropped: dict[str, int] = field(default_factory=dict)
    best_dropped_score: dict[str, float] = field(default_factory=dict)


def budget_for(asset_type: AssetType) -> Budget:
    """A missing register row raises. A silent zero would hide that type forever."""
    return ASSET_REGISTER[asset_type].budget


def apply_budgets(
    hits: Sequence[RankedHit],
    *,
    pulled_in: Sequence[PulledIn],
) -> BudgetResult:
    """Cap ranked hits per type. ``pulled_in`` is unchanged and does not consume the cap.

    Ties break on asset id. The score ceiling still clamps, so equals occur and dict order swapped them across
    processes.
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
