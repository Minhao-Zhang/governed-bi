"""A cap must report what it discarded. A silent drop is indistinguishable from a miss."""

from __future__ import annotations

from governed_bi.registry.assets import AssetType
from governed_bi.retrieve.budget import apply_budgets, budget_for


def test_the_cap_reports_what_it_discarded() -> None:
    table_cap = budget_for(AssetType.table)
    assert isinstance(table_cap, int) and table_cap > 0
    hits = [
        (f"sales.t{i:02d}", AssetType.table, 1.0 - i / 100)
        for i in range(table_cap + 3)
    ]
    result = apply_budgets(hits, pulled_in=[])

    assert len(result.hits) == table_cap
    assert result.dropped == {"table": 3}, (
        "the cap cut three tables and reported nothing, so the loss is unattributable"
    )
    # A bare count cannot tell a near-miss from noise, so the best discarded score is kept.
    assert result.best_dropped_score["table"] == hits[table_cap][2]


def test_nothing_dropped_reports_nothing() -> None:
    """An under-cap turn must stay byte-identical, so `context_hash` does not move."""
    result = apply_budgets([("sales.a", AssetType.table, 0.9)], pulled_in=[])
    assert result.dropped == {}
    assert result.best_dropped_score == {}




def test_equal_scores_break_on_the_asset_id_not_on_dict_order() -> None:
    """The score ceiling still produces equals at the cap, so dict order must not choose the survivors."""
    cap = budget_for(AssetType.table)
    assert isinstance(cap, int)
    tied = [(f"sales.{name}", AssetType.table, 1.0) for name in "zyxwvutsrqponm"]
    forward = apply_budgets(tied, pulled_in=[])
    backward = apply_budgets(list(reversed(tied)), pulled_in=[])
    assert [h[0] for h in forward.hits] == [h[0] for h in backward.hits], (
        "input order changed which equal-scoring tables survived the cap"
    )
    assert [h[0] for h in forward.hits] == sorted(h[0] for h in forward.hits)[:cap]
