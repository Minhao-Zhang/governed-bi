"""Every rate's denominator must be the set of rows that could be in its numerator.

Found by an adversarial review of the crash/refusal split: fixing `refusal_rate` had
quietly relocated the same defect into `routing_recall`, which charged every crash to
the router, and `cond_ex_given_routing` divided *all* correct rows by *only* routed
ones — EX/routing_recall wearing a conditional's name.
"""

from __future__ import annotations

from governed_bi.eval.run_datalake import _summarise_rows


def _row(qid, **kw):
    row = {
        "question_id": qid,
        "generated_sql": "SELECT 1",
        "correct": False,
        "correct_strict": False,
        "routed_hit": True,
        "error": None,
    }
    row.update(kw)
    return row


def _crash(qid):
    # What _grade_one writes when solve_with_meta raises: no meta at all, so the row
    # records routed_schemas=[] and routed_hit=False whether or not the router ran.
    return _row(
        qid,
        generated_sql=None,
        routed_hit=False,
        error="KeyError: 'schema'",
        routed_schemas=[],
    )


# --------------------------------------------------------------------------- #
# routing_recall must not absorb crashes
# --------------------------------------------------------------------------- #


def test_a_crash_does_not_count_against_the_router():
    # 3 routed + 1 crash. The router saw 3 questions and got all 3 right.
    rows = [_row("q1"), _row("q2"), _row("q3"), _crash("q4")]
    s = _summarise_rows("curated", rows)
    assert s["n_crashed"] == 1
    assert s["n_routing_observed"] == 3
    assert s["routing_recall"] == 1.0, (
        "a crashed turn returns no meta, so its routed_hit=False says nothing about "
        "the router; counting it charges our bug to routing"
    )


def test_a_genuine_routing_miss_still_counts():
    rows = [_row("q1"), _row("q2", routed_hit=False)]
    s = _summarise_rows("curated", rows)
    assert s["n_routing_observed"] == 2
    assert s["routing_recall"] == 0.5


def test_a_crash_after_routing_cannot_push_recall_above_one():
    """The other half of the crash carve-out, and the one that was missing.

    A turn that crashes *after* ``assemble`` carries real ``routed_schemas`` on its
    provenance, so ``routed_hit`` is a genuine ``True``. The numerator was taken over
    the routing rows and the denominator was recomputed with its own crash filter, so
    that row counted as a hit and was struck from the denominator. Measured at 3 such
    crashes plus one clean miss: ``crash_rate 0.75, n_routing_observed 1,
    routing_recall 3.0`` — a rate above 1.0 in the artifact.

    That is the shape a rate-limit storm at ``--workers 8`` produces, which is the
    condition the runbook explicitly warns about. Both terms now come from one
    population.
    """
    crashed_but_routed = [
        _row(
            f"c{i}",
            generated_sql=None,
            routed_hit=True,
            error="RateLimitError: 429",
        )
        for i in range(3)
    ]
    rows = [*crashed_but_routed, _row("q1", routed_hit=False)]
    s = _summarise_rows("curated", rows)
    # The headline claim first: a rate is a rate.
    assert s["routing_recall"] <= 1.0, s["routing_recall"]
    assert s["routing_recall"] == 0.0
    assert s["n_routing_observed"] == 1
    assert s["n_crashed"] == 3
    assert s["n_routing_crashed"] == 3


def test_the_correct_answer_buckets_partition_n_correct():
    """Five disjoint buckets, and their sum is published so a sixth exclusion shows up.

    ``n_correct_bypassed`` was computed by subtraction — everything not in the routing
    population — which was right while "bypassed" was the only exclusion. Once
    unrecorded turns were also excluded, a correct answer on a turn that recorded no
    routing decision was booked as "bypassed", giving ``n_correct_bypassed >
    n_routing_bypassed``: an impossible pair for anyone checking the identity
    ``docs/measurement.md`` tells them to check.
    """
    rows = [
        _row("routed", correct=True, routed_hit=True),
        _row("missed", correct=True, routed_hit=False),
        _row("pinned", correct=True, routing_bypassed=True),
        _row("silent", correct=True, routed_hit=None, routed_schemas=None),
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_correct"] == 4
    assert s["n_correct_routed"] == 1
    assert s["n_correct_unrouted"] == 1
    assert s["n_correct_bypassed"] == 1
    assert s["n_correct_routing_unrecorded"] == 1
    assert s["n_correct_routing_crashed"] == 0
    assert s["n_correct_unaccounted"] == 0
    # The pair that used to be impossible.
    assert s["n_correct_bypassed"] <= s["n_routing_bypassed"]


def test_routing_recall_is_none_when_every_turn_crashed():
    s = _summarise_rows("curated", [_crash("q1"), _crash("q2")])
    assert s["n_routing_observed"] == 0
    assert s["routing_recall"] is None, "nothing reached the router, so nothing was measured"


# --------------------------------------------------------------------------- #
# cond_ex_given_routing must draw both terms from the routed rows
# --------------------------------------------------------------------------- #


def test_conditional_ex_uses_only_routed_rows_in_both_terms():
    # 2 routed (1 correct), 1 mis-routed but somehow correct.
    rows = [
        _row("q1", correct=True),
        _row("q2", correct=False),
        _row("q3", routed_hit=False, correct=True),
    ]
    s = _summarise_rows("curated", rows)
    assert s["cond_ex_given_routing"] == 0.5
    assert s["n_correct_unrouted"] == 1


def test_conditional_ex_cannot_exceed_one():
    # The old formula (all correct / routed only) gave 2/1 = 2.0 here.
    rows = [_row("q1", correct=True), _row("q2", routed_hit=False, correct=True)]
    s = _summarise_rows("curated", rows)
    assert s["cond_ex_given_routing"] == 1.0
    assert s["n_correct_unrouted"] == 1


def test_no_correct_unrouted_is_the_normal_case():
    rows = [_row("q1", correct=True), _row("q2", routed_hit=False)]
    s = _summarise_rows("curated", rows)
    assert s["n_correct_unrouted"] == 0
    # With nothing correct off-route, EX == routing_recall x cond_ex_given_routing.
    assert s["ex_lenient"] == s["routing_recall"] * s["cond_ex_given_routing"]


# --------------------------------------------------------------------------- #
# an arm that wrote no SQL has not earned a perfect governance score
# --------------------------------------------------------------------------- #


def test_decoy_rate_is_unmeasured_when_nothing_produced_sql():
    s = _summarise_rows("curated", [_crash("q1"), _crash("q2")])
    assert s["decoy_touch_rate"] is None, (
        "an arm that wrote no SQL touched no decoys because it wrote no SQL; 0.0 "
        "would make the worst possible run look like the best-governed one"
    )
    assert s["conditional_ex_lenient"] is None


def test_decoy_rate_is_measured_when_some_row_produced_sql():
    rows = [_row("q1", decoy_touch=True), _crash("q2")]
    s = _summarise_rows("curated", rows)
    assert s["decoy_touch_rate"] == 1.0


# --------------------------------------------------------------------------- #
# ungradeable rows are counted, not merely absorbed
# --------------------------------------------------------------------------- #


def test_unusable_gold_rows_are_counted():
    rows = [_row("q1", error="gold_unusable:missing_hash"), _row("q2")]
    s = _summarise_rows("curated", rows)
    assert s["n_gold_unusable"] == 1
    assert s["n_missing_gold"] == 0


def test_missing_and_unusable_gold_are_separate_counts():
    rows = [_row("q1", error="missing_gold_hash"), _row("q2", error="gold_unusable:x")]
    s = _summarise_rows("curated", rows)
    assert (s["n_missing_gold"], s["n_gold_unusable"]) == (1, 1)


# --------------------------------------------------------------------------- #
# Console formatting must survive an unmeasured rate
# --------------------------------------------------------------------------- #


def test_the_progress_line_renders_unmeasured_rates_without_crashing():
    """Every rate is None on an empty denominator, and the per-arm progress line
    formats seven of them with `:.3f`. Applying a format spec to None raises
    TypeError *after* the whole serve loop and *before* summary.json is written —
    hours of live model calls discarded to print a progress line."""
    from governed_bi.eval.run_datalake import _fmt_rate

    s = _summarise_rows("curated", [])
    line = (
        f"EX={_fmt_rate(s['ex_lenient'])} "
        f"EX_gradeable={_fmt_rate(s['ex_gradeable'])} "
        f"routing_recall={_fmt_rate(s['routing_recall'])} "
        f"cond_EX|routed={_fmt_rate(s['cond_ex_given_routing'])} "
        f"decoy={_fmt_rate(s['decoy_touch_rate'], 4)} "
        f"refuse={_fmt_rate(s['refusal_rate'])} "
        f"crash={_fmt_rate(s['crash_rate'])}"
    )
    assert line.count("n/a") == 7


def test_fmt_rate_still_formats_a_real_number():
    from governed_bi.eval.run_datalake import _fmt_rate

    assert _fmt_rate(0.5) == "0.500"
    assert _fmt_rate(0.0) == "0.000", "a measured zero is not 'n/a'"
    assert _fmt_rate(0.01234, 4) == "0.0123"


def test_share_with_a_note_is_unmeasured_when_no_row_recorded_injection():
    s = _summarise_rows("curated", [_row("q1")])
    assert s["share_with_a_note"] is None
    assert s["mean_notes_injected"] is None


def test_share_with_a_note_is_measured_when_some_row_recorded_injection():
    rows = [_row("q1", n_notes_injected=2), _row("q2", n_notes_injected=0)]
    s = _summarise_rows("curated", rows)
    assert s["share_with_a_note"] == 0.5


# --------------------------------------------------------------------------- #
# routing metrics must be UNMEASURED, not 0.0 and not 1.0, when nothing routed
# --------------------------------------------------------------------------- #


def test_a_bypassed_pool_reports_no_routing_recall_rather_than_a_perfect_one():
    """A one-schema corpus (or an oracle rung handed its schema) has no routing
    decision to score, and both available lies are worse than an absence.

    Counting bypassed rows as misses reported 0.0 recall for a pool with nothing to
    route. Counting them as hits reports 1.0, which credits a router that never ran —
    and on an oracle rung, where the schema was *given*, that is the rung grading its
    own gift.
    """
    rows = [_row(f"q{i}", routing_bypassed=True) for i in range(1, 5)]
    s = _summarise_rows("oracle_schema", rows)
    assert s["n_routing_bypassed"] == 4
    assert s["n_routing_observed"] == 0
    assert s["routing_recall"] is None, "not measured is not the same as perfect"
    assert s["cond_ex_given_routing"] is None


def test_bypassed_rows_are_excluded_but_routed_rows_still_score():
    """A mixed set: only the rows that actually faced a routing decision count."""
    rows = [
        _row("q1"),  # routed, hit
        _row("q2", routed_hit=False),  # routed, miss
        _row("q3", routing_bypassed=True),  # no decision to score
        _row("q4", routing_bypassed=True),
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_routing_bypassed"] == 2
    assert s["n_routing_observed"] == 2
    assert s["routing_recall"] == 0.5


def test_a_bypassed_correct_answer_is_not_booked_as_an_unrouted_win():
    """``n_correct_unrouted`` exists to flag EX that routing did not enable. A
    bypassed row had no router, so it must not appear there — otherwise every correct
    answer on a single-schema arm reads as a routing anomaly."""
    rows = [
        _row("q1", correct=True),  # routed hit, correct
        _row("q2", routing_bypassed=True, correct=True),
        _row("q3", routing_bypassed=True, correct=True),
    ]
    s = _summarise_rows("oracle_schema", rows)
    assert s["n_correct_unrouted"] == 0
    assert s["cond_ex_given_routing"] == 1.0


def test_the_routing_decomposition_stays_checkable_when_bypassed_rows_are_mixed_in():
    """EX is over ALL rows; the routed terms are not. The third bucket must be named.

    Adversarial review found this: excluding bypassed rows from the routing terms
    silently broke `EX == routing_recall * cond_ex_given_routing` while
    `n_correct_unrouted` — documented as the escape hatch that flags exactly this —
    still read 0, because a bypassed correct answer lives in neither routed term.
    """
    rows = [
        _row("q1", correct=False, routed_hit=True),
        _row("q2", routing_bypassed=True, correct=True),
    ]
    s = _summarise_rows("mixed", rows)

    assert s["ex_lenient"] == 0.5
    assert s["routing_recall"] == 1.0
    assert s["cond_ex_given_routing"] == 0.0
    # The old escape hatch alone would have said "nothing to see here".
    assert s["n_correct_unrouted"] == 0
    # The named third bucket is what makes the shortfall legible.
    assert s["n_correct_bypassed"] == 1
    # ...and it is exactly the gap: EX counts 1 correct answer that neither routed
    # term can see.
    assert s["ex_lenient"] > s["routing_recall"] * s["cond_ex_given_routing"]


def test_correct_answers_partition_into_routed_unrouted_and_bypassed():
    """The invariant that makes the routing table readable as a decomposition."""
    rows = [
        _row("q1", correct=True, routed_hit=True),  # routed hit, correct
        _row("q2", correct=True, routed_hit=False),  # routed miss, correct anyway
        _row("q3", correct=True, routing_bypassed=True),  # no routing decision
        _row("q4", correct=False, routed_hit=True),
    ]
    s = _summarise_rows("arm", rows)
    n_correct = 3
    n_routed_correct = round(
        (s["cond_ex_given_routing"] or 0.0) * s["routing_recall"] * s["n_routing_observed"]
    )
    assert n_routed_correct + s["n_correct_unrouted"] + s["n_correct_bypassed"] == n_correct


# --------------------------------------------------------------------------- #
# Per-database diagnosis: `by_db` used to hold two numbers (ex_lenient, n), which
# says WHICH schemas drag a pooled run down and nothing about WHY — while the
# cluster sign test in `comparisons[].cluster` already named which databases
# regressed. The artifact was raising a question it could not answer.
# --------------------------------------------------------------------------- #


def test_each_database_carries_the_full_diagnostic_block():
    rows = [
        _row("q1", db_id="alpha", correct=True),
        _row("q2", db_id="alpha", routed_hit=False),
        _row("q3", db_id="beta", correct=True),
    ]
    s = _summarise_rows("curated", rows)
    for db in ("alpha", "beta"):
        block = s["by_db"][db]
        for key in (
            "ex_lenient", "ex_gradeable", "routing_recall", "cond_ex_given_routing",
            "n_routing_bypassed", "crash_rate", "refusal_rate", "by_outcome",
            "n_gold_unusable", "by_error_type",
        ):
            assert key in block, f"{db} is missing {key}"


def test_every_per_db_key_matches_the_same_figure_computed_alone():
    """It is the same function over a subset of the same rows, which is the point —
    a parallel set of per-db counters is how two numbers that should be equal drift.

    Diffs EVERY top-level key, not a hand-picked handful. The earlier version checked
    five rates whose equality holds for any pure function of rows, so it could not have
    failed — and it missed the one key where the recursive call genuinely did diverge
    (`treatment`, which was reporting a null `corpus_note_assets` at nested level).
    """
    alpha = [
        _row("q1", db_id="alpha", correct=True, n_notes_injected=2, context_hash="h1"),
        _row("q2", db_id="alpha", routed_hit=False, context_hash="h2"),
    ]
    beta = [_row("q3", db_id="beta", correct=True, context_hash="h3")]

    pooled = _summarise_rows("curated", alpha + beta, corpus_note_assets=99)
    standalone = _summarise_rows("curated", alpha, nested=True)

    inner = pooled["by_db"]["alpha"]
    assert set(inner) == set(standalone), (
        f"key sets differ: {set(inner) ^ set(standalone)}"
    )
    for key in sorted(standalone):
        assert inner[key] == standalone[key], key


def test_a_nested_block_omits_what_it_cannot_mean_rather_than_nulling_it():
    """`corpus_note_assets` is a whole-corpus count with no per-db decomposition. In
    this module `None` means "not verified", so reporting it as null would make
    inapplicable indistinguishable from unmeasured."""
    rows = [_row("q1", db_id="alpha", n_notes_injected=1, context_hash="h")]
    s = _summarise_rows("curated", rows, corpus_note_assets=42)
    assert s["treatment"]["corpus_note_assets"] == 42
    assert "corpus_note_assets" not in s["by_db"]["alpha"]["treatment"]


def test_rows_the_grouping_cannot_place_are_counted_not_silently_dropped():
    """`sum(by_db[*].n)` must either equal `n` or say why it does not. Dropping a row
    with no `db_id` is right; dropping it unaccounted for is the failure mode this
    harness exists to stop."""
    rows = [_row("q1", db_id="alpha"), _row("q2")]  # q2 carries no db_id
    s = _summarise_rows("curated", rows)
    assert s["n"] == 2
    assert sum(v["n"] for v in s["by_db"].values()) == 1
    assert s["n_rows_no_db_id"] == 1, "the gap between n and by_db is unexplained"


def test_a_conditional_rate_is_not_the_n_weighted_mean_of_its_parts():
    """The rollup trap `by_db` invites, pinned so the caution in the code stays true.

    A conditional rate weights by its own denominator, not by `n`. Reconstructing the
    pooled figure as an `n`-weighted mean of per-db figures gives the wrong answer.
    """
    rows = [
        # alpha: 1 routed row, correct -> cond_ex = 1.0 over n=2
        _row("q1", db_id="alpha", routed_hit=True, correct=True),
        _row("q2", db_id="alpha", routed_hit=False, correct=False),
        # beta: 3 routed rows, none correct -> cond_ex = 0.0 over n=3
        _row("q3", db_id="beta", routed_hit=True, correct=False),
        _row("q4", db_id="beta", routed_hit=True, correct=False),
        _row("q5", db_id="beta", routed_hit=True, correct=False),
    ]
    s = _summarise_rows("curated", rows)
    per_db = s["by_db"]
    assert per_db["alpha"]["cond_ex_given_routing"] == 1.0
    assert per_db["beta"]["cond_ex_given_routing"] == 0.0

    naive = (1.0 * per_db["alpha"]["n"] + 0.0 * per_db["beta"]["n"]) / s["n"]
    assert s["cond_ex_given_routing"] == 0.25
    assert naive != s["cond_ex_given_routing"], (
        "if these ever agree the test has stopped exercising the trap"
    )


def test_the_pooled_number_is_decomposable_into_its_databases():
    """One schema at 0.0 and one at 1.0 must not read as a uniform 0.5."""
    rows = [
        _row("q1", db_id="easy", correct=True),
        _row("q2", db_id="easy", correct=True),
        _row("q3", db_id="hard", correct=False),
        _row("q4", db_id="hard", correct=False),
    ]
    s = _summarise_rows("curated", rows)
    assert s["ex_lenient"] == 0.5
    assert s["by_db"]["easy"]["ex_lenient"] == 1.0
    assert s["by_db"]["hard"]["ex_lenient"] == 0.0


def test_nested_blocks_do_not_recurse_or_repeat_themselves():
    rows = [_row("q1", db_id="alpha"), _row("q2", db_id="beta")]
    s = _summarise_rows("curated", rows)
    inner = s["by_db"]["alpha"]
    assert inner["by_db"] == {}, "a single db's by_db is itself — infinite regress"
    assert inner["by_difficulty"] == {}
    assert inner["by_gold_rank"] == {}


def test_rows_with_no_db_id_do_not_become_a_phantom_database():
    rows = [_row("q1", db_id="alpha"), _row("q2")]  # q2 carries no db_id
    s = _summarise_rows("curated", rows)
    assert sorted(s["by_db"]) == ["alpha"]
    assert "None" not in s["by_db"]


# --------------------------------------------------------------------------- #
# The governance stamp, aggregated.
#
# Five fields are stamped on every row — `tier`, `semantic_assurance`,
# `safety_clearance`, `graded_delivery`, `coverage_best_effort` — and none reached the
# summary. So a run could report that EX moved and say nothing about whether the answers
# were *governed*, which is half of what the corpus is claimed to buy. Reliability is
# graded on `semantic_assurance`, so an arm that raises EX while shifting mass toward
# `unverified` has not improved the product in the way the claim means.
# --------------------------------------------------------------------------- #


def _approx(x, tol=1e-9):
    class _A:
        def __eq__(self, other):
            return abs(other - x) < tol
    return _A()


def _gov_row(qid, *, tier=None, assurance=None, safety=None, graded=None, best_effort=None):
    return {
        "question_id": qid, "db_id": "d", "arm": "curated", "split": "test",
        "correct": True, "generated_sql": "SELECT 1",
        "tier": tier, "semantic_assurance": assurance,
        "safety_clearance": safety, "graded_delivery": graded,
        "coverage_best_effort": best_effort,
    }


def test_the_summary_counts_tiers_and_assurance_levels():
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        _gov_row("q1", tier="governed", assurance="unflagged"),
        _gov_row("q2", tier="governed", assurance="unflagged"),
        _gov_row("q3", tier="fenced_raw", assurance="unverified"),
    ]
    s = _summarise_rows("curated", rows)
    assert s["by_tier"] == {"governed": 2, "fenced_raw": 1}
    assert s["by_semantic_assurance"] == {
        "unflagged": 2, "unverified": 1,
    }
    assert s["n_with_governance_stamp"] == 3


def test_a_boolean_that_was_never_stamped_is_not_counted_as_false():
    """The denominator is rows that recorded the field, not every row. Averaging over all
    rows would report a governance failure wherever the instrumentation simply did not
    run — the absent-versus-zero conflation this module exists to prevent."""
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        _gov_row("q1", safety=True, graded=False),
        _gov_row("q2", safety=True, graded=True),
        _gov_row("q3"),  # nothing stamped at all
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_safety_clearance_observed"] == 2
    assert s["safety_clearance_rate"] == 1.0, "2 of 2 observed, not 2 of 3 rows"
    assert s["n_graded_delivery_observed"] == 2
    assert s["graded_delivery_rate"] == 0.5


def test_governance_rates_are_unmeasured_rather_than_perfect_when_absent():
    from governed_bi.eval.run_datalake import _summarise_rows

    s = _summarise_rows("curated", [_gov_row("q1"), _gov_row("q2")])
    assert s["safety_clearance_rate"] is None
    assert s["graded_delivery_rate"] is None
    assert s["coverage_best_effort_rate"] is None
    assert s["by_tier"] == {}
    assert s["n_with_governance_stamp"] == 0


def test_a_false_boolean_is_a_measurement_not_an_absence():
    """`False` must lower the rate; only `None` may leave it unmeasured."""
    from governed_bi.eval.run_datalake import _summarise_rows

    s = _summarise_rows(
        "curated",
        [_gov_row("q1", safety=False), _gov_row("q2", safety=False)],
    )
    assert s["n_safety_clearance_observed"] == 2
    assert s["safety_clearance_rate"] == 0.0


def test_a_ladder_step_reports_the_governance_it_traded_for_ex():
    """A rung that raises EX by delivering more answers below the assurance bar has traded
    governance for score. The ladder supports a claim about *governed* answers, so that
    trade must be as visible as the EX it bought."""
    from governed_bi.eval.run_datalake import ladder_deltas

    def arm(ex, graded, safety):
        return {
            "arm": "x", "n": 100, "n_correct": int(ex * 100),
            "question_ids": [f"q{i}" for i in range(100)],
            "ex_lenient": ex, "ex_gradeable": ex,
            "routing_recall": None, "cond_ex_given_routing": None,
            "graded_delivery_rate": graded, "safety_clearance_rate": safety,
            "coverage_best_effort_rate": None,
            "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 100},
        }

    d = ladder_deltas({
        "seeded": arm(0.30, 0.10, 1.0),
        "curated": arm(0.40, 0.35, 0.9),   # +10 EX, but far more graded delivery
    })
    assert d["curated_minus_seeded_ex"] == _approx(0.10)
    assert d["curated_minus_seeded_graded_delivery_rate"] == _approx(0.25)
    assert d["curated_minus_seeded_safety_clearance_rate"] == _approx(-0.10)


def test_a_summary_missing_a_governance_metric_does_not_kill_every_delta():
    """`ladder_deltas` is read over archived summaries too, and a summary predating a
    metric legitimately lacks the key. Indexing turned that into a KeyError that took out
    the deltas the summary *did* have."""
    from governed_bi.eval.run_datalake import ladder_deltas

    def old_arm(ex):
        return {
            "arm": "x", "n": 100, "n_correct": int(ex * 100),
            "question_ids": [f"q{i}" for i in range(100)],
            "ex_lenient": ex, "ex_gradeable": ex,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": 1.0, "n_rows_priced": 100},
        }

    d = ladder_deltas({"seeded": old_arm(0.3), "curated": old_arm(0.4)})
    assert d["curated_minus_seeded_ex"] == _approx(0.10)
    assert d["curated_minus_seeded_graded_delivery_rate"] is None


def test_governance_rates_describe_delivered_answers_not_refusals():
    """A refusal stamps `safety_clearance=False` outright, and `arms.py` coerces the other
    two through `bool(...)` so absent becomes `False`. Averaged over all rows the readings
    came out backwards: an arm that refused 8 of 10 reported the *best* graded-delivery
    rate and the *worst* safety-clearance rate, because refusing is not delivering and not
    clearing — the opposite of what these rates are for.

    Refusal behaviour is `refusal_rate`'s job. These describe the answers handed back.
    """
    from governed_bi.eval.run_datalake import _summarise_rows

    def r(qid, *, sql, safety, graded):
        return {
            "question_id": qid, "db_id": "d", "arm": "x", "split": "test",
            "correct": bool(sql), "generated_sql": sql,
            "tier": "governed" if sql else "refused",
            "safety_clearance": safety, "graded_delivery": graded,
            "coverage_best_effort": bool(sql),
        }

    answering = [r(f"q{i}", sql="SELECT 1", safety=i < 8, graded=i < 2) for i in range(10)]
    refusing = [r(f"q{i}", sql="SELECT 1", safety=True, graded=i < 1) for i in range(2)] + [
        r(f"x{i}", sql=None, safety=False, graded=False) for i in range(8)
    ]

    a, b = _summarise_rows("x", answering), _summarise_rows("x", refusing)
    assert b["refusal_rate"] == 0.8

    # Every rate, not just the first. Reverting `graded_delivery_rate` or
    # `coverage_best_effort_rate` to all-rows left the suite green — including the
    # commit's own headline example, "refused 8 of 10 reported the best graded-delivery
    # rate", which was the one case with no assertion behind it.
    for rate, denom, expected_b, expected_over_rows in (
        ("safety_clearance_rate", "n_safety_clearance_observed", 1.0, 0.2),
        ("graded_delivery_rate", "n_graded_delivery_observed", 0.5, 0.1),
        ("coverage_best_effort_rate", "n_coverage_best_effort_observed", 1.0, 0.2),
    ):
        assert b[denom] == 2, (
            f"{denom} must count the delivered answers, so a tiny sample is visible"
        )
        assert b[rate] == expected_b, f"{rate} is not conditioned on delivery"
        assert b[rate] != expected_over_rows, (
            f"{rate} matches the all-rows figure, so refusals are still in its denominator"
        )
        assert a[denom] == 10

    # And the refusing arm must not look better on safety merely for refusing.
    assert b["safety_clearance_rate"] >= a["safety_clearance_rate"]


def test_every_governance_rate_reports_its_denominator():
    """A rate without its denominator is not a measurement — `0.0` cannot be told from
    0-of-1. `coverage_best_effort_rate` shipped without one, in the block whose whole
    point is that distinction."""
    from governed_bi.eval.run_datalake import _summarise_rows

    s = _summarise_rows(
        "x",
        [
            {
                "question_id": "q1", "db_id": "d", "arm": "x", "split": "test",
                "correct": True, "generated_sql": "SELECT 1",
                "safety_clearance": True, "graded_delivery": False,
                "coverage_best_effort": True,
            }
        ],
    )
    for rate in ("safety_clearance", "graded_delivery", "coverage_best_effort"):
        assert s[f"{rate}_rate"] is not None, rate
        assert s[f"n_{rate}_observed"] == 1, f"n_{rate}_observed missing or wrong"


def test_an_enum_valued_tier_does_not_split_across_two_keys():
    """Every producer stamps `.value` today, but `str(ReliabilityTier.governed)` is
    `'ReliabilityTier.governed'` while the same value round-trips through JSON as
    `'governed'`. `_summarise_rows` aggregates replayed rows alongside fresh ones, so an
    enum-stamping producer would split one tier in two on a resume, silently."""
    from governed_bi.analyst.answer import ReliabilityTier
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        {"question_id": "q1", "db_id": "d", "arm": "x", "split": "test", "correct": True,
         "generated_sql": "SELECT 1", "tier": ReliabilityTier.governed},   # fresh
        {"question_id": "q2", "db_id": "d", "arm": "x", "split": "test", "correct": True,
         "generated_sql": "SELECT 1", "tier": "governed"},                  # replayed
    ]
    assert _summarise_rows("x", rows)["by_tier"] == {"governed": 2}


def test_an_enum_valued_assurance_does_not_split_across_two_keys():
    """Same hazard, same fix, and it was only pinned for `by_tier`."""
    from governed_bi.analyst.answer import SemanticAssurance
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        {"question_id": "q1", "db_id": "d", "arm": "x", "split": "test", "correct": True,
         "generated_sql": "SELECT 1", "semantic_assurance": SemanticAssurance.unflagged},
        {"question_id": "q2", "db_id": "d", "arm": "x", "split": "test", "correct": True,
         "generated_sql": "SELECT 1", "semantic_assurance": "unflagged"},
    ]
    assert _summarise_rows("x", rows)["by_semantic_assurance"] == {"unflagged": 2}


def test_the_two_delivery_rates_are_currently_complements():
    """Only two `Answer` constructors carry SQL: `assemble` (clears safety, not a graded
    delivery) and `graded_delivery` (the reverse). So over delivered rows
    `safety_clearance_rate == 1 - graded_delivery_rate`, and the two ladder deltas are
    exact negatives — while the runbook presents them as separate governance signals.

    Both are kept, because a future path could deliver an answer that clears safety *and*
    is graded. **If this test fails, that has happened**: the metrics have become
    independent and the runbook's note about the identity needs removing. It is a signal,
    not a regression.

    The shapes are built by calling the real constructors and mapping them the way
    `arms.agent_solver` does. Hand-typing `safety XOR graded` fixtures made this a
    tautology — the sum is 1.0 for any partitioned fixture under any implementation, so
    flipping `safety_clearance` inside `graded_delivery()` left it green.
    """
    from governed_bi.analyst.answer import (
        ResultTable,
        UncertaintySignals,
        assemble,
        graded_delivery,
    )
    from governed_bi.eval.run_datalake import _summarise_rows

    table = ResultTable(columns=["v"], rows=[(1,)], row_count=1)

    def as_row(qid, answer):
        """The mapping `arms.agent_solver` applies when building a scored row."""
        prov = answer.provenance or {}
        return {
            "question_id": qid, "db_id": "d", "arm": "x", "split": "test",
            "correct": True, "generated_sql": answer.sql,
            "safety_clearance": answer.safety_clearance,
            "graded_delivery": bool(prov.get("graded_delivery")),
        }

    clean = assemble(
        text="t", sql="SELECT 1", signals=UncertaintySignals(), provenance={}, result=table
    )
    graded = graded_delivery(sql="SELECT 1", provenance={}, result=table, text="t")
    assert clean.sql and graded.sql, "both constructors must carry SQL for this to apply"

    rows = [as_row("q1", clean), as_row("q2", graded), as_row("q3", graded)]
    s = _summarise_rows("x", rows)
    assert s["safety_clearance_rate"] + s["graded_delivery_rate"] == 1.0, (
        "the two delivery rates are no longer complements — a serve path now produces an "
        "answer that clears safety AND is a graded delivery, or neither. That is a real "
        "change, not a bug: the metrics have separated and the runbook's note about the "
        "identity should be removed."
    )


def test_a_graded_delivery_never_claims_guardrail_clearance():
    """The audit invariant the complementarity rests on, and it was pinned nowhere. A §6
    graded delivery hands back SQL that did *not* clear the full guardrail path, so
    claiming clearance would misreport it in the run log and the API response alike.
    Flipping this flag inside `graded_delivery()` left the whole suite green."""
    from governed_bi.analyst.answer import ReliabilityTier, ResultTable, graded_delivery

    ans = graded_delivery(
        sql="SELECT 1",
        provenance={},
        result=ResultTable(columns=["v"], rows=[(1,)], row_count=1),
        text="t",
    )
    assert ans.sql is not None
    assert ans.safety_clearance is False
    assert ans.tier is ReliabilityTier.fenced_raw


def test_the_five_correct_buckets_are_disjoint_and_exhaustive():
    """`n_correct_unaccounted` must be a real check, not an algebraic identity.

    It was computed as `n_correct − (five buckets)` while one of those five —
    `n_correct_routing_crashed` — was itself `n_correct − (the other four)`. That makes
    the check identically zero whatever the rows: a sixth exclusion added to the
    routing population produced `n_correct_routing_crashed = 2` beside
    `n_routing_crashed = 0` with the check still reading 0, which is the same
    impossible pair the block was rewritten to remove, one bucket over.

    Every bucket is counted directly now, so the sum is a claim about the rows rather
    than a restatement of its own definition. Fuzzed, because the interesting cases are
    combinations (correct + crashed + bypassed, correct + unrecorded + crashed) that
    are tedious to enumerate by hand and easy to get wrong one at a time.
    """
    import random

    rng = random.Random(20260727)
    for trial in range(500):
        rows = []
        for i in range(rng.randint(1, 12)):
            crashed = rng.random() < 0.3
            routed = rng.choice([True, False, None])
            rows.append(
                _row(
                    f"q{i}",
                    correct=rng.random() < 0.5,
                    routed_hit=routed,
                    routed_schemas=None if routed is None else [],
                    routing_bypassed=rng.random() < 0.25,
                    generated_sql=None if crashed else "SELECT 1",
                    error="RuntimeError: boom" if crashed else None,
                )
            )
        s = _summarise_rows("curated", rows)
        buckets = (
            s["n_correct_routed"]
            + s["n_correct_unrouted"]
            + s["n_correct_bypassed"]
            + s["n_correct_routing_unrecorded"]
            + s["n_correct_routing_crashed"]
        )
        assert s["n_correct_unaccounted"] == 0, (trial, s["n_correct"], buckets)
        assert buckets == s["n_correct"], (trial, buckets, s["n_correct"])
        # Each bucket is bounded by the population it names — the pair that used to
        # be impossible.
        assert s["n_correct_bypassed"] <= s["n_routing_bypassed"]
        assert s["n_correct_routing_crashed"] <= s["n_routing_crashed"]
        assert s["n_correct_routing_unrecorded"] <= s["n_routing_unrecorded"]
        assert s["n_correct_routed"] + s["n_correct_unrouted"] <= s["n_routing_observed"]


def test_every_routing_family_metric_shares_the_uncrashed_population():
    """`routing_recall`, `schema_pick_accuracy` and `by_gold_rank` are one family.

    The crash carve-out was applied to the recall terms and not to the other two, so a
    crash that recorded a pick counted in `schema_pick_accuracy` while the same crash
    recording a route was struck from `routing_recall` — the runbook names all three
    together, and under a rate-limit storm the split is systematic, not incidental.
    """
    rows = [
        _row("clean", routed_hit=True, schema_pick="db_a", pick_hit=True,
             shortlisted_schemas=["db_a"], gold_schema_rank=1),
        # Crashed, but got far enough to record a pick and a shortlist.
        _row("crashed", generated_sql=None, error="RateLimitError: 429",
             routed_hit=True, schema_pick="other", pick_hit=False,
             shortlisted_schemas=["other"], gold_schema_rank=None),
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_crashed"] == 1
    assert s["n_routing_observed"] == 1
    assert s["routing_recall"] == 1.0
    # The crash must not drag the pick accuracy down either.
    assert s["schema_pick_accuracy"] == 1.0, (
        "a crashed turn's pick is not a picker error"
    )
    ranks = s["by_gold_rank"]
    assert sum(b["n"] for b in ranks.values()) == 1, ranks
    assert "1" in ranks
