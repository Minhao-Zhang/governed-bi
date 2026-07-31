"""Offline eval analysis + the resumable pooled driver's row plumbing.

These pin the measurement machinery rather than the model: a wrong table-overlap
statistic or a mis-scored resume is indistinguishable from a real regression in a
run that takes hours, so the arithmetic is tested directly and offline.
"""

from __future__ import annotations

import json

import pytest

from governed_bi.eval.analysis import (
    analyse_run,
    census_delta,
    corpus_census,
    gradeable_report,
    mcnemar,
    rank_report,
    sql_tables,
    table_selection_report,
)
from governed_bi.eval.arms import ARM_ORDER
from governed_bi.eval.run_datalake import (
    _check_resume_manifest,
    _merge_resume_manifest,
    _read_rows,
    _summarise_rows,
)

# --------------------------------------------------------------------------- #
# sql_tables
# --------------------------------------------------------------------------- #


def test_sql_tables_ignores_aliases_and_columns():
    """A regex over quoted identifiers also captures ``T1`` and column names; the
    AST walk must return only real tables (the mis-measurement this replaces)."""
    sql = (
        'SELECT "T1"."party" FROM "address"."congress" AS "T1" '
        'INNER JOIN "address"."state" AS "T2" ON "T1"."abbr" = "T2"."abbr"'
    )
    assert sql_tables(sql) == frozenset({"congress", "state"})


def test_sql_tables_excludes_cte_names():
    """``WITH recent AS (...) ... FROM recent`` references no table ``recent``."""
    sql = (
        "WITH recent AS (SELECT * FROM orders) "
        "SELECT * FROM recent JOIN customers c ON c.id = recent.cid"
    )
    assert sql_tables(sql) == frozenset({"orders", "customers"})


@pytest.mark.parametrize("bad", [None, "", "   ", "NOT SQL AT ALL (("])
def test_sql_tables_tolerates_unusable_sql(bad):
    assert sql_tables(bad) == frozenset()


# --------------------------------------------------------------------------- #
# McNemar
# --------------------------------------------------------------------------- #


def _pair(a_only: int, b_only: int, both: int = 10):
    a, b = [], []
    for i in range(a_only):
        a.append({"question_id": f"a{i}", "correct": True})
        b.append({"question_id": f"a{i}", "correct": False})
    for i in range(b_only):
        a.append({"question_id": f"b{i}", "correct": False})
        b.append({"question_id": f"b{i}", "correct": True})
    for i in range(both):
        a.append({"question_id": f"c{i}", "correct": True})
        b.append({"question_id": f"c{i}", "correct": True})
    return a, b


def test_mcnemar_symmetric_discordance_is_not_significant():
    """Near-symmetric discordance is noise however many questions disagree: the
    two arms won and lost an equal share, so neither is shown to be better."""
    res = mcnemar(*_pair(40, 41))
    assert res.n_discordant == 81
    assert res.net == 1
    assert res.p_value == pytest.approx(1.0)


def test_mcnemar_lopsided_discordance_is_significant():
    res = mcnemar(*_pair(10, 90))
    assert res.net == 80
    assert res.p_value < 1e-10


def test_mcnemar_ignores_concordant_pairs():
    """Only discordant pairs carry signal: padding agreements cannot move p."""
    few = mcnemar(*_pair(2, 8, both=0))
    many = mcnemar(*_pair(2, 8, both=5000))
    assert few.p_value == pytest.approx(many.p_value)


def test_mcnemar_uses_only_shared_questions():
    a = [{"question_id": "q1", "correct": True}, {"question_id": "only_a", "correct": True}]
    b = [{"question_id": "q1", "correct": False}, {"question_id": "only_b", "correct": True}]
    res = mcnemar(a, b)
    assert res.n_paired == 1
    assert (res.a_only, res.b_only) == (1, 0)


# --------------------------------------------------------------------------- #
# Table selection: retrieval miss vs selection miss
# --------------------------------------------------------------------------- #


def _row(qid, *, correct, sql, routed=True, retrieved=None, db_id="food_inspection"):
    return {
        "question_id": qid,
        "db_id": db_id,
        "arm": "curated",
        "correct": correct,
        "routed_hit": routed,
        "generated_sql": sql,
        "retrieved_tables": retrieved,
    }


def test_table_report_ignores_offers_from_another_schema():
    """With llm_pick off the routed set spans schemas, so a same-named table from
    the wrong schema must not count as having offered the gold one — that would
    silently convert a retrieval miss into a selection miss."""
    rows = [
        _row("q1", correct=False, sql="SELECT * FROM allgemeine_informationen",
             db_id="food_inspection",
             retrieved=["food_inspection.allgemeine_informationen",
                        "movie_platform.standort"])
    ]
    rep = table_selection_report(rows, {"q1": "SELECT * FROM standort"})
    assert (rep.n_retrieval_miss, rep.n_selection_miss) == (1, 0)


def test_table_report_splits_retrieval_miss_from_selection_miss():
    """The distinction that decides what to fix: was the gold table never offered
    (retrieval) or offered and unused (generation)?"""
    gold = {"q1": "SELECT * FROM standort", "q2": "SELECT * FROM standort"}
    rows = [
        # gold table was never retrieved -> retrieval failure
        _row("q1", correct=False, sql="SELECT * FROM allgemeine_informationen",
             retrieved=["allgemeine_informationen"]),
        # gold table WAS retrieved but the model used another -> selection failure
        _row("q2", correct=False, sql="SELECT * FROM allgemeine_informationen",
             retrieved=["allgemeine_informationen", "standort"]),
    ]
    rep = table_selection_report(rows, gold, arm="curated")
    assert rep.n_right_schema_wrong_sql == 2
    assert rep.n_table_mismatch == 2
    assert (rep.n_retrieval_miss, rep.n_selection_miss) == (1, 1)
    assert rep.retrieval_miss_rate == pytest.approx(0.5)
    assert rep.top_missed_tables == [("standort", 2)]


def test_table_report_excludes_misrouted_rows():
    """A mis-routed question cannot have used the gold tables; counting it would
    just re-measure routing inside a generation metric."""
    gold = {"q1": "SELECT * FROM standort"}
    rows = [_row("q1", correct=False, sql="SELECT * FROM other", routed=False)]
    rep = table_selection_report(rows, gold)
    assert rep.n_right_schema_wrong_sql == 0
    assert rep.n_table_mismatch == 0


def test_table_report_conversion_rate_given_right_tables():
    """p(correct | right tables) is the multiplier for sizing a table-selection
    fix — cond_EX would overstate it."""
    gold = {f"q{i}": "SELECT * FROM t_a" for i in range(4)}
    rows = [
        _row("q0", correct=True, sql="SELECT * FROM t_a"),
        _row("q1", correct=True, sql="SELECT * FROM t_a"),
        _row("q2", correct=False, sql="SELECT * FROM t_a"),  # right tables, wrong SQL
        _row("q3", correct=False, sql="SELECT * FROM t_b"),  # wrong table
    ]
    rep = table_selection_report(rows, gold)
    assert rep.p_correct_given_right_tables == pytest.approx(2 / 3)


def test_table_report_separates_the_reasons_a_row_cannot_be_compared():
    """Missing gold, frozen gold and unparseable gold are different problems. If
    they collapse into one bucket, pointing the report at the wrong split reads as
    a clean zero-mismatch result instead of a broken input."""
    rows = [
        _row("missing", correct=False, sql="SELECT * FROM t_a"),
        _row("frozen", correct=False, sql="SELECT * FROM t_a"),
        _row("bad", correct=False, sql="SELECT * FROM t_a"),
        _row("ok", correct=False, sql="SELECT * FROM t_b"),
    ]
    gold = {
        "frozen": "SELECT * FROM (VALUES ('a', 1)) AS v(c0, c1)",
        "bad": "(((",
        "ok": "SELECT * FROM t_a",
    }
    rep = table_selection_report(rows, gold)
    assert (rep.n_gold_missing, rep.n_gold_frozen, rep.n_gold_unparseable) == (1, 1, 1)
    assert rep.n_compared == 1
    assert rep.n_table_mismatch == 1
    assert rep.table_mismatch_rate == pytest.approx(1.0)  # over comparable rows only


def test_table_report_does_not_call_an_over_join_a_missing_table():
    """Using every gold table plus an extra is an over-join, not a table the model
    failed to find; scoring it as a selection miss inflates the fix-generation
    bucket the report exists to size."""
    rows = [
        _row("q1", correct=False, sql="SELECT * FROM t_a JOIN t_b ON 1=1",
             retrieved=["t_a", "t_b"])
    ]
    rep = table_selection_report(rows, {"q1": "SELECT * FROM t_a"})
    assert rep.n_table_mismatch == 1
    assert rep.n_extra_tables_only == 1
    assert (rep.n_retrieval_miss, rep.n_selection_miss) == (0, 0)
    assert rep.top_missed_tables == []


def test_table_report_does_not_call_a_refusal_a_table_mismatch():
    """A refused or crashed row produced no SQL, so it selected no tables. Scored as
    a mismatch it is also charged to retrieval or selection, manufacturing a
    table-selection failure — and a missed-table tally — out of a refusal."""
    gold = {q: "SELECT * FROM standort" for q in ("q1", "q2", "q3")}
    rows = [
        _row("q1", correct=False, sql=None, retrieved=["food_inspection.standort"]),
        _row("q2", correct=False, sql="   ", retrieved=["food_inspection.standort"]),
        _row("q3", correct=False, sql="SELECT * FROM other",
             retrieved=["food_inspection.standort"]),
    ]
    rep = table_selection_report(rows, gold)
    assert rep.n_right_schema_wrong_sql == 3
    assert rep.n_no_sql == 2
    assert rep.n_compared == 1
    assert rep.n_table_mismatch == 1
    assert (rep.n_retrieval_miss, rep.n_selection_miss) == (0, 1)
    assert rep.top_missed_tables == [("standort", 1)]  # once, not three times


def test_table_report_conversion_rate_ignores_rows_with_no_sql():
    """p(correct | right tables) must not read a refusal as a failed conversion; an
    empty table set cannot cover a non-empty gold, so it drops out by construction."""
    gold = {"q1": "SELECT * FROM t_a", "q2": "SELECT * FROM t_a"}
    rows = [
        _row("q1", correct=True, sql="SELECT * FROM t_a"),
        _row("q2", correct=False, sql=None),
    ]
    rep = table_selection_report(rows, gold)
    assert rep.p_correct_given_right_tables == pytest.approx(1.0)


def test_table_report_rates_are_none_when_nothing_was_comparable():
    """An arm that refused every question has no table statistic to report. 0.0 would
    read as "the tables were fine", and 0.0 recall as "it found none of them"."""
    rows = [_row("q1", correct=False, sql=None)]
    rep = table_selection_report(rows, {"q1": "SELECT * FROM standort"})
    assert (rep.n_no_sql, rep.n_compared) == (1, 0)
    assert rep.table_mismatch_rate is None
    assert rep.mean_table_recall is None
    assert rep.mean_table_precision is None


def test_table_report_matches_schema_qualified_provenance():
    """Provenance records ``schema.table``; SQL yields bare names. The comparison
    must not read every offered table as missing."""
    rows = [
        _row("q1", correct=False, sql="SELECT * FROM allgemeine_informationen",
             retrieved=["food_inspection.allgemeine_informationen",
                        "food_inspection.standort"])
    ]
    rep = table_selection_report(rows, {"q1": "SELECT * FROM standort"})
    assert (rep.n_retrieval_miss, rep.n_selection_miss) == (0, 1)


# --------------------------------------------------------------------------- #
# Gradeable EX / rank buckets
# --------------------------------------------------------------------------- #


def test_gradeable_report_drops_frozen_values_gold():
    rows = [
        {"question_id": "q1", "correct": False, "gold_frozen": True, "generated_sql": "x"},
        {"question_id": "q2", "correct": True, "gold_frozen": False, "generated_sql": "y"},
    ]
    rep = gradeable_report(rows)
    assert rep["ex_lenient"] == pytest.approx(0.5)
    assert rep["ex_gradeable"] == pytest.approx(1.0)
    assert rep["n_frozen_gold"] == 1


def test_gradeable_report_falls_back_to_gold_sql_for_old_rows():
    """Runs predating the ``gold_frozen`` flag stay analysable."""
    rows = [
        {"question_id": "q1", "correct": False, "generated_sql": "x"},
        {"question_id": "q2", "correct": True, "generated_sql": "y"},
    ]
    gold = {"q1": "SELECT * FROM (VALUES ('a', 1)) AS v(c0, c1)", "q2": "SELECT 1 FROM t"}
    assert gradeable_report(rows, gold)["n_frozen_gold"] == 1


def test_rank_report_separates_shortlist_miss_from_picker_error():
    rows = [
        {"gold_schema_rank": 1, "shortlisted_schemas": ["a"], "correct": False, "pick_hit": False},
        {"gold_schema_rank": 1, "shortlisted_schemas": ["a"], "correct": True, "pick_hit": True},
        # Retrieval RAN and did not surface the schema: a shortlist exists, the gold
        # is not in it.
        {"gold_schema_rank": None, "shortlisted_schemas": ["b"], "correct": False, "pick_hit": False},
    ]
    rep = rank_report(rows)
    assert rep["1"]["n"] == 2
    assert rep["1"]["pick_accuracy"] == pytest.approx(0.5)
    assert rep["miss"]["ex_lenient"] == 0.0
    assert list(rep) == ["1", "miss"]  # numeric ranks first, miss last


def test_a_row_with_no_shortlist_is_not_counted_as_a_retrieval_miss():
    """An oracle rung records no shortlist, and that is not the embedder's failure.

    Both cases give ``gold_schema_rank=None``, so the buckets were merged and the
    whole-split ``oracle_sql`` ceiling published ``{"miss": {"n": 2030,
    "ex_lenient": 1.0}}`` — a bucket documented as "retrieval never surfaced the
    schema — widen the shortlist or fix the embedder", sitting at a perfect score,
    off a run where retrieval was never invoked. ``docs/prompt-experiments.md``
    routes a spending decision off that bucket.
    """
    rows = [
        {"gold_schema_rank": None, "shortlisted_schemas": ["b"], "correct": False},
        {"gold_schema_rank": None, "shortlisted_schemas": None, "correct": True},
        {"gold_schema_rank": None, "correct": True},  # field absent entirely
    ]
    rep = rank_report(rows)
    assert rep["miss"]["n"] == 1
    assert rep["miss"]["ex_lenient"] == 0.0
    assert rep["no_shortlist"]["n"] == 2
    assert rep["no_shortlist"]["ex_lenient"] == 1.0
    assert list(rep) == ["miss", "no_shortlist"]


# --------------------------------------------------------------------------- #
# Driver: row aggregation, resume replay, manifest guard
# --------------------------------------------------------------------------- #


def _scored(qid, **over):
    row = {
        "question_id": qid,
        "db_id": "db_a",
        "split": "test",
        "generated_sql": "SELECT 1",
        "correct": True,
        "correct_strict": True,
        "gold_frozen": False,
        "routed_hit": True,
        "schema_pick": "db_a",
        "pick_hit": True,
        "decoy_touch": False,
        "difficulty": "simple",
        "gold_schema_rank": 1,
        "error": None,
    }
    row.update(over)
    return row


def test_summarise_rows_separates_routing_from_generation():
    """EX = routing_recall x cond_ex_given_routing — the decomposition that says
    whether a change helped routing or generation."""
    rows = [
        _scored("q1", correct=True),
        _scored("q2", correct=False),
        _scored("q3", correct=False, routed_hit=False, pick_hit=False, schema_pick="other"),
        _scored("q4", correct=False, routed_hit=False, pick_hit=False, schema_pick="other"),
    ]
    s = _summarise_rows("curated", rows)
    assert s["ex_lenient"] == pytest.approx(0.25)
    assert s["routing_recall"] == pytest.approx(0.5)
    assert s["cond_ex_given_routing"] == pytest.approx(0.5)
    assert s["ex_lenient"] == pytest.approx(
        s["routing_recall"] * s["cond_ex_given_routing"]
    )


def test_a_turn_that_recorded_no_routing_decision_is_not_a_routing_miss():
    """Absent is not zero — the invariant this module keeps relearning.

    The row builder read ``meta.get("routed_schemas") or []``, so a turn that ended
    before ``assemble`` ran recorded ``[]``, which gave ``routed_hit=False``, which is
    indistinguishable from a router that ran and picked the wrong schema. The
    whole-split ``--skip-agent`` ceiling published ``routing_recall: 0.0`` with
    ``n_routing_observed: 2030`` and ``n_routing_bypassed: 0`` — a confident zero for a
    router that was never invoked, in the ledger headline.

    The crash and bypass carve-outs do not cover this: the turn neither crashed nor
    was pinned. So the denominator is defined on POSITIVE evidence — a recorded
    decision — and the excluded rows are counted where a reader can see them.
    """
    rows = [
        _scored("q1", correct=True),
        _scored("q2", correct=False, routed_hit=None, routed_schemas=None),
        _scored("q3", correct=False, routed_hit=None, routed_schemas=None),
    ]
    s = _summarise_rows("curated", rows)
    assert s["n"] == 3
    assert s["n_routing_unrecorded"] == 2
    assert s["n_routing_observed"] == 1
    assert s["routing_recall"] == pytest.approx(1.0)

    # And when NOTHING recorded a decision, the metric is absent rather than 0.0.
    none_at_all = _summarise_rows(
        "oracle_sql",
        [_scored(f"q{i}", routed_hit=None, routed_schemas=None) for i in range(4)],
    )
    assert none_at_all["routing_recall"] is None
    assert none_at_all["n_routing_observed"] == 0
    assert none_at_all["n_routing_unrecorded"] == 4


def test_summarise_rows_gradeable_denominator_and_decoy():
    rows = [
        _scored("q1", correct=True, gold_frozen=False),
        _scored("q2", correct=False, gold_frozen=True),
        _scored("q3", correct=False, gold_frozen=False, decoy_touch=True),
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_frozen_gold"] == 1
    assert s["ex_lenient"] == pytest.approx(1 / 3)
    assert s["ex_gradeable"] == pytest.approx(0.5)
    assert s["n_decoy_touch"] == 1
    assert s["decoy_touch_rate"] == pytest.approx(1 / 3)


def test_summarise_rows_counts_refusals_from_missing_sql():
    rows = [_scored("q1"), _scored("q2", generated_sql=None, correct=False)]
    s = _summarise_rows("curated", rows)
    assert s["refusal_rate"] == pytest.approx(0.5)
    assert s["conditional_ex_lenient"] == pytest.approx(1.0)


def test_summarise_rows_separates_genuine_picks_from_fallbacks():
    """A proxy outage substitutes rank-1 for a pick the model never made. Both
    numbers are reported so the real pick rate needs no hand subtraction."""
    rows = [
        _scored("q1", pick_hit=True, schema_pick_fallback="call_failed"),
        _scored("q2", pick_hit=True),
        _scored("q3", pick_hit=False, schema_pick="other"),
    ]
    s = _summarise_rows("curated", rows)
    assert s["schema_pick_accuracy"] == pytest.approx(2 / 3)
    assert s["schema_pick_accuracy_excl_fallback"] == pytest.approx(0.5)
    assert s["n_pick_fallback"] == 1


def test_summarise_rows_pick_accuracy_agrees_with_rank_buckets():
    """summary.json must not ship two contradictory pick-accuracy numbers."""
    rows = [_scored(f"q{i}", schema_pick=None, pick_hit=None) for i in range(3)]
    s = _summarise_rows("curated", rows)
    assert s["schema_pick_accuracy"] is None
    assert s["by_gold_rank"]["1"]["pick_accuracy"] is None


def test_summarise_rows_reports_an_empty_arm_as_unmeasured_not_as_zero():
    """Every rate is None at n == 0, not 0.0.

    Changed from asserting 0.0: an arm that scored no rows measured nothing, and
    0.0 says it measured everything and got none of it right. The ledger's
    quotability rule keys on ``crash_rate is None`` to mean "this run never
    recorded whether it crashed", so a confident 0.0 here made a run that produced
    no rows at all pass as quotable.
    """
    s = _summarise_rows("curated", [])
    assert s["n"] == 0
    assert s["ex_lenient"] is None
    assert s["ex_strict"] is None
    assert s["ex_gradeable"] is None
    assert s["refusal_rate"] is None
    assert s["crash_rate"] is None
    assert s["cond_ex_given_routing"] is None
    assert s["routing_recall"] is None
    assert s["decoy_touch_rate"] is None
    assert s["conditional_ex_lenient"] is None
    assert s["schema_pick_accuracy"] is None


def test_read_rows_drops_truncated_tail(tmp_path):
    """A killed run leaves a half-written final line; resume must survive it."""
    path = tmp_path / "generations.curated.jsonl"
    path.write_text(
        json.dumps(_scored("q1")) + "\n" + '{"question_id": "q2", "corr',
        encoding="utf-8",
    )
    rows = _read_rows(path)
    assert [r["question_id"] for r in rows] == ["q1"]


def test_read_rows_missing_file_is_empty(tmp_path):
    assert _read_rows(tmp_path / "nope.jsonl") == []


def test_check_resume_manifest_rejects_split_change(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"split": "train"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing to resume"):
        _check_resume_manifest(tmp_path, {"split": "test"})


def test_check_resume_manifest_warns_but_allows_knob_drift(tmp_path, capsys):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": "test", "route_top_k": 8}), encoding="utf-8"
    )
    _check_resume_manifest(tmp_path, {"split": "test", "route_top_k": 10})
    assert "changed knobs" in capsys.readouterr().out


def test_resuming_after_a_model_change_warns(tmp_path, capsys):
    """Model is a resume-drift knob; changing it mid-directory mixes configurations.

    The retired ``--skip-agent`` transition used to under-report this (``model: None``
    was exempted by ``prior.get(k) is not None``). Model is now compared when present
    on both sides like any other resume knob.
    """
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": "test", "model": None}),
        encoding="utf-8",
    )
    # None → name: prior None is skipped by the drift loop; this documents that
    # behaviour. Use two non-None models to assert the warning path.
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": "test", "model": "gpt-old"}),
        encoding="utf-8",
    )
    _check_resume_manifest(
        tmp_path, {"split": "test", "model": "gpt-5.6-luna"}
    )
    assert "changed knobs" in capsys.readouterr().out


def test_check_resume_manifest_silent_when_absent(tmp_path):
    _check_resume_manifest(tmp_path, {"split": "test"})  # no manifest: not an error


def test_manifest_merge_keeps_every_attempt():
    """Overwriting makes drift detection one-shot: after 8 -> 10 -> 12 only the
    last hop would be visible, and nothing would record rows scored at 8."""
    first = {"split": "test", "route_top_k": 8, "created_at_utc": "T1"}
    second = _merge_resume_manifest(first, {"split": "test", "route_top_k": 10,
                                            "created_at_utc": "T2"})
    third = _merge_resume_manifest(second, {"split": "test", "route_top_k": 12,
                                            "created_at_utc": "T3"})
    # Original knobs and start time survive; each attempt is preserved in order.
    assert third["route_top_k"] == 8
    assert third["created_at_utc"] == "T1"
    assert [r["route_top_k"] for r in third["resumes"]] == [10, 12]


def test_manifest_merge_on_a_fresh_run_is_the_manifest_itself():
    current = {"split": "test", "route_top_k": 10}
    assert _merge_resume_manifest({}, current) == current


# --------------------------------------------------------------------------- #
# Input-side metrics: the corpus census and prompt delivery
# --------------------------------------------------------------------------- #


def _corpus(*extra):
    from governed_bi.corpus import Corpus
    from governed_bi.corpus.schemas import Column, LogicalType, TableAsset

    col = lambda n: Column(  # noqa: E731
        physical_name=n, physical_type="TEXT", logical_type=LogicalType.string,
        nullable=True, is_unique=False,
    )
    table = TableAsset(
        id="tbl_a_t", schema="a", physical_name="t",
        description="A described table.", columns=[col("x"), col("y")],
    )
    return Corpus(assets=[table, *extra])


def _note(nid, scope):
    from governed_bi.corpus.schemas import NoteAsset, NoteKind

    return NoteAsset(id=nid, kind=NoteKind.context, scope=scope, summary="caveat")


def test_corpus_census_counts_the_independent_variable():
    census = corpus_census(_corpus(_note("n1", ["schema:a"]), _note("n2", [])))
    assert census["n_tables"] == 1
    assert census["n_tables_described"] == 1
    assert census["n_columns"] == 2
    assert census["n_notes"] == 2
    # Scope kind is broken out because a global note behaves nothing like a
    # schema-scoped one at injection time.
    assert census["notes_by_scope"] == {"global": 1, "schema": 1}
    assert census["notes_by_activation"] == {"always": 2}


def test_census_delta_reports_what_an_arm_added():
    lower = corpus_census(_corpus())
    higher = corpus_census(_corpus(_note("n1", ["schema:a"])))
    assert census_delta(lower, higher) == {"n_notes": 1}


def test_census_delta_is_empty_when_an_arm_added_nothing():
    """The signal that an arm-to-arm EX difference measures nothing: an empty
    delta means the higher rung is the lower rung, so any gap is noise."""
    census = corpus_census(_corpus())
    assert census_delta(census, census) == {}


def test_summarise_rows_reports_prompt_delivery():
    """Whether curated content reached the model at all — the measurement that
    separates 'curation does not help' from 'curation never arrived'."""
    rows = [
        _scored("q1", n_notes_injected=2, n_few_shots_injected=3, context_chars=100),
        _scored("q2", n_notes_injected=0, n_few_shots_injected=1, context_chars=50),
    ]
    s = _summarise_rows("curated", rows)
    assert s["mean_notes_injected"] == pytest.approx(1.0)
    assert s["share_with_a_note"] == pytest.approx(0.5)
    assert s["mean_few_shots_injected"] == pytest.approx(2.0)
    assert s["mean_context_chars"] == pytest.approx(75.0)


def test_delivery_means_are_none_when_a_run_never_recorded_them():
    """An older run that lacks the field must read as "not measured", not zero —
    zero would look like a real observation of nothing being injected."""
    s = _summarise_rows("curated", [_scored("q1")])
    assert s["mean_notes_injected"] is None
    # Changed from 0.0: a run that never recorded note injection has no share to
    # report. 0.0 said "the notes never reached the prompt" — a real observation of
    # a delivery failure — right beside a null mean, which says nothing was measured.
    assert s["share_with_a_note"] is None


def test_summarise_rows_counts_wrong_answers_that_matched_row_count():
    """Right row count + wrong hash is the projection/ordering class; a different
    count is a different answer. Sizing them apart decides whether to fix the
    generator or the grading contract."""
    rows = [
        _scored("q1", correct=False, nrows_match=True),
        _scored("q2", correct=False, nrows_match=False),
        _scored("q3", correct=True, nrows_match=True),
    ]
    assert _summarise_rows("curated", rows)["n_wrong_but_nrows_match"] == 1


def test_cost_block_is_separate_from_the_scored_fields():
    rows = [_scored("q1", latency_sec=1.5, usage={"total_tokens": 100})]
    s = _summarise_rows("curated", rows)
    assert s["cost"]["total_latency_sec"] == pytest.approx(1.5)
    assert s["cost"]["total_tokens"] == 100
    assert "total_latency_sec" not in s  # nested, so invariance stays testable


# --------------------------------------------------------------------------- #
# analyse_run: the whole offline report over a run directory
# --------------------------------------------------------------------------- #


def _write_run(tmp_path, bird_dir):
    """A two-arm run directory plus the gold split file it is analysed against."""
    (bird_dir / "eval_dataset").mkdir(parents=True)
    gold = [
        {"question_id": "q1", "sql_rename": "SELECT * FROM t_a"},
        {"question_id": "q2", "sql_rename": "SELECT * FROM t_b"},
    ]
    (bird_dir / "eval_dataset" / "test_final.jsonl").write_text(
        "".join(json.dumps(g) + "\n" for g in gold), encoding="utf-8"
    )
    for arm, correct in (("baseline", [False, False]), ("curated", [True, False])):
        rows = [
            {
                "question_id": g["question_id"], "db_id": "db_a", "arm": arm,
                "split": "test", "correct": c, "routed_hit": True,
                "generated_sql": g["sql_rename"] if c else "SELECT * FROM t_z",
                "gold_frozen": False, "gold_schema_rank": 1, "pick_hit": True,
                "retrieved_tables": ["db_a.t_a", "db_a.t_b"],
            }
            for g, c in zip(gold, correct)
        ]
        (tmp_path / f"generations.{arm}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )


def test_analyse_run_reports_every_arm_and_pairing(tmp_path):
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    report = analyse_run(tmp_path, bird_dir=bird)

    assert report["split"] == "test"
    assert set(report["arms"]) == {"baseline", "curated"}
    assert report["arms"]["curated"]["gradeable"]["ex_lenient"] == pytest.approx(0.5)
    # Both gold tables were offered, so every miss is the model's selection.
    assert report["arms"]["baseline"]["tables"]["n_selection_miss"] == 2
    assert report["question_coverage"]["n_common_to_all_arms"] == 2
    assert report["question_coverage"]["incomplete_arms"] == []
    assert report["mcnemar"]["baseline_vs_curated"]["b_only"] == 1


def test_analyse_run_flags_an_arm_missing_questions(tmp_path):
    """The flagged arm is the truncated one. This test previously asserted
    ``["baseline"]`` — the untouched, complete arm — because the implementation
    compared each arm against the intersection, which the short arm itself shrinks."""
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    path = tmp_path / "generations.curated.jsonl"
    first = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(first + "\n", encoding="utf-8")  # truncated / still-running arm

    report = analyse_run(tmp_path, bird_dir=bird)
    assert report["question_coverage"]["n_common_to_all_arms"] == 1
    assert report["question_coverage"]["n_scored_by_any_arm"] == 2
    assert report["question_coverage"]["per_arm"] == {"baseline": 2, "curated": 1}
    assert report["question_coverage"]["incomplete_arms"] == ["curated"]


def test_analyse_run_flags_both_arms_when_neither_is_complete(tmp_path):
    """Two arms of equal length covering different questions are both incomplete;
    a "fewer rows than the fullest arm" rule would flag neither, and the paired
    test would still run on a silent subset."""
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    for arm, keep in (("baseline", 0), ("curated", 1)):
        path = tmp_path / f"generations.{arm}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text(lines[keep] + "\n", encoding="utf-8")

    report = analyse_run(tmp_path, bird_dir=bird)
    assert report["question_coverage"]["n_common_to_all_arms"] == 0
    assert report["question_coverage"]["incomplete_arms"] == ["baseline", "curated"]


def _rewrite_rows(path, fn):
    """Apply ``fn`` to every row of a generations file, in place."""
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    path.write_text(
        "".join(json.dumps(fn(r)) + "\n" for r in rows), encoding="utf-8"
    )


def _drop_split(row):
    row.pop("split", None)
    return row


def test_analyse_run_refuses_to_guess_the_split_of_legacy_rows(tmp_path):
    """Defaulting to "test" picks a gold file. If it is the wrong one every row lands
    in n_gold_missing and the report reads as "nothing to compare" — a clean bill of
    health for a run that was never actually compared to anything."""
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    for arm in ("baseline", "curated"):
        _rewrite_rows(tmp_path / f"generations.{arm}.jsonl", _drop_split)

    with pytest.raises(RuntimeError, match="records no split"):
        analyse_run(tmp_path, bird_dir=bird)


def test_analyse_run_accepts_an_explicit_split_for_legacy_rows(tmp_path):
    """The escape hatch: the caller asserts the split the rows do not record."""
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    for arm in ("baseline", "curated"):
        _rewrite_rows(tmp_path / f"generations.{arm}.jsonl", _drop_split)

    report = analyse_run(tmp_path, bird_dir=bird, split="test")
    assert report["split"] == "test"
    assert report["arms"]["curated"]["tables"]["n_gold_missing"] == 0


def test_analyse_run_rejects_unlabelled_rows_beside_labelled_ones(tmp_path):
    """Half-migrated file: the rows with no split may be from another split, and
    silently adopting the labelled one counts them as if they were not."""
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    _rewrite_rows(
        tmp_path / "generations.baseline.jsonl",
        lambda r: _drop_split(r) if r["question_id"] == "q1" else r,
    )

    with pytest.raises(RuntimeError, match="no split at all"):
        analyse_run(tmp_path, bird_dir=bird)


def test_analyse_run_rejects_a_gold_file_sharing_no_question_ids(tmp_path):
    """An explicit --split can still name the wrong file; zero id overlap is never a
    real run, so it must not degrade into an all-missing-gold report."""
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    (bird / "eval_dataset" / "train_final.jsonl").write_text(
        json.dumps({"question_id": "other", "sql_rename": "SELECT * FROM t_a"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="question ids"):
        analyse_run(tmp_path, bird_dir=bird, split="train")


def test_analyse_run_rejects_a_directory_mixing_splits(tmp_path):
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    path = tmp_path / "generations.baseline.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["split"] = "train"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    with pytest.raises(RuntimeError, match="mixes splits"):
        analyse_run(tmp_path, bird_dir=bird)


def test_mcnemar_rejects_duplicate_question_ids():
    """A duplicated id means the generations file is corrupt; a p-value computed
    from it would be wrong in an unknown direction."""
    dupes = [{"question_id": "q1", "correct": True}] * 2
    with pytest.raises(ValueError, match="duplicate"):
        mcnemar(dupes, [{"question_id": "q1", "correct": False}])


def test_analyse_run_keeps_reports_when_one_pairing_fails(tmp_path):
    """One corrupt arm must not discard the per-arm reports already computed —
    the pairing records an error and the rest of the report survives."""
    bird = tmp_path / "bird"
    _write_run(tmp_path, bird)
    path = tmp_path / "generations.baseline.jsonl"
    rows = path.read_text(encoding="utf-8")
    path.write_text(rows + rows.splitlines()[0] + "\n", encoding="utf-8")  # duplicate id

    report = analyse_run(tmp_path, bird_dir=bird)
    assert "error" in report["mcnemar"]["baseline_vs_curated"]
    assert "duplicate" in report["mcnemar"]["baseline_vs_curated"]["error"]
    assert report["arms"]["curated"]["gradeable"]["n"] == 2  # other reports intact


def test_rank_report_pick_accuracy_ignores_rows_without_a_pick():
    """Dividing by every row reports 0.0 for a --no-llm-pick run and contradicts
    the summary's own schema_pick_accuracy."""
    no_pick = rank_report([{"gold_schema_rank": 1, "correct": True, "pick_hit": None}])
    assert no_pick["1"]["pick_accuracy"] is None
    assert no_pick["1"]["n_picked"] == 0

    mixed = rank_report([
        {"gold_schema_rank": 1, "correct": True, "pick_hit": True},
        {"gold_schema_rank": 1, "correct": False, "pick_hit": None},
    ])
    assert mixed["1"]["pick_accuracy"] == pytest.approx(1.0)  # 1/1, not 1/2
    assert mixed["1"]["n"] == 2 and mixed["1"]["n_picked"] == 1


# --------------------------------------------------------------------------- #
# analyse_run's pairing must carry the same correction policy as summary.json.
#
# This report enumerates every pair, so four arms is six tests. It used to publish
# six raw p-values under the name `p_value` and nothing else, which made the
# runbook's "check p_value_holm, not just p_value" impossible to follow here and
# left the uncorrected number as the only one on offer.
# --------------------------------------------------------------------------- #


def _write_four_arm_run(tmp_path, bird_dir, *, n=40):
    """A four-arm run over `n` questions, each arm strictly better than the last."""
    (bird_dir / "eval_dataset").mkdir(parents=True)
    gold = [{"question_id": f"q{i}", "sql_rename": f"SELECT * FROM t_{i}"} for i in range(n)]
    (bird_dir / "eval_dataset" / "test_final.jsonl").write_text(
        "".join(json.dumps(g) + "\n" for g in gold), encoding="utf-8"
    )
    # Nested correctness: arm k gets the first (k+1)*n//5 questions right. Nesting
    # makes every pair discordant in one direction only, so the p-values are small
    # enough for the correction to be visible rather than saturated at 1.0.
    for k, arm in enumerate(["baseline", "seeded", "curated", "curated_sme"]):
        cutoff = (k + 1) * n // 5
        rows = [
            {
                "question_id": g["question_id"], "db_id": "db_a", "arm": arm,
                "split": "test", "correct": i < cutoff, "routed_hit": True,
                "generated_sql": g["sql_rename"] if i < cutoff else "SELECT * FROM t_z",
                "gold_frozen": False, "gold_schema_rank": 1, "pick_hit": True,
                "retrieved_tables": [f"db_a.t_{i}"],
            }
            for i, g in enumerate(gold)
        ]
        (tmp_path / f"generations.{arm}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )


def test_analyse_run_holm_corrects_across_the_whole_pair_family(tmp_path):
    bird = tmp_path / "bird"
    _write_four_arm_run(tmp_path, bird)
    pairs = analyse_run(tmp_path, bird_dir=bird)["mcnemar"]

    assert len(pairs) == 6, "four arms is six tests; that is the multiplicity"
    for name, entry in pairs.items():
        assert entry["n_family"] == 6, name
        # The correction can only tighten. A Holm p below the raw p would mean the
        # adjustment is manufacturing significance rather than spending it.
        assert entry["p_value_holm"] >= entry["p_value"] - 1e-12, name
        assert entry["p_value_holm"] <= 1.0, name

    # Somewhere in a family this size the correction has to bite, or it is not
    # actually being applied — a pass-through would satisfy every assertion above.
    assert any(e["p_value_holm"] > e["p_value"] for e in pairs.values()), (
        "no pair was tightened, so Holm is not being applied"
    )


def test_analyse_run_names_what_a_non_adjacent_pair_bundles(tmp_path):
    """`baseline_vs_curated` cannot say whether the seed or the curator LLM paid.
    That is the conflation the `seeded` rung exists to break, and this report
    enumerates the pair whether or not anyone should quote it."""
    bird = tmp_path / "bird"
    _write_four_arm_run(tmp_path, bird)
    pairs = analyse_run(tmp_path, bird_dir=bird)["mcnemar"]

    # Adjacent, but NOT one variable: the rung adds train-SQL joins, train-SQL
    # metrics and decoy marking together (AUDIT E5). Both facts are now reported,
    # and `single_variable` means what it says.
    step = pairs["baseline_vs_seeded"]
    assert step["adjacent_rung"] is True
    assert step["single_variable"] is False
    assert len(step["mechanisms_changed"]) == 3
    assert "bundles" not in step, (
        "spelled absent rather than empty, matching summary.json's deltas.*_bundles"
    )

    compound = pairs["baseline_vs_curated"]
    assert compound["single_variable"] is False
    assert compound["bundles"] == ["seeded"]

    # Consecutive among the arms that ran, yet still bundling: the default arm set
    # changes two mechanisms at once, which is the docs-vs-protocol confound.
    # Reporting this pair as single-variable is the failure this flag prevents.
    sme = pairs["curated_vs_curated_sme"]
    assert sme["single_variable"] is False
    assert "bundles" not in sme, "nothing is skipped — the rung is adjacent"
    assert sme["single_variable"] is False
    assert len(sme["mechanisms_changed"]) == 2


def test_analyse_run_states_it_has_no_noise_floor(tmp_path):
    """A p-value below .05 on a delta smaller than the run can resolve is not a
    result. This report cannot measure resolution, so it has to say so."""
    bird = tmp_path / "bird"
    _write_four_arm_run(tmp_path, bird)
    report = analyse_run(tmp_path, bird_dir=bird)

    assert report["mcnemar_caveats"]["correction"] == "holm"
    assert "summary.json" in report["mcnemar_caveats"]["no_noise_floor"]
    # And it must not grow a resolution field that would look like one.
    for entry in report["mcnemar"].values():
        assert "mde_questions" not in entry
        assert "noise_floor" not in entry


def test_analyse_run_correction_family_excludes_pairs_that_errored(tmp_path):
    """An errored pair tested nothing. Counting it in the family would tighten every
    other pair's p-value on behalf of a test that never ran."""
    bird = tmp_path / "bird"
    _write_four_arm_run(tmp_path, bird, n=40)
    # Duplicate a question id in one arm: `_outcome_by_key` rejects it, so every
    # pair touching that arm errors while the rest still pair cleanly.
    path = tmp_path / "generations.curated_sme.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines) + lines[0], encoding="utf-8")

    pairs = analyse_run(tmp_path, bird_dir=bird)["mcnemar"]
    errored = [k for k, v in pairs.items() if "error" in v]
    tested = [k for k, v in pairs.items() if "p_value" in v]
    assert errored, "the duplicate id should have failed its pairings"
    assert len(tested) == 6 - len(errored)
    for k in tested:
        assert pairs[k]["n_family"] == len(tested), (
            "the family is the tests that ran, not the pairs that were attempted"
        )
    for k in errored:
        assert "p_value_holm" not in pairs[k]


def test_analyse_run_survives_arms_that_are_not_on_the_ladder(tmp_path):
    """`analyse_run` reads whatever `generations.*.jsonl` are in the directory, and a
    real run holds more than the fair rungs: `--replicate` writes `<arm>__replicate`
    and `--oracle` writes `oracle_sql`. Labelling used `ARM_ORDER.index`, which
    raises on those — crashing the entire report instead of skipping one label.

    Caught by running the CLI over a real run directory, not by a test: every test
    above builds only fair arms.
    """
    bird = tmp_path / "bird"
    _write_four_arm_run(tmp_path, bird, n=20)
    # Add the two kinds of off-ladder arm a real run produces.
    fair = (tmp_path / "generations.curated.jsonl").read_text(encoding="utf-8")
    for off in ("curated__replicate", "oracle_sql"):
        rows = [
            {**json.loads(line), "arm": off}
            for line in fair.splitlines()
            if line.strip()
        ]
        (tmp_path / f"generations.{off}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    report = analyse_run(tmp_path, bird_dir=bird)
    pairs = report["mcnemar"]

    # 6 arms -> 15 pairs, and the report is produced rather than raising.
    assert len(pairs) == 15
    for name, entry in pairs.items():
        if "curated__replicate" in name or "oracle_sql" in name:
            assert entry["single_variable"] is None, name
            assert "bundles" not in entry, name
        else:
            assert entry["single_variable"] in (True, False), name

    # The fair pairs keep their labels, so the off-ladder arms cost nothing.
    assert pairs["baseline_vs_seeded"]["adjacent_rung"] is True
    assert pairs["baseline_vs_curated"]["bundles"] == ["seeded"]


def test_analyse_run_excludes_off_ladder_arms_from_the_correction_family(tmp_path):
    """A replicate is not a hypothesis. It exists to measure the noise floor, and
    every pair it forms duplicates the one its source arm already forms — so a
    four-arm run plus one replicate would correct across ten tests where six distinct
    questions are being asked, making every real comparison harder to call."""
    bird = tmp_path / "bird"
    _write_four_arm_run(tmp_path, bird, n=20)
    fair = (tmp_path / "generations.curated.jsonl").read_text(encoding="utf-8")
    for off in ("curated__replicate", "oracle_sql"):
        rows = [{**json.loads(line), "arm": off} for line in fair.splitlines() if line.strip()]
        (tmp_path / f"generations.{off}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    pairs = analyse_run(tmp_path, bird_dir=bird)["mcnemar"]
    family = [k for k, v in pairs.items() if v.get("p_value_holm") is not None]

    assert len(pairs) == 15, "all pairs are still reported"
    assert len(family) == 6, "the family is the four fair arms' six pairs"
    for k in family:
        assert pairs[k]["n_family"] == 6
        assert "replicate" not in k and "oracle" not in k
    # Excluded pairs keep their raw p-value — readable, just not corrected as though
    # they were hypotheses. The adjusted value is an explicit `None` rather than an
    # absent key, matching summary.json so a reader of both files sees one shape.
    for k, v in pairs.items():
        if k not in family:
            assert "p_value" in v or "error" in v
            if "error" not in v:
                assert v["p_value_holm"] is None, k
                assert v["n_family"] == 6, "an excluded row still says how big the family was"


def test_analyse_run_excludes_a_pair_that_shared_no_questions(tmp_path):
    """Zero overlap yields `p_value = 1.0` from an empty discordance count. That is
    not a measurement, it is the arithmetic of having nothing to compare, and
    counting it tightens every other pair on behalf of a test never asked."""
    bird = tmp_path / "bird"
    _write_four_arm_run(tmp_path, bird, n=20)
    # Rewrite one arm onto question ids no other arm scored.
    path = tmp_path / "generations.curated_sme.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:
        r["question_id"] = "z" + str(r["question_id"])
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    pairs = analyse_run(tmp_path, bird_dir=bird)["mcnemar"]
    zero_overlap = [k for k, v in pairs.items() if v.get("n_paired") == 0]
    assert zero_overlap, "the fixture should have produced a zero-overlap pair"
    for k in zero_overlap:
        assert pairs[k]["p_value"] == 1.0
        assert pairs[k]["p_value_holm"] is None, (
            f"{k} shared no questions and must not be a member of the family"
        )
    family = [k for k, v in pairs.items() if v.get("p_value_holm") is not None]
    assert all(pairs[k]["n_family"] == len(family) for k in family)
    assert len(family) == 6 - len(zero_overlap)


def test_analyse_run_keys_ladder_pairs_in_ladder_order(tmp_path):
    """Alphabetically `curated` precedes `seeded`, so this report used to call the
    `seeded -> curated` step `curated_vs_seeded` while every other artifact called it
    the other way round, and a reader comparing the two by hand had to know that."""
    bird = tmp_path / "bird"
    _write_four_arm_run(tmp_path, bird, n=20)
    pairs = analyse_run(tmp_path, bird_dir=bird)["mcnemar"]

    assert "seeded_vs_curated" in pairs
    assert "curated_vs_seeded" not in pairs
    for key in pairs:
        lo, hi = key.split("_vs_")
        assert ARM_ORDER.index(lo) < ARM_ORDER.index(hi), key


def test_skipped_rungs_does_not_depend_on_argument_order():
    """Reversed, it used to return `[]` — "nothing skipped, one thing changed" — for
    a pair spanning the whole ladder. Every call site happened to pre-sort, so it was
    a footgun rather than a bug; this stops a new caller re-arming it."""
    from governed_bi.eval.arms import skipped_rungs

    for lo, hi in [
        ("baseline", "curated_sme"),
        ("baseline", "curated"),
        ("seeded", "curated_sme"),
        ("baseline", "seeded"),
    ]:
        assert skipped_rungs(lo, hi) == skipped_rungs(hi, lo), (lo, hi)
    assert skipped_rungs("curated_sme", "baseline") == [
        "seeded", "curated",
    ]


def test_a_resume_may_not_change_the_runs_scope(tmp_path):
    """The runbook's own resume line omitted every scope flag, and nothing refused.

    `--arms`, `--dbs`, `--oracle` and `--replicate` were re-read from argv on every
    invocation and derived from nothing in the directory, so resuming a Step 3 rung
    directory with the documented line dropped `--oracle` and picked up the four
    default arms: two LLM curator passes over the pool plus three extra serve passes —
    most of a Step 2 budget — from an operator who typed a resume. Reproduced offline
    before the fix: `[seeded] EX=... [curated] EX=... [curated_sme] EX=...`, none
    requested, no warning.

    Narrowing is equally fatal: `summary.json` is written once at the end, so a resume
    with fewer arms came back holding only the arms served in that attempt, blanking
    the rest (the row files survived, so it was recoverable — if you knew).
    """
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "split": "test",
                "arms": ["baseline"],
                "oracles": ["oracle_sql"],
                "replicate_of": None,
                "db_ids": ["beer_factory"],
            }
        ),
        encoding="utf-8",
    )
    base = {
        "split": "test",
        "arms": ["baseline"],
        "oracles": ["oracle_sql"],
        "replicate_of": None,
        "db_ids": ["beer_factory"],
    }
    _check_resume_manifest(tmp_path, base)  # unchanged scope resumes

    for key, changed, flag in (
        ("arms", ["baseline", "seeded", "curated", "curated_sme"], "--arms"),
        ("arms", [], "--arms"),  # narrowing
        ("oracles", [], "--oracle"),
        ("replicate_of", "curated", "--replicate"),
        ("db_ids", None, "--dbs"),  # None means the WHOLE split
        ("db_ids", ["beer_factory", "address"], "--dbs"),
    ):
        with pytest.raises(RuntimeError, match="Scope is not a resume knob"):
            _check_resume_manifest(tmp_path, {**base, key: changed})

    # A directory written before scope was recorded cannot be checked, and refusing
    # every such resume would strand work that is otherwise fine.
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": "test"}), encoding="utf-8"
    )
    _check_resume_manifest(tmp_path, {**base, "arms": ["anything", "at", "all"]})
