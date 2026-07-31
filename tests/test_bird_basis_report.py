"""BIRD-basis funnel / report reproduction (N15.2).

Pins the cascade against synthetic rows, and — when the fixed2 ladder artifacts
are present — against every number in
``docs/experiments/20260730T034522Z-curated-sme-error-analysis.md`` that the
tool can reproduce. Mismatches are named (tool vs report), never waved away.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.eval.bird_basis import (
    bird_basis_report,
    funnel_stage,
    question_arm_view,
    schema_pick_report,
    sme_perturbation_report,
    stage4_structural_report,
    stage_waterfall,
)
from governed_bi.eval.analysis import load_arm_rows, load_gold_sql

FIXED2 = Path(
    "runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z"
)
BIRD = Path("../BIRD-Data-Obfuscation")

# Report §1 (BIRD n=1325). Seeded table/wrong_shape differ by one cell — see
# ``test_fixed2_waterfall_matches_report``.
REPORT_WATERFALL = {
    "baseline": {
        "OK": 526,
        "retrieval": 59,
        "pick": 102,
        "table": 281,
        "wrong_shape": 141,
        "wrong_value": 193,
        "refused": 23,
        "ex": 0.397,
    },
    "seeded": {
        "OK": 626,
        "retrieval": 68,
        "pick": 105,
        "table": 139,
        "wrong_shape": 155,
        "wrong_value": 208,
        "refused": 24,
        "ex": 0.472,
    },
    "curated": {
        "OK": 779,
        "retrieval": 34,
        "pick": 89,
        "table": 60,
        "wrong_shape": 137,
        "wrong_value": 218,
        "refused": 8,
        "ex": 0.588,
    },
    "curated_sme": {
        "OK": 777,
        "retrieval": 32,
        "pick": 96,
        "table": 58,
        "wrong_shape": 127,
        "wrong_value": 228,
        "refused": 7,
        "ex": 0.586,
    },
}


def _row(**kwargs):
    base = {
        "question_id": "q1",
        "db_id": "address",
        "correct": False,
        "gold_order_sensitive": False,
        "shortlisted_schemas": ["address", "world"],
        "routed_schemas": ["address"],
        "routed_hit": True,
        "pick_hit": True,
        "generated_sql": 'SELECT 1 FROM "address"."zip_data"',
        "pred_nrows": 1,
        "gold_nrows": 1,
        "nrows_match": True,
        "refused_by": None,
        "error": None,
        "outcome": "answered",
    }
    base.update(kwargs)
    return base


def test_funnel_stage_cascade_order():
    gold = {"q1": 'SELECT city FROM "address"."zip_data"'}
    assert funnel_stage(_row(correct=True), gold) == "OK"
    assert (
        funnel_stage(
            _row(
                refused_by="no_coverage",
                generated_sql="",
                outcome="refused",
            ),
            gold,
        )
        == "refused"
    )
    assert (
        funnel_stage(
            _row(shortlisted_schemas=["world"], routed_hit=False, pick_hit=False),
            gold,
        )
        == "retrieval"
    )
    assert (
        funnel_stage(
            _row(routed_hit=False, pick_hit=False, routed_schemas=["world"]), gold
        )
        == "pick"
    )
    # Capped (exhausted) with correct route and no SQL → table, not refused.
    assert (
        funnel_stage(
            _row(refused_by="exhausted", generated_sql="", outcome="capped"),
            gold,
        )
        == "table"
    )
    miss_tables = _row(
        generated_sql='SELECT 1 FROM "address"."congress"',
        nrows_match=False,
        pred_nrows=0,
        gold_nrows=1,
    )
    assert funnel_stage(miss_tables, gold) == "table"
    shape = _row(
        generated_sql='SELECT city FROM "address"."zip_data"',
        nrows_match=False,
        pred_nrows=2,
        gold_nrows=1,
    )
    assert funnel_stage(shape, gold) == "wrong_shape"
    value = _row(
        generated_sql='SELECT city FROM "address"."zip_data"',
        nrows_match=True,
        correct=False,
    )
    assert funnel_stage(value, gold) == "wrong_value"


def test_stage_waterfall_ex_and_partition():
    gold = {
        "a": "SELECT x FROM t",
        "b": "SELECT x FROM t",
    }
    rows = [
        _row(question_id="a", correct=True),
        _row(
            question_id="b",
            correct=False,
            refused_by="guardrail",
            generated_sql="",
            outcome="refused",
        ),
    ]
    report = stage_waterfall(rows, gold)
    assert report["n"] == 2
    assert report["ex"] == pytest.approx(0.5)
    assert sum(report["stages"].values()) == 2
    assert report["stages"]["refused"] == 1


def test_question_arm_view_lists_all_arms():
    arms = {
        "baseline": [_row(arm="baseline", correct=False, routed_hit=False, pick_hit=False, routed_schemas=["world"])],
        "curated": [_row(arm="curated", correct=True)],
    }
    view = question_arm_view(arms, "q1", {"q1": "SELECT 1"})
    assert view["arms"]["baseline"]["funnel_stage"] == "pick"
    assert view["arms"]["curated"]["correct"] is True
    assert view["arms"]["baseline"]["generated_sql"]


@pytest.mark.skipif(not FIXED2.is_dir(), reason="fixed2 run artifacts not present")
@pytest.mark.skipif(not (BIRD / "eval_dataset" / "test_final.jsonl").is_file(), reason="BIRD data missing")
def test_fixed2_waterfall_matches_report():
    arms = load_arm_rows(FIXED2)
    gold = load_gold_sql(BIRD, split="test")
    report = bird_basis_report(arms, gold)

    # Exact matches for every arm except seeded table/wrong_shape (off-by-one).
    for arm, expected in REPORT_WATERFALL.items():
        got = report["waterfall"][arm]
        assert got["n"] == 1325
        assert got["ex"] == pytest.approx(expected["ex"], abs=0.0005)
        for stage in ("OK", "retrieval", "pick", "refused", "wrong_value"):
            assert got["stages"][stage] == expected[stage], (arm, stage)
        if arm != "seeded":
            assert got["stages"]["table"] == expected["table"]
            assert got["stages"]["wrong_shape"] == expected["wrong_shape"]
        else:
            # Tool: table=138, wrong_shape=156. Report: 139 / 155.
            # Same partition sum; one row differs. Tool side is the defined cascade;
            # report cell is wrong (or used a one-row-different table extract).
            assert got["stages"]["table"] == 138
            assert got["stages"]["wrong_shape"] == 156
            assert (
                got["stages"]["table"] + got["stages"]["wrong_shape"]
                == expected["table"] + expected["wrong_shape"]
            )


@pytest.mark.skipif(not FIXED2.is_dir(), reason="fixed2 run artifacts not present")
@pytest.mark.skipif(not (BIRD / "eval_dataset" / "test_final.jsonl").is_file(), reason="BIRD data missing")
def test_fixed2_schema_pick_and_stage4():
    arms = load_arm_rows(FIXED2)
    gold = load_gold_sql(BIRD, split="test")
    pick = schema_pick_report(arms["curated_sme"])
    assert pick["n_pick_wrong_gold_shortlisted"] == 96
    assert pick["gold_rank_histogram"] == {"1": 26, "2": 31, "3+": 39, "none": 0}
    # results.md claims 44 overrides; tool on BIRD-basis pick stage gets 41.
    # Tool side is the mechanical shortlist-index comparison; report is high.
    assert pick["rank_overrides"] == 41

    s4 = stage4_structural_report(arms["curated_sme"], gold)
    assert s4["n_stage4"] == 355
    assert s4["missing_distinct"] == 19  # matches report
    assert s4["like_vs_exact"] == 26  # matches report
    # Report: extra DISTINCT 75, over-join 113. Tool: 76 / 110.
    assert s4["extra_distinct"] == 76
    assert s4["over_join"] == 110

    sme = sme_perturbation_report(arms["curated"], arms["curated_sme"])
    assert sme["sql_changed"] == 678
    assert sme["helped"] == 59
    assert sme["hurt"] == 61

    decoy = bird_basis_report(arms, gold)["decoy_touch"]
    assert decoy["baseline"] == 143
    assert decoy["seeded"] == 62
    assert decoy["curated"] == 1
    assert decoy["curated_sme"] == 1
