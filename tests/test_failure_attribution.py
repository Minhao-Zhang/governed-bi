"""Tests for the failure-attribution stack.

Weighted toward the mistakes that actually cost this project a set of
conclusions, rather than toward coverage. Each of the treatment tests below
corresponds to a real incident: an arm that never delivered its treatment and was
reported as a measured null, and a null-within-noise reported as a finding.
"""

from __future__ import annotations

import pytest

from governed_bi.eval.error_taxonomy import (
    ErrorClass,
    attribute_row,
    attribute_rows,
    summarise_attributions,
)
from governed_bi.eval.power import (
    correct_by_question,
    mcnemar,
    measure_floor,
    minimum_detectable_effect,
)
from governed_bi.eval.sql_diff import (
    Dimension,
    diff_sql,
    extract_features,
    is_frozen_constant,
)
from governed_bi.eval.treatment import (
    compare_arms,
    fingerprint_arm,
    treatment_reasons,
)
from governed_bi.stages import Stage

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
# sql_diff
# --------------------------------------------------------------------------- #


def test_aliases_resolve_so_equivalent_sql_compares_equal():
    """Gold writes T1/T2; a model writes real table names. Same query."""
    gold = (
        'SELECT T2.party FROM zip_congress AS T1 '
        'JOIN congress AS T2 ON T1.district = T2.cognress_rep_id'
    )
    gen = (
        "SELECT congress.party FROM zip_congress "
        "JOIN congress ON zip_congress.district = congress.cognress_rep_id"
    )
    diff = diff_sql(gen, gold)
    assert diff.mismatched() == [], diff.to_dict()


def test_same_column_name_on_a_different_table_is_a_mismatch():
    """The distinction a bare lowercased name cannot make.

    Both statements project a column called ``name``. One reads it from
    ``customers``, the other from ``orders``. Comparing unqualified names — which
    the previous taxonomy did — scores these identical.
    """
    gold = "SELECT customers.name FROM customers JOIN orders ON customers.id = orders.cid"
    gen = "SELECT orders.name FROM customers JOIN orders ON customers.id = orders.cid"
    diff = diff_sql(gen, gold)
    assert Dimension.projection in diff.mismatched()
    detail = diff.to_dict()["detail"]["projection"]
    assert detail["missing"] == ["customers.name"]
    assert detail["extra"] == ["orders.name"]


def test_projection_order_is_distinguished_from_a_wrong_column_set():
    gold = "SELECT a.x, a.y FROM a"
    gen = "SELECT a.y, a.x FROM a"
    diff = diff_sql(gen, gold)
    assert diff.dimensions[Dimension.projection].order_only is True
    # An order error must not be reported as missing/extra columns; the fix is
    # entirely different and much cheaper.
    assert diff.dimensions[Dimension.projection].missing == ()


def test_join_comparison_ignores_the_order_the_tables_were_written_in():
    gold = "SELECT a.x FROM a JOIN b ON a.k = b.k"
    gen = "SELECT a.x FROM b JOIN a ON b.k = a.k"
    diff = diff_sql(gen, gold)
    assert Dimension.join_keys not in diff.mismatched()
    assert Dimension.join_graph not in diff.mismatched()


def test_frozen_gold_is_flagged_and_not_compared():
    gold = "SELECT * FROM (VALUES ('Sunny'), ('Cloudy')) AS t(x)"
    assert is_frozen_constant(gold)
    diff = diff_sql("SELECT weather FROM days", gold)
    assert diff.gold_frozen
    assert not diff.comparable()
    # Every dimension unknown, so nothing lands in a structural bucket.
    assert diff.mismatched() == []


def test_unparseable_generated_sql_is_unknown_not_a_structural_error():
    diff = diff_sql("SELCT oops FROM", "SELECT a.x FROM a")
    assert diff.gold_parsed and not diff.gen_parsed
    assert diff.mismatched() == []


def test_unresolved_aliases_downgrade_table_sensitive_dimensions_to_unknown():
    """A comparison that cannot be trusted must not report ``match``."""
    features = extract_features("SELECT x FROM a")
    assert features.parsed


# --------------------------------------------------------------------------- #
# error_taxonomy
# --------------------------------------------------------------------------- #


def _row(**kw):
    base = {
        "question_id": "q1",
        "correct": False,
        "generated_sql": "SELECT a.x FROM a",
        "routed_hit": True,
        "pick_hit": True,
    }
    base.update(kw)
    return base


def test_routing_failure_outranks_whatever_the_sql_says():
    """A query against the wrong schema is a routing failure, full stop.

    Its SQL is written over tables it should never have seen, so charging it to
    join or projection errors would inflate those classes with queries no
    generation fix can rescue.
    """
    a = attribute_row(
        _row(pick_hit=False, generated_sql="SELECT z.q FROM z"),
        "SELECT a.x FROM a",
    )
    assert a.stage is Stage.schema_pick
    assert a.primary is ErrorClass.wrong_schema
    assert a.classes == (ErrorClass.wrong_schema,)


def test_gold_missing_from_the_shortlist_is_an_embedding_failure_not_a_picker_one():
    a = attribute_row(
        _row(pick_hit=False), "SELECT a.x FROM a", gold_in_shortlist=False
    )
    assert a.stage is Stage.shortlist
    assert a.primary is ErrorClass.embedding_wall


def test_wrong_table_attributes_to_table_select_not_sql_generate():
    a = attribute_row(
        _row(generated_sql="SELECT b.x FROM b"), "SELECT a.x FROM a"
    )
    assert a.stage is Stage.table_select
    assert a.primary is ErrorClass.wrong_table


def test_structurally_identical_but_wrong_is_a_value_level_failure():
    """The LIKE '%Logan%' class: nothing structural differs, the answer is wrong."""
    a = attribute_row(
        _row(generated_sql="SELECT a.x FROM a WHERE a.city = 'arecibo'"),
        "SELECT a.x FROM a WHERE a.city = 'ARECIBO'",
    )
    # The literal comparison is case-folded on purpose, so this reaches value_level
    # rather than firing a spurious structural class.
    assert a.primary is ErrorClass.value_level
    # Charged to `sql_generate`: the statement executed fine and returned exactly the
    # rows it asked for, so `execute` is not the failing stage — the generator wrote
    # the wrong value. (This assertion has been `Stage.execute` and then `None`; both
    # were wrong. `execute` named a component that did not fail; `None` left the class
    # permanently unattributed behind a gate no run could satisfy, so
    # `by_error_stage` did not sum to `n_wrong`.)
    assert a.stage is Stage.sql_generate


def test_frozen_gold_is_excluded_from_the_denominator_not_blamed_on_generation():
    a = attribute_row(
        _row(), "SELECT * FROM (VALUES ('x')) AS t(c)"
    )
    assert a.primary is ErrorClass.gold_unusable
    assert a.gradeable is False
    assert a.stage is None


def test_a_refusal_keeps_its_live_stage_and_is_not_relabelled():
    """A governed refusal must not be re-derived as a SQL failure."""
    a = attribute_row(
        _row(generated_sql=None, refused_by="guardrail", error="refusal"),
        "SELECT a.x FROM a",
    )
    assert a.stage is Stage.guardrail
    assert a.primary is None


def test_stage_buckets_are_mutually_exclusive_and_sum_to_the_wrong_count():
    rows = [
        _row(question_id="q1", correct=True),
        _row(question_id="q2", pick_hit=False),
        _row(question_id="q3", generated_sql="SELECT b.x FROM b"),
        _row(question_id="q4", generated_sql="SELECT a.y FROM a"),
        _row(question_id="q5", generated_sql=None, refused_by="guardrail"),
    ]
    gold = {f"q{i}": "SELECT a.x FROM a" for i in range(1, 6)}
    summary = summarise_attributions(attribute_rows(rows, gold))
    assert summary["n_wrong"] == 4
    # Every wrong row lands in exactly one stage bucket.
    assert sum(summary["by_error_stage"].values()) == 4


def test_multi_class_share_is_reported_so_headroom_is_not_read_as_additive():
    rows = [
        _row(
            question_id="q1",
            generated_sql="SELECT b.y FROM b WHERE b.z = 2",
        )
    ]
    gold = {"q1": "SELECT a.x FROM a WHERE a.w = 1"}
    summary = summarise_attributions(attribute_rows(rows, gold))
    assert summary["multi_class_share"] == 1.0
    assert summary["classes_per_query"]


# --------------------------------------------------------------------------- #
# treatment — the checks that would have caught the two real incidents
# --------------------------------------------------------------------------- #


def test_arms_delivering_identical_context_are_flagged_not_scored():
    """The oracle incident: 9,154 notes on disk, none reaching a prompt.

    Both arms hand the model the same context on every question. Their scores will
    still differ, because the model is not deterministic. That difference is not a
    result and must not read as one.
    """
    a = [{"question_id": str(i), "context_hash": f"h{i}"} for i in range(50)]
    b = [{"question_id": str(i), "context_hash": f"h{i}"} for i in range(50)]
    pair = compare_arms("curated", a, "oracle", b)
    assert pair.divergence == 0.0
    assert pair.delivered is False
    assert treatment_reasons([], [pair])


def test_arms_that_genuinely_differ_pass():
    a = [{"question_id": str(i), "context_hash": f"h{i}"} for i in range(50)]
    b = [{"question_id": str(i), "context_hash": f"x{i}"} for i in range(50)]
    assert compare_arms("a", a, "b", b).delivered is True


def test_a_missing_context_hash_reads_as_unverified_not_as_delivered():
    """Absence of evidence is not evidence of a treatment."""
    a = [{"question_id": str(i)} for i in range(50)]
    b = [{"question_id": str(i)} for i in range(50)]
    pair = compare_arms("a", a, "b", b)
    assert pair.divergence is None
    assert pair.delivered is False
    assert any("unverified" in r for r in pair.reasons)


def test_a_corpus_full_of_notes_that_injected_none_disqualifies_the_arm():
    """The exact signature of both incidents, from the arm side."""
    rows = [
        {"question_id": str(i), "n_notes_injected": 0, "context_hash": f"h{i}"}
        for i in range(20)
    ]
    fp = fingerprint_arm("oracle", rows, corpus_note_assets=9154)
    reasons = treatment_reasons([fp])
    assert reasons and "injected zero" in reasons[0]


def test_an_arm_recording_nothing_about_delivery_is_unverified():
    fp = fingerprint_arm("legacy", [{"question_id": "q1", "correct": True}])
    assert fp.observed is False
    assert treatment_reasons([fp])


# --------------------------------------------------------------------------- #
# power
# --------------------------------------------------------------------------- #


def test_mcnemar_is_exact_and_ignores_the_questions_both_arms_got_right():
    a = {"q1": True, "q2": False, "q3": True}
    b = {"q1": True, "q2": True, "q3": True}
    r = mcnemar("a", a, "b", b)
    assert (r.n_b_only, r.n_a_only) == (1, 0)
    assert r.net == 1
    assert r.p_value == pytest.approx(1.0)


def test_a_question_only_one_arm_answered_is_excluded_not_scored_wrong():
    r = mcnemar("a", {"q1": True, "q2": True}, "b", {"q1": True})
    assert r.n_shared == 1
    assert r.n_discordant == 0


def test_an_effect_inside_the_measured_noise_is_reported_as_unresolvable():
    """The published '+5 questions, not significant' result.

    With a 6.7% discordance over 2030 questions the run cannot resolve anything
    smaller than ~33 questions. Calling +5 a null result is a statement the run was
    never entitled to make.
    """
    mde = minimum_detectable_effect(2030, 135 / 2030)
    assert 30 < mde.questions < 36
    assert mde.resolves(5) is False
    assert mde.resolves(43) is True
    assert "below resolution" in mde.verdict(5)


def test_a_replicate_that_drifted_is_flagged_as_not_a_floor():
    """A replicate should disagree at random, not improve."""
    first = {f"q{i}": False for i in range(200)}
    second = {f"q{i}": i < 100 for i in range(200)}  # 100 one-way flips
    floor = measure_floor(first, second)
    assert floor.net == 100
    assert floor.suspect is True


def test_a_genuine_replicate_is_not_flagged():
    first = {f"q{i}": i % 2 == 0 for i in range(200)}
    second = dict(first)
    for i in (1, 4, 7, 10):  # a few flips, balanced-ish
        second[f"q{i}"] = not second[f"q{i}"]
    floor = measure_floor(first, second)
    assert floor.suspect is False


def test_exact_binomial_survives_a_discordant_count_above_the_float_limit():
    """A *float* power overflows past n=1024; the integer denominator must not.

    ``2 ** n`` with an int exponent is arbitrary-precision and fine. The hazard is
    ``2.0 ** n`` / ``float(2 ** n)``, which this implementation avoids by keeping
    the denominator an int and letting int true-division round it.
    """
    a = {f"q{i}": i % 2 == 0 for i in range(3000)}
    b = {f"q{i}": i % 3 == 0 for i in range(3000)}
    r = mcnemar("a", a, "b", b)
    assert r.n_discordant > 1024
    assert 0.0 <= r.p_value <= 1.0


def test_correct_by_question_reads_both_id_spellings():
    assert correct_by_question([{"request_id": "q1", "correct": True}]) == {"q1": True}


# --------------------------------------------------------------------------- #
# driver guards
# --------------------------------------------------------------------------- #


def test_a_delta_between_unmeasured_rates_is_none_not_a_crash():
    """Found by running the offline smoke, which had been broken.

    Rates became ``None`` at an empty denominator so "measured zero" could be told
    from "never measured". The delta arithmetic kept subtracting them, so the
    ``--skip-agent`` path — where no arm produces SQL and every decoy rate is
    ``None`` — raised ``TypeError`` after the full run and before ``summary.json``
    was written, losing the run's artifacts.
    """
    from governed_bi.eval.harness import _delta

    assert _delta(None, None) is None
    assert _delta(0.5, None) is None
    assert _delta(None, 0.5) is None
    assert _delta(0.5, 0.2) == pytest.approx(0.3)
    # A genuine zero must still be a zero, not swallowed into None.
    assert _delta(0.0, 0.0) == 0.0


# --------------------------------------------------------------------------- #
# The replicate / quotability gate
# --------------------------------------------------------------------------- #


def _rows(arm, n, *, ctx, correct_every=3):
    return [
        {
            "question_id": f"q{i}",
            "arm": arm,
            "correct": i % correct_every == 0,
            "context_hash": f"{ctx}{i}",
        }
        for i in range(n)
    ]


def test_measuring_the_noise_floor_does_not_disqualify_the_run():
    """A replicate is the same corpus twice, so identical context is the design.

    Before the exemption, asking for resolution made the run un-quotable for
    "the arms are the same experiment run twice" - which is what a replicate IS.
    The harness computed the resolution number and then voided the run for it.
    """
    from governed_bi.eval.index import quotable
    from governed_bi.eval.run_datalake import _compare_arms

    rows_by_arm = {
        "baseline": _rows("baseline", 60, ctx="b"),
        "curated": _rows("curated", 60, ctx="c"),
        "curated__replicate": _rows("curated__replicate", 60, ctx="c"),
    }
    _comparisons, divergences = _compare_arms(rows_by_arm, replicate_of="curated")
    pair = next(
        d for d in divergences if {d["arm_a"], d["arm_b"]} == {"curated", "curated__replicate"}
    )
    assert pair["expected_identical"] is True
    assert pair["divergence"] == 0.0  # the measurement is kept, not discarded
    assert pair["treatment_delivered"] is None  # index.py tests `is False`
    assert pair["reasons"] == []

    record = {
        "manifest_readable": True,
        "split": "test",
        "n_questions": 200,
        "headline": {"curated": {"crash_rate": 0.0, **_MEASURED_FREE_PASSES}},
        "treatment_not_delivered": [
            r for d in divergences if d.get("treatment_delivered") is False
            for r in d.get("reasons", [])
        ],
    }
    ok, reasons = quotable(record)
    assert ok, reasons


def test_a_replicate_that_did_not_replicate_is_flagged():
    """The assertion a replicate actually needs, which is the inverse of the usual one."""
    from governed_bi.eval.run_datalake import _compare_arms

    rows_by_arm = {
        "curated": _rows("curated", 60, ctx="c"),
        "curated__replicate": _rows("curated__replicate", 60, ctx="DIFFERENT"),
    }
    _c, divergences = _compare_arms(rows_by_arm, replicate_of="curated")
    pair = divergences[0]
    assert pair["replicate_drifted"] is True
    assert pair["reasons"] and "not the same configuration" in pair["reasons"][0]


def test_a_genuine_undelivered_pair_is_still_caught_alongside_a_replicate():
    """The exemption must not become a blanket amnesty."""
    from governed_bi.eval.run_datalake import _compare_arms

    rows_by_arm = {
        "curated": _rows("curated", 60, ctx="c"),
        "curated__replicate": _rows("curated__replicate", 60, ctx="c"),
        "oracle": _rows("oracle", 60, ctx="c"),  # same context, NOT a replicate
    }
    _c, divergences = _compare_arms(rows_by_arm, replicate_of="curated")
    bad = [d for d in divergences if d.get("treatment_delivered") is False]
    assert bad, "an arm pair delivering identical context must still be flagged"
    assert all({"curated", "curated__replicate"} != {d["arm_a"], d["arm_b"]} for d in bad)


def test_duplicate_question_ids_are_fatal_on_the_canonical_path():
    """analysis.py treated this as corruption; power.py silently last-write-wins."""
    with pytest.raises(ValueError, match="duplicate question id"):
        correct_by_question(
            [{"question_id": "q1", "correct": True}, {"question_id": "q1", "correct": False}]
        )


def test_the_package_exports_both_mcnemars_under_distinct_names():
    """`from governed_bi.eval import mcnemar` used to silently get the offline one."""
    import governed_bi.eval as ev
    from governed_bi.eval import analysis, power

    assert ev.mcnemar is analysis.mcnemar
    assert ev.paired_mcnemar is power.mcnemar


def test_a_degenerate_oracle_pair_does_not_void_the_fair_ladder():
    """A broken diagnostic must not disqualify the results it was meant to explain.

    Seen live on a 2-db smoke: baseline routed correctly, so oracle_schema pinned
    the same schema and delivered identical context. That makes the RUNG's number
    meaningless, which is worth saying, but baseline-vs-curated is untouched by it.
    """
    from governed_bi.eval.index import quotable
    from governed_bi.eval.run_datalake import _compare_arms

    rows_by_arm = {
        "baseline": _rows("baseline", 40, ctx="same"),
        "curated": _rows("curated", 40, ctx="c"),
        "oracle_schema": _rows("oracle_schema", 40, ctx="same"),
    }
    _c, divergences = _compare_arms(rows_by_arm)
    oracle_pair = next(d for d in divergences if "oracle_schema" in (d["arm_a"], d["arm_b"]))
    # The finding is kept in the artifact...
    assert oracle_pair["treatment_delivered"] is False
    assert oracle_pair["diagnostic_pair"] is True

    # ...but it does not reach the gate.
    record = {
        "manifest_readable": True,
        "split": "test",
        "n_questions": 200,
        "headline": {"curated": {"crash_rate": 0.0, **_MEASURED_FREE_PASSES}},
    }
    from governed_bi.eval.index import _undelivered

    record["treatment_not_delivered"] = _undelivered(
        {"treatment_divergence": divergences, "arms": {}}
    )
    ok, reasons = quotable(record)
    assert ok, reasons


def test_two_fair_arms_delivering_the_same_context_still_void_the_run():
    """The diagnostic exemption must not become a blanket amnesty."""
    from governed_bi.eval.index import _undelivered
    from governed_bi.eval.run_datalake import _compare_arms

    rows_by_arm = {
        "baseline": _rows("baseline", 40, ctx="same"),
        "curated": _rows("curated", 40, ctx="same"),
    }
    _c, divergences = _compare_arms(rows_by_arm)
    assert _undelivered({"treatment_divergence": divergences, "arms": {}})


def test_a_drifted_replicate_fails_quotable_and_publishes_no_resolution():
    """Setting reasons without treatment_delivered=False files a complaint
    nothing ever opens, and a floor from a broken control is worse than none."""
    from governed_bi.eval.index import _undelivered
    from governed_bi.eval.run_datalake import _compare_arms

    rows_by_arm = {
        "curated": _rows("curated", 40, ctx="c"),
        "curated__replicate": _rows("curated__replicate", 40, ctx="DRIFTED"),
    }
    comparisons, divergences = _compare_arms(rows_by_arm, replicate_of="curated")
    pair = divergences[0]
    assert pair["replicate_drifted"] is True
    assert pair["treatment_delivered"] is False  # reaches the gate
    assert _undelivered({"treatment_divergence": divergences, "arms": {}})
    # And no resolution is published off a control that did not hold.
    assert comparisons[0]["noise_floor"] is None
    assert comparisons[0]["detectable"] is None


def test_a_healthy_replicate_still_publishes_its_floor():
    from governed_bi.eval.run_datalake import _compare_arms

    rows_by_arm = {
        "curated": _rows("curated", 40, ctx="c"),
        "curated__replicate": _rows("curated__replicate", 40, ctx="c"),
    }
    comparisons, _d = _compare_arms(rows_by_arm, replicate_of="curated")
    assert comparisons[0]["noise_floor"] is not None


def test_an_unrecorded_total_schemas_does_not_suppress_a_routing_miss():
    """`(total_schemas or 0) <= 1` failed open: a missing field read as bypassed."""
    from governed_bi.eval.run_datalake import _summarise_rows

    row = {
        "question_id": "q1", "correct": False, "generated_sql": "SELECT a.x FROM a",
        "routed_hit": False, "pick_hit": False, "routing_bypassed": False,
    }
    summary = _summarise_rows("arm", [row], gold={"q1": "SELECT a.x FROM a"})
    assert summary["errors"]["by_error_stage"] == {"schema_pick": 1}


def test_stage_execute_is_reserved_for_a_statement_that_failed_to_run():
    """`value_level` is a generation defect, so `execute` must stay unused by it.

    `Stage.execute` is stamped by the live path via `refused_by="execution"`. If the
    taxonomy also charged it for wrong-literal rows, the bucket would mix "the query
    errored" with "the query ran perfectly and the value was wrong" — two findings
    with opposite fixes.
    """
    value = attribute_row(
        _row(generated_sql="SELECT a.x FROM a WHERE a.c = 'x'"),
        "SELECT a.x FROM a WHERE a.c = 'X'",
    )
    assert value.primary is ErrorClass.value_level
    assert value.stage is Stage.sql_generate

    errored = attribute_row(
        {
            "question_id": "q1",
            "correct": False,
            "generated_sql": None,
            "refused_by": "execution",
        },
        "SELECT a.x FROM a",
    )
    assert errored.stage is Stage.execute


def test_result_shape_is_derived_from_grading_fields_and_costs_no_query():
    """The descriptive half of a value-level failure, free from what grading wrote.

    `score_sql_hashes` already executes the generated statement and records
    `pred_nrows`; the gold artifact ships `nrows`. An earlier design ran a second pair
    of queries per wrong row to recover exactly this.
    """
    empty = attribute_row(
        _row(
            generated_sql="SELECT a.x FROM a WHERE a.c = 'x'",
            pred_nrows=0,
            gold_nrows=7,
        ),
        "SELECT a.x FROM a WHERE a.c = 'X'",
    )
    assert empty.result_shape == "empty_result"

    same = attribute_row(
        _row(
            generated_sql="SELECT a.x FROM a WHERE a.c = 'x'",
            pred_nrows=7,
            gold_nrows=7,
        ),
        "SELECT a.x FROM a WHERE a.c = 'X'",
    )
    assert same.result_shape == "same_row_count"

    differs = attribute_row(
        _row(
            generated_sql="SELECT a.x FROM a WHERE a.c = 'x'",
            pred_nrows=3,
            gold_nrows=7,
        ),
        "SELECT a.x FROM a WHERE a.c = 'X'",
    )
    assert differs.result_shape == "row_count_differs"

    # Unrecorded counts are unmeasured, never "matched".
    assert (
        attribute_row(
            _row(generated_sql="SELECT a.x FROM a WHERE a.c = 'x'"),
            "SELECT a.x FROM a WHERE a.c = 'X'",
        ).result_shape
        is None
    )
    # ...and it never decides a stage.
    assert empty.stage is same.stage is differs.stage is Stage.sql_generate


def test_a_statement_that_raised_at_grading_is_not_a_bad_literal():
    """Adversarial review found this: the grader executes the generated SQL, and when
    that raises, `score_sql_hashes` records the error and no row counts. The driver
    keeps the row gradeable — correctly, the model's statement failed, not ours — but
    the structural diff then found nothing differing (both sides parse identically)
    and charged it to `value_level`, i.e. "ran fine, wrong value". It did not run.
    """
    a = attribute_row(
        {
            "question_id": "q1",
            "outcome": "answered",
            "correct": False,
            "generated_sql": "SELECT a FROM t WHERE a = '5'",
            "error": "exec_error:InvalidTextRepresentation: invalid input syntax",
        },
        "SELECT a FROM t WHERE a = '5'",
    )
    assert a.primary is ErrorClass.execution_error
    assert a.stage is Stage.execute, (
        "same bucket the live path uses for refused_by='execution' — an execution "
        "failure is one finding whether the agent hit it or the grader did"
    )
    assert a.gradeable is True


def test_an_execution_error_is_not_counted_as_a_crash():
    """It is the model's statement that failed, not the harness."""
    from governed_bi.stages import Outcome, classify_row

    outcome, _stage, recognised = classify_row(
        {
            "generated_sql": "SELECT 1",
            "error": "exec_error:UndefinedColumn: no such column",
        }
    )
    assert outcome is Outcome.answered
    assert recognised is True


def test_both_blocks_agree_on_what_a_diagnostic_pair_is():
    """`comparisons[]` and `treatment_divergence[]` used to disagree.

    `diagnostic_pair` was computed twice — broadly for `comparisons[]` (off-ladder,
    oracle, OR replicate) and narrowly for `treatment_divergence[]` (oracle only). So
    `baseline vs seeded__replicate` read `true` in one block and `null` in the other.
    `index._undelivered` skips on the divergence one, so that pair could block
    quotability while its own comparison entry called it a diagnostic.

    The control-vs-source pair is the deliberate exception and must NOT be excused:
    its sameness is the measurement.
    """
    from governed_bi.eval.run_datalake import _compare_arms

    rows = {
        arm: [
            {
                "question_id": f"q{i}",
                "db_id": "db_a",
                "correct": i % 2 == 0,
                "context_hash": f"{arm}-{i}",
            }
            for i in range(8)
        ]
        for arm in ("baseline", "seeded", "seeded__replicate", "oracle_sql")
    }
    comparisons, divergences = _compare_arms(rows, replicate_of="seeded")

    by_pair = {frozenset((c["arm_a"], c["arm_b"])): c for c in comparisons}
    div_by_pair = {frozenset((d["arm_a"], d["arm_b"])): d for d in divergences}
    assert by_pair.keys() == div_by_pair.keys()

    control = frozenset(("seeded", "seeded__replicate"))
    for key in by_pair:
        if key == control:
            continue
        assert bool(by_pair[key]["diagnostic_pair"]) == bool(
            div_by_pair[key].get("diagnostic_pair")
        ), f"blocks disagree about {sorted(key)}"

    # The specific pair that used to disagree.
    off = frozenset(("baseline", "seeded__replicate"))
    assert by_pair[off]["diagnostic_pair"] is True
    assert div_by_pair[off]["diagnostic_pair"] is True

    # ...and the control is still gated, not excused.
    assert div_by_pair[control].get("diagnostic_pair") is not True
    assert div_by_pair[control]["expected_identical"] is True


def test_a_rung_only_run_is_not_quotable_when_the_rungs_measured_nothing():
    """The treatment gate went completely inert on the shape the runbook prescribes.

    `_undelivered` skips `diagnostic_pair` entries so a dead oracle rung does not
    disqualify the fair ladder — correct, but it assumes there IS a fair ladder beside
    it. Step 3 is `--arms baseline --oracle ...`, where every pair is diagnostic, so a
    run whose rungs delivered byte-identical context to the base arm on every question
    — the exact no-op `plan_arm_serving` exists to prevent — reported `quotable: true`
    with an empty `not_quotable_because`.
    """
    from governed_bi.eval.index import _undelivered, quotable

    rung_only = {
        "treatment_divergence": [
            {
                "arm_a": "baseline",
                "arm_b": "oracle_schema",
                "diagnostic_pair": True,
                "treatment_delivered": False,
                "reasons": ["identical context on 2030 of 2030 questions"],
            }
        ],
        "arms": {},
    }
    reasons = _undelivered(rung_only)
    assert reasons, "a rung-only run with a dead rung must not pass silently"
    assert any("no non-diagnostic comparison" in r for r in reasons), reasons

    record = {
        "manifest_readable": True,
        "split": "test",
        "n_questions": 2030,
        "headline": {"baseline": {"crash_rate": 0.0, **_MEASURED_FREE_PASSES}},
        "treatment_not_delivered": reasons,
    }
    ok, why = quotable(record)
    assert not ok
    assert any("identical context" in r for r in why), why

    # ...but a dead rung BESIDE a real ladder still does not disqualify the ladder.
    with_ladder = {
        "treatment_divergence": [
            {"arm_a": "baseline", "arm_b": "curated", "treatment_delivered": None,
             "reasons": []},
            {"arm_a": "baseline", "arm_b": "oracle_schema", "diagnostic_pair": True,
             "treatment_delivered": False, "reasons": ["rung was a no-op"]},
        ],
        "arms": {},
    }
    assert _undelivered(with_ladder) == []


def test_an_arm_that_recorded_no_delivery_fields_is_not_quotable():
    """`treatment_reasons` has two arm-level checks and the ledger read only one.

    An arm whose rows recorded no delivery fields at all cannot have its numbers
    attributed to its corpus — that is the whole premise of the ladder. The check
    existed in `treatment.py` and was never called from `_undelivered`, so a run with
    a dead provenance relay passed the gate on 2030 questions.
    """
    from governed_bi.eval.index import _undelivered

    reasons = _undelivered(
        {
            "arms": {
                "curated": {"treatment": {"n_rows": 2030, "n_rows_observed": 0}},
            }
        }
    )
    assert any("recorded no delivery fields" in r for r in reasons), reasons

    # A healthy arm is silent.
    assert _undelivered(
        {"arms": {"curated": {"treatment": {"n_rows": 2030, "n_rows_observed": 2030}}}}
    ) == []
