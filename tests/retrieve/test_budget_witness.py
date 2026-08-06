"""What the per-type cap discarded, and the tie-break it lacked.

``apply_budgets`` filtered over-budget hits out of ``selected`` and ``attributions`` and counted
nothing, so a 9th-ranked gold table did not exist to the turn and ``table_coverage`` reported the
resulting miss as though retrieval had never found it. Measured offline: 44% of questions whose
schema was routed correctly have a gold table outside the 8-table cap, median worst rank 9 — one
position past the budget.
"""

from __future__ import annotations

from governed_bi.register.assets import AssetType
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
    # A drop at 0.97 and a drop at 0.01 want opposite decisions; a bare count cannot tell them
    # apart, so the best surviving-nothing score is carried too.
    assert result.best_dropped_score["table"] == hits[table_cap][2]


def test_nothing_dropped_reports_nothing() -> None:
    """A turn under budget must stay byte-identical, so `context_hash` does not move."""
    result = apply_budgets([("sales.a", AssetType.table, 0.9)], pulled_in=[])
    assert result.dropped == {}
    assert result.best_dropped_score == {}


def test_an_n_a_budget_counts_as_a_drop_rather_than_vanishing() -> None:
    """``negative_example`` ships ``"n/a"`` — zero ranked hits, ever.

    v1's defect was a missing budget defaulting to 0 and deleting every hit of a type "with no
    record". A declared zero with no record is the same silence wearing a declaration.
    """
    assert budget_for(AssetType.negative_example) == "n/a"
    result = apply_budgets(
        [("sales.neg", AssetType.negative_example, 0.9)], pulled_in=[]
    )
    assert result.hits == []
    assert result.dropped == {"negative_example": 1}


def test_equal_scores_break_on_the_asset_id_not_on_dict_order() -> None:
    """Every other ordering in the retrieval path is ``(-score, str(id))``; this one was not.

    ``connect.py`` was given three explicit sorts after a cross-process coverage tremor of one
    question in 114 was traced to hash order. Ties are no longer rare here: within-facet scaling
    puts every channel's best hit at exactly 1.0, so the 8-table boundary sees them often.
    """
    cap = budget_for(AssetType.table)
    assert isinstance(cap, int)
    tied = [(f"sales.{name}", AssetType.table, 1.0) for name in "zyxwvutsrqponm"]
    forward = apply_budgets(tied, pulled_in=[])
    backward = apply_budgets(list(reversed(tied)), pulled_in=[])
    assert [h[0] for h in forward.hits] == [h[0] for h in backward.hits], (
        "input order changed which equal-scoring tables survived the cap"
    )
    assert [h[0] for h in forward.hits] == sorted(h[0] for h in forward.hits)[:cap]
