"""Regression coverage for silent-failure audit findings F8, F9, F12, F13, F14."""

from __future__ import annotations

from types import SimpleNamespace

from governed_bi.eval.analysis import gradeable_report
from governed_bi.eval.error_taxonomy import _STAGE_CASCADE, ErrorClass, summarise_attributions
from governed_bi.eval.index import (
    MIN_QUOTABLE_QUESTIONS,
    arithmetic_floor_for_arms,
    holm_family_size,
    quotable,
    record_for_run,
)
from governed_bi.eval.leakage import is_gradeable_eval_row
from governed_bi.eval.run_datalake import (
    _routing_escaped,
    _schema_of_assets,
    _summarise_rows,
    ladder_deltas,
    price_verdict,
)

#: The three grading free-pass counters, at "measured, and zero". ``quotable()`` fails
#: closed when an arm omits them — an absent counter cannot be told from a measured
#: zero, and these guard a FLATTERING result — so a fixture standing in for a real run
#: spells them, exactly as it already spells ``crash_rate``.
_MEASURED_FREE_PASSES = {
    "n_correct_with_empty_gold": 0,
    "n_correct_and_pred_has_no_from": 0,
    "n_correct_and_zero_table_overlap": 0,
}

# --------------------------------------------------------------------------- #
# F8 — ledger hygiene vs claim readiness
# --------------------------------------------------------------------------- #


def test_quotable_true_is_ledger_ok_not_claim_ready(tmp_path):
    """``quotable`` / ``ledger_ok`` is hygiene; claim readiness is never auto-true."""
    import json

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({
            "mode": "datalake", "created_at_utc": "t", "model": "m",
            "split": "test", "prompt_set_hash": "abc",
        }),
        encoding="utf-8",
    )
    arms = {
        a: {"n": 72, "ex_lenient": 0.2, "ex_gradeable": 0.2, "crash_rate": 0.0, **_MEASURED_FREE_PASSES}
        for a in ("baseline", "seeded", "curated", "curated_sme")
    }
    (run_dir / "summary.json").write_text(
        json.dumps({
            "mode": "datalake", "split": "test", "n_questions": 72,
            "arms": arms, "arms_run": list(arms),
        }),
        encoding="utf-8",
    )
    record = record_for_run(run_dir)
    assert record["quotable"] is True
    assert record["ledger_ok"] is True
    assert record["hygiene_ok"] is True
    assert record["claim_ready"] is False
    # Not `== list(CLAIM_READY_REQUIRES)` — that asserted index.py's own assignment
    # against itself and would pass for any content, including an empty tuple. Check
    # what the field is FOR: a reader has to be told the substantive gates, so an
    # emptied or hygiene-only list must fail here.
    requires = record["claim_ready_requires"]
    assert len(requires) >= 5, "a claim-readiness checklist this short explains nothing"
    joined = " ".join(requires).lower()
    for gate in ("noise floor", "mde", "holm", "sign-test", "single-variable"):
        assert gate in joined, f"claim_ready_requires never mentions {gate}"
    assert any("hygiene only" in r for r in record["claim_ready_blocked_because"])
    assert record["arithmetic_floor_questions"] == 8
    assert record["holm_family_size"] == 6
    assert record["floor_sufficient_for_family"] is True


def test_four_vs_five_arm_arithmetic_floor():
    assert arithmetic_floor_for_arms(4) == MIN_QUOTABLE_QUESTIONS == 8
    assert holm_family_size(4) == 6
    assert arithmetic_floor_for_arms(5) == 9
    assert holm_family_size(5) == 10


def test_five_arm_family_rejects_default_eight_question_floor():
    """Eight questions clear the four-arm floor but not a five-arm Holm family."""
    arms = ["baseline", "seeded", "curated", "curated_sme", "oracle_sql"]
    base = {
        "n_questions": 8,
        "split": "test",
        "arms": arms,
        "headline": {
            a: {"n": 8, "ex_lenient": 0.1, "crash_rate": 0.0, **_MEASURED_FREE_PASSES} for a in arms
        },
    }
    ok, reasons = quotable(base)
    assert not ok
    assert any("5-arm" in r and "floor of 9" in r for r in reasons)

    ok8, _ = quotable({**base, "arms": arms[:4], "headline": {
        a: {"n": 8, "ex_lenient": 0.1, "crash_rate": 0.0, **_MEASURED_FREE_PASSES} for a in arms[:4]
    }})
    assert ok8


# --------------------------------------------------------------------------- #
# F9 — gradeable_report parity with summary
# --------------------------------------------------------------------------- #


def test_gradeable_report_matches_summary_exclusion_rule():
    rows = [
        {"question_id": "a", "correct": True, "gold_frozen": False, "gold_order_sensitive": False},
        {"question_id": "b", "correct": False, "gold_frozen": True, "gold_order_sensitive": False},
        {"question_id": "c", "correct": True, "gold_frozen": False, "gold_order_sensitive": True},
        {"question_id": "d", "correct": True, "gold_frozen": False, "gold_order_sensitive": False},
    ]
    summary = _summarise_rows("baseline", rows)
    offline = gradeable_report(rows)
    assert summary["n_gradeable"] == offline["n_gradeable"] == 2
    assert summary["n_frozen_gold"] == offline["n_frozen_gold"] == 1
    assert summary["n_order_sensitive_gold"] == offline["n_order_sensitive_gold"] == 1
    assert summary["ex_gradeable"] == offline["ex_gradeable"] == 1.0
    assert all(is_gradeable_eval_row(r) for r in rows if r["question_id"] in {"a", "d"})


def test_gradeable_report_empty_and_normal_cases():
    assert gradeable_report([])["ex_gradeable"] is None
    assert gradeable_report([])["n_gradeable"] == 0
    normal = [
        {"question_id": "1", "correct": True, "gold_frozen": False},
        {"question_id": "2", "correct": False, "gold_frozen": False},
    ]
    rep = gradeable_report(normal)
    assert rep["n_gradeable"] == 2
    assert rep["ex_gradeable"] == 0.5
    assert rep["n_order_sensitive_gold"] == 0


# --------------------------------------------------------------------------- #
# F12 — paired cost-per-added-correct
# --------------------------------------------------------------------------- #


def test_equal_n_mismatched_ids_are_not_priced():
    lo = {
        "arm": "seeded", "n": 10, "n_correct": 2,
        "question_ids": [f"a{i}" for i in range(10)],
        "ex_lenient": 0.2, "ex_gradeable": 0.2,
        "routing_recall": None, "cond_ex_given_routing": None,
        "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 10},
    }
    hi = {
        **lo, "arm": "curated", "n_correct": 5,
        "question_ids": [f"b{i}" for i in range(10)],
        "cost": {"total_cost_est_usd": 3.0, "n_rows_priced": 10},
    }
    d = ladder_deltas({"seeded": lo, "curated": hi})
    assert d["curated_minus_seeded_usd_per_added_correct"] is None
    assert "different question" in d["curated_minus_seeded_not_priced_because"]
    # Canonical gain is paired-only — must not wear the unpaired equal-N delta.
    assert d["curated_minus_seeded_correct_answers"] is None
    assert "different question" in d["curated_minus_seeded_correct_answers_unmeasured_because"]
    assert d["curated_minus_seeded_unpaired_n_correct_delta"] == 3

    tag, why = price_verdict(
        lo="seeded", hi="curated", n_lo=10, n_hi=10,
        lo_cost=1.0, hi_cost=3.0, lo_priced=10, hi_priced=10, added=3,
        ids_lo=set(lo["question_ids"]), ids_hi=set(hi["question_ids"]),
    )
    assert tag == "mismatched_ids" and why


def test_missing_question_ids_refuse_canonical_correct_answers():
    lo = {
        "arm": "seeded", "n": 10, "n_correct": 2,
        "ex_lenient": 0.2, "ex_gradeable": 0.2,
        "routing_recall": None, "cond_ex_given_routing": None,
        "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 10},
    }
    hi = {
        **lo, "arm": "curated", "n_correct": 5,
        "cost": {"total_cost_est_usd": 3.0, "n_rows_priced": 10},
    }
    d = ladder_deltas({"seeded": lo, "curated": hi})
    assert d["curated_minus_seeded_correct_answers"] is None
    assert "question-id sets were not recorded" in (
        d["curated_minus_seeded_correct_answers_unmeasured_because"]
    )
    assert d["curated_minus_seeded_unpaired_n_correct_delta"] == 3
    assert d["curated_minus_seeded_usd_per_added_correct"] is None


def test_unequal_n_refuses_canonical_correct_answers():
    d = ladder_deltas({
        "seeded": {
            "n": 100, "n_correct": 20, "question_ids": [f"q{i}" for i in range(100)],
            "ex_lenient": 0.2, "ex_gradeable": 0.2,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 100},
        },
        "curated": {
            "n": 50, "n_correct": 15, "question_ids": [f"q{i}" for i in range(50)],
            "ex_lenient": 0.3, "ex_gradeable": 0.3,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 2.0, "n_rows_priced": 50},
        },
    })
    assert d["curated_minus_seeded_correct_answers"] is None
    assert "unpaired" in d["curated_minus_seeded_correct_answers_unmeasured_because"]
    assert d["curated_minus_seeded_unpaired_n_correct_delta"] == -5
    assert d["curated_minus_seeded_usd_per_added_correct"] is None


def test_identical_pool_ladder_still_prices():
    ids = [f"q{i}" for i in range(100)]
    d = ladder_deltas({
        "seeded": {
            "arm": "seeded", "n": 100, "n_correct": 30, "question_ids": ids,
            "ex_lenient": 0.3, "ex_gradeable": 0.3,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 100},
        },
        "curated": {
            "arm": "curated", "n": 100, "n_correct": 40, "question_ids": ids,
            "ex_lenient": 0.4, "ex_gradeable": 0.4,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 3.0, "n_rows_priced": 100},
        },
    })
    assert d["curated_minus_seeded_correct_answers"] == 10
    assert "curated_minus_seeded_correct_answers_unmeasured_because" not in d
    assert "curated_minus_seeded_unpaired_n_correct_delta" not in d
    assert d["curated_minus_seeded_usd_per_added_correct"] == 0.2


def test_zero_and_negative_gain_semantics_preserved():
    ids = [f"q{i}" for i in range(50)]
    zero = ladder_deltas({
        "seeded": {
            "n": 50, "n_correct": 10, "question_ids": ids,
            "ex_lenient": 0.2, "ex_gradeable": 0.2,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 50},
        },
        "curated": {
            "n": 50, "n_correct": 10, "question_ids": ids,
            "ex_lenient": 0.2, "ex_gradeable": 0.2,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 4.0, "n_rows_priced": 50},
        },
    })
    assert zero["curated_minus_seeded_correct_answers"] == 0
    assert zero["curated_minus_seeded_usd_per_added_correct"] is None
    assert "bought no additional" in zero["curated_minus_seeded_not_priced_because"]

    neg = ladder_deltas({
        "seeded": {
            "n": 50, "n_correct": 20, "question_ids": ids,
            "ex_lenient": 0.4, "ex_gradeable": 0.4,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 50},
        },
        "curated": {
            "n": 50, "n_correct": 15, "question_ids": ids,
            "ex_lenient": 0.3, "ex_gradeable": 0.3,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 4.0, "n_rows_priced": 50},
        },
    })
    assert neg["curated_minus_seeded_correct_answers"] == -5
    assert neg["curated_minus_seeded_usd_per_added_correct"] is None
    assert neg["curated_minus_seeded_usd_per_lost_correct"] == 0.6


def test_paired_net_gain_from_shared_rows_matches_n_correct_on_identical_ids():
    """When rows are supplied, canonical gain is the paired discordant net."""
    lo_rows = [
        {"question_id": "a", "correct": True},
        {"question_id": "b", "correct": False},
        {"question_id": "c", "correct": True},
    ]
    hi_rows = [
        {"question_id": "a", "correct": True},
        {"question_id": "b", "correct": True},  # gained
        {"question_id": "c", "correct": False},  # lost
    ]
    summaries = {
        "seeded": {
            "n": 3, "n_correct": 2, "question_ids": ["a", "b", "c"],
            "ex_lenient": 2 / 3, "ex_gradeable": 2 / 3,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 3},
        },
        "curated": {
            "n": 3, "n_correct": 2, "question_ids": ["a", "b", "c"],
            "ex_lenient": 2 / 3, "ex_gradeable": 2 / 3,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 2.0, "n_rows_priced": 3},
        },
    }
    d = ladder_deltas(
        summaries, rows_by_arm={"seeded": lo_rows, "curated": hi_rows}
    )
    # Net gain 0 (one gain, one loss), not an unpaired illusion.
    assert d["curated_minus_seeded_correct_answers"] == 0
    assert d["curated_minus_seeded_usd_per_added_correct"] is None
    assert "bought no additional" in d["curated_minus_seeded_not_priced_because"]


# --------------------------------------------------------------------------- #
# F13 — by_error_stage rename + taxonomy inventory
# --------------------------------------------------------------------------- #


def test_offline_errors_use_by_error_stage_not_by_failed_stage():
    from governed_bi.eval.error_taxonomy import Attribution
    from governed_bi.stages import Outcome, Stage

    attrs = [
        Attribution(
            question_id="q1",
            outcome=Outcome.answered,
            correct=False,
            stage=Stage.schema_pick,
            primary=ErrorClass.wrong_schema,
            classes=(ErrorClass.wrong_schema,),
            n_classes=1,
        )
    ]
    summary = summarise_attributions(attrs)
    assert "by_error_stage" in summary
    assert "by_failed_stage" not in summary
    assert summary["by_error_stage"] == {"schema_pick": 1}


def test_error_class_inventory_includes_execution_error_in_cascade():
    classes = {c.value for c in ErrorClass}
    assert "execution_error" in classes
    cascade_classes = [c.value for c, _ in _STAGE_CASCADE]
    assert "execution_error" in cascade_classes
    # Pin the full inventory so a silent drop of a class fails closed in tests.
    assert classes == {
        "embedding_wall",
        "wrong_schema",
        "unparseable_sql",
        "gold_unusable",
        "execution_error",
        "wrong_table",
        "wrong_join_graph",
        "wrong_join_key",
        "wrong_join_type",
        "wrong_projection",
        "projection_order",
        "wrong_filter_column",
        "wrong_filter_literal",
        "wrong_aggregation",
        "wrong_group_by",
        "wrong_order_limit",
        "wrong_distinct",
        "wrong_set_op",
        "value_level",
        "unresolved_diff",
    }


def test_live_summary_keeps_by_failed_stage_for_outcome_attribution():
    rows = [
        {
            "question_id": "q1", "db_id": "d", "arm": "baseline", "split": "test",
            "outcome": "refused", "failed_stage": "refuse_gate",
            "refused_by": "refuse_gate", "correct": False,
        }
    ]
    s = _summarise_rows("baseline", rows)
    assert "by_failed_stage" in s
    assert s.get("errors") is None


# --------------------------------------------------------------------------- #
# F14 — unresolved tables_used
# --------------------------------------------------------------------------- #


def test_schema_of_assets_reports_unresolved_ids():
    class Corpus:
        def by_id(self, aid):
            if aid == "tbl_ok":
                return SimpleNamespace(asset_type="table", schema="beer")
            return None

    resolved, unresolved = _schema_of_assets(Corpus(), ["tbl_ok", "tbl_missing"])
    assert resolved == {"beer"}
    assert unresolved == ["tbl_missing"]


def test_routing_escape_fully_resolved_partial_and_unobserved():
    # Fully resolved, inside routed set.
    assert _routing_escaped({"beer"}, ["beer"], bypassed=False, unresolved_ids=[]) is False
    # Fully resolved escape.
    assert _routing_escaped({"other"}, ["beer"], bypassed=False, unresolved_ids=[]) is True
    # Partial unresolved but resolved schemas already escape → definitive True.
    assert _routing_escaped(
        {"other"}, ["beer"], bypassed=False, unresolved_ids=["tbl_x"]
    ) is True
    # Partial unresolved, resolved stay inside → unknown (None), not False.
    assert _routing_escaped(
        {"beer"}, ["beer"], bypassed=False, unresolved_ids=["tbl_x"]
    ) is None
    # Fully unresolved non-empty tables_used → unknown, not unobserved-as-compliant.
    assert _routing_escaped(
        set(), ["beer"], bypassed=False, unresolved_ids=["tbl_x"]
    ) is None
    # Genuinely unobserved (no tables_used / nothing unresolved).
    assert _routing_escaped(set(), ["beer"], bypassed=False, unresolved_ids=[]) is None
    assert _routing_escaped(None, ["beer"], bypassed=False, unresolved_ids=None) is None


def test_summary_counts_routing_escape_unknown_separately_from_rate():
    rows = [
        {
            "question_id": "q1", "db_id": "beer", "arm": "curated", "split": "test",
            "routed_schemas": ["beer"], "routed_hit": True,
            "routing_escaped": False, "routing_escape_unknown": False,
            "n_tables_used_unresolved": 0, "correct": True, "generated_sql": "SELECT 1",
        },
        {
            "question_id": "q2", "db_id": "beer", "arm": "curated", "split": "test",
            "routed_schemas": ["beer"], "routed_hit": True,
            "routing_escaped": None, "routing_escape_unknown": True,
            "n_tables_used_unresolved": 2, "correct": False, "generated_sql": "SELECT 1",
        },
        {
            "question_id": "q3", "db_id": "beer", "arm": "curated", "split": "test",
            "routed_schemas": ["beer"], "routed_hit": True,
            "routing_escaped": True, "routing_escape_unknown": False,
            "n_tables_used_unresolved": 0, "correct": False, "generated_sql": "SELECT 1",
        },
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_routing_escape_observed"] == 2  # definitive True/False only
    assert s["n_routing_escaped"] == 1
    assert abs(s["routing_escape_rate"] - 0.5) < 1e-9
    assert s["n_routing_escape_unknown"] == 1
    assert s["n_tables_used_unresolved"] == 2
