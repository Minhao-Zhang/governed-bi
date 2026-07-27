"""Statistical machinery: the parts that decide whether a delta may be believed.

Three defects motivate these tests, all found by reading the code rather than by a
failing run:

* zero observed discordance was treated as zero noise, which made the minimum
  detectable effect 0 and every delta "resolvable" — the module's purpose inverted,
  and worst exactly on the small runs where zero disagreements is unremarkable;
* six pairwise tests were each reported at a nominal 0.05, a ~26% family-wise false
  positive rate;
* questions were treated as independent when they are nested in ~69 databases, so a
  change that suits a handful of schemas reads as a hundred independent wins.
"""

from __future__ import annotations

import math

import pytest

from governed_bi.eval.power import (
    cluster_sign_test,
    holm_adjust,
    mcnemar,
    measure_floor,
    minimum_detectable_effect,
)


# --------------------------------------------------------------------------- #
# Minimum detectable effect
# --------------------------------------------------------------------------- #


def test_zero_observed_discordance_is_not_zero_noise():
    """The inversion: a small replicate that happened to agree everywhere used to
    report that it could resolve any effect, including no effect at all."""
    d = minimum_detectable_effect(4, 0.0)
    assert d.from_zero_discordance is True
    assert d.questions > 0
    assert d.resolves(1) is False
    assert d.resolves(0) is False
    assert "rule of three" in d.verdict(1) or "below resolution" in d.verdict(1)


def test_the_zero_discordance_bound_is_the_rule_of_three():
    """0 events in n trials bounds the rate at ~3/n, so the floor is ~3 pairs."""
    d = minimum_detectable_effect(500, 0.0)
    z_alpha = 1.959963984540054  # two-sided 0.05
    z_beta = 0.8416212335729143  # 80% power
    assert math.isclose(d.questions, (z_alpha + z_beta) * math.sqrt(3.0), rel_tol=1e-9)
    assert d.from_zero_discordance is True


def test_the_bound_never_exceeds_the_pairs_available():
    """With only 2 paired questions, "3 discordant" is impossible."""
    d = minimum_detectable_effect(2, 0.0)
    z = 1.959963984540054 + 0.8416212335729143
    assert math.isclose(d.questions, z * math.sqrt(2.0), rel_tol=1e-9)


def test_no_paired_questions_means_resolution_is_unknown_not_perfect():
    d = minimum_detectable_effect(0, 0.0)
    assert d.measured is False
    assert d.resolves(999) is False
    assert "unknown" in d.verdict(999)


def test_a_measured_discordance_is_unchanged_by_the_fix():
    """The well-powered path must be untouched: MDE = (z_a + z_b) * sqrt(d)."""
    d = minimum_detectable_effect(2000, 0.10)  # 200 discordant
    z = 1.959963984540054 + 0.8416212335729143
    assert math.isclose(d.questions, z * math.sqrt(200.0), rel_tol=1e-9)
    assert d.from_zero_discordance is False
    assert d.measured is True


def test_a_perfect_replicate_on_a_small_run_cannot_bless_a_one_question_delta():
    """End to end, the case that motivated the fix."""
    a = {f"q{i}": i % 2 == 0 for i in range(4)}
    floor = measure_floor(a, a)
    assert floor.n_discordant == 0
    d = minimum_detectable_effect(floor.n_pairs, floor.discordance_rate or 0.0)
    assert d.resolves(1) is False


# --------------------------------------------------------------------------- #
# Holm-Bonferroni
# --------------------------------------------------------------------------- #


def test_holm_matches_a_hand_computed_family():
    """n=3, sorted p = .01, .04, .05 -> 3*.01=.03, 2*.04=.08, 1*.05=.05 -> monotone."""
    out = holm_adjust([0.04, 0.01, 0.05])
    assert math.isclose(out[1], 0.03, rel_tol=1e-9)  # smallest
    assert math.isclose(out[0], 0.08, rel_tol=1e-9)
    # Step-down enforces monotonicity: the last cannot fall below the previous.
    assert math.isclose(out[2], 0.08, rel_tol=1e-9)


def test_holm_is_order_preserving_and_clipped():
    ps = [0.9, 0.5, 0.02]
    out = holm_adjust(ps)
    assert len(out) == 3
    assert all(0.0 <= p <= 1.0 for p in out)
    assert math.isclose(out[2], 0.06, rel_tol=1e-9)
    assert out[0] == 1.0  # 1 * 0.9 clipped by the running max of 3*0.5


def test_holm_on_a_single_test_changes_nothing():
    assert holm_adjust([0.03]) == [0.03]
    assert holm_adjust([]) == []


def test_holm_would_have_demoted_a_marginal_win_in_a_six_test_family():
    """Four arms is six pairs. A nominal 0.04 does not survive the family."""
    family = [0.04, 0.30, 0.55, 0.61, 0.80, 0.95]
    out = holm_adjust(family)
    assert family[0] < 0.05
    assert out[0] > 0.05, "the whole point: this is not a finding at family level"


# --------------------------------------------------------------------------- #
# Cluster (database-level) sign test
# --------------------------------------------------------------------------- #


def _spread(n_dbs: int, per_db: int, winner: str):
    """Build two arms over `n_dbs` databases with `per_db` questions each."""
    a, b, db = {}, {}, {}
    for d in range(n_dbs):
        for q in range(per_db):
            qid = f"d{d}q{q}"
            db[qid] = f"db{d}"
            a[qid] = False
            b[qid] = winner == "b"
    return a, b, db


def test_one_easy_schema_cannot_manufacture_a_cluster_level_win():
    """The pseudoreplication the question-level test is blind to.

    Arm b wins 100 questions — but all of them in a single database. McNemar sees
    100 independent wins; the cluster test sees one database.
    """
    a, b, db = {}, {}, {}
    for q in range(100):
        qid = f"big{q}"
        db[qid], a[qid], b[qid] = "db_big", False, True
    for d in range(1, 6):
        qid = f"small{d}"
        db[qid], a[qid], b[qid] = f"db{d}", True, True

    question_level = mcnemar("a", a, "b", b)
    assert question_level.p_value < 1e-9, "100 vs 0 discordant looks overwhelming"

    cluster = cluster_sign_test(a, b, db)
    assert cluster["n_dbs_better"] == 1
    assert cluster["n_dbs_worse"] == 0
    assert cluster["p_value"] == 1.0, (
        "one database improving is one observation, and one observation is not "
        "evidence at any p"
    )


def test_a_broad_win_survives_the_cluster_test():
    a, b, db = _spread(n_dbs=12, per_db=5, winner="b")
    cluster = cluster_sign_test(a, b, db)
    assert cluster["n_dbs_better"] == 12
    assert cluster["n_dbs_worse"] == 0
    assert cluster["p_value"] < 0.05


def test_ties_are_excluded_from_the_cluster_denominator():
    """A database where neither arm moved carries no sign."""
    a = {"x1": True, "x2": False, "y1": True}
    b = {"x1": True, "x2": True, "y1": True}
    db = {"x1": "dbx", "x2": "dbx", "y1": "dby"}
    cluster = cluster_sign_test(a, b, db)
    assert cluster["n_dbs"] == 2
    assert cluster["n_dbs_better"] == 1
    assert cluster["n_dbs_tied"] == 1
    assert cluster["n_dbs_worse"] == 0


def test_no_database_movement_reports_nothing_rather_than_significance():
    a = {"q1": True, "q2": False}
    b = {"q1": True, "q2": False}
    cluster = cluster_sign_test(a, b, {"q1": "d1", "q2": "d2"})
    assert cluster["p_value"] == 1.0
    assert "no database-level difference" in cluster["reading"]


def test_questions_with_no_database_are_dropped_not_pooled_into_one():
    """An unlabelled question must not become its own phantom cluster."""
    a = {"q1": False, "q2": False}
    b = {"q1": True, "q2": True}
    cluster = cluster_sign_test(a, b, {"q1": "db1"})  # q2 unlabelled
    assert cluster["n_dbs"] == 1


def test_an_unmeasured_effect_stays_valid_json():
    """`summary.json` is a durable artifact. A float infinity serialises as the bare
    token `Infinity`, which is not valid JSON — one unmeasured field would corrupt
    the whole file for any strict reader (jq, another language's parser)."""
    import json

    def _strict(text: str):
        return json.loads(
            text,
            parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)),
        )

    for n_pairs, rate in ((0, 0.0), (4, 0.0), (2030, 135 / 2030)):
        payload = json.dumps(minimum_detectable_effect(n_pairs, rate).to_dict())
        _strict(payload)  # raises on Infinity / NaN

    unmeasured = minimum_detectable_effect(0, 0.0)
    assert unmeasured.to_dict()["mde_questions"] is None
    assert unmeasured.to_dict()["mde_rate"] is None


def test_the_detectable_rate_is_never_above_one():
    """A "minimum detectable *rate*" over 1.0 says the smallest effect the run could
    see is larger than every question it asked, which has no reading. Reachable on a
    tiny replicate against the rule-of-three floor."""
    for n_pairs in (1, 2, 3):
        d = minimum_detectable_effect(n_pairs, 0.0)
        assert 0.0 <= d.rate <= 1.0, (n_pairs, d.rate)
    assert minimum_detectable_effect(1, 0.0).rate == 1.0


# --------------------------------------------------------------------------- #
# The exact test against hand-computed values, not just against its extremes.
#
# The existing checks assert p < 1e-10 for lopsided discordance and p ~= 1.0 for
# symmetric — both of which a formula off by a factor of two, or by one term in the
# `comb` sum, would still satisfy. These are the mid-range values where such a bug
# is visible, computed by hand as exact rationals:
#
#   p = min(1, 2 * sum(C(n, i) for i in 0..min(b,c)) / 2**n)
#
# e.g. b=3, c=7 -> n=10, k=3: 2*(1+10+45+120)/1024 = 352/1024 = 0.34375
# --------------------------------------------------------------------------- #

_EXACT_MCNEMAR = [
    # (a_only, b_only, p) — p as an exact decimal, verifiable with a calculator.
    (0, 0, 1.0),            # no discordance carries no information
    (1, 0, 1.0),            # 2 * 1/2
    (5, 0, 0.0625),         # 2 * 1/32
    (0, 5, 0.0625),         # direction must not change the two-sided p
    (3, 1, 0.625),          # 2 * (1+4)/16
    (3, 7, 0.34375),        # 2 * (1+10+45+120)/1024
    (10, 2, 0.03857421875), # 2 * (1+12+66)/4096
    (12, 4, 0.076812744140625),
    (8, 8, 1.0),            # capped at 1, never 1.0000000000000002
    (20, 7, 0.019157290458679199),
]


@pytest.mark.parametrize("a_only,b_only,expected", _EXACT_MCNEMAR)
def test_mcnemar_matches_a_hand_computed_exact_binomial(a_only, b_only, expected):
    a = {f"x{i}": True for i in range(a_only)} | {f"y{i}": False for i in range(b_only)}
    b = {f"x{i}": False for i in range(a_only)} | {f"y{i}": True for i in range(b_only)}
    # One concordant pair, to prove it is excluded from the discordance count rather
    # than quietly widening n.
    a["same"] = b["same"] = True

    result = mcnemar("a", a, "b", b)
    assert result.n_a_only == a_only
    assert result.n_b_only == b_only
    assert result.n_shared == a_only + b_only + 1
    assert result.p_value == pytest.approx(expected, abs=1e-15)


def test_mcnemar_p_value_never_exceeds_one():
    """The two-sided doubling can overshoot at small n — b=c=1 gives 2 * 1.0 — and a
    p-value above 1 would sail through every `p < 0.05` check downstream while
    looking obviously wrong to a reader."""
    for n in range(1, 12):
        a = {f"x{i}": i % 2 == 0 for i in range(2 * n)}
        b = {q: not v for q, v in a.items()}
        assert 0.0 <= mcnemar("a", a, "b", b).p_value <= 1.0


def test_mcnemar_is_symmetric_under_swapping_the_arms():
    """A two-sided test cannot depend on argument order. If it did, `a_vs_b` and
    `b_vs_a` would disagree and the ladder's direction would decide significance."""
    a = {f"q{i}": i < 9 for i in range(30)}
    b = {f"q{i}": i >= 4 for i in range(30)}
    assert mcnemar("a", a, "b", b).p_value == mcnemar("b", b, "a", a).p_value


# --------------------------------------------------------------------------- #
# The correction family is the distinct hypotheses, not every pair on disk.
#
# `--replicate` serves one arm a second time to measure the run's noise floor, and
# the runbook requires it to quote a delta at all. Every pair the replicate forms
# duplicates the pair its source arm already forms, so counting them as hypotheses
# makes Holm's multiplier too large in exactly the runs that were set up correctly.
# --------------------------------------------------------------------------- #


def test_the_holm_family_excludes_every_pair_the_replicate_forms():
    from governed_bi.eval.run_datalake import _compare_arms

    qids = [f"q{i}" for i in range(24)]

    def rows(right):
        return [
            {"question_id": q, "db_id": "db_a", "correct": q in right, "arm": "x"}
            for q in qids
        ]

    right = {
        "baseline": set(),
        "seeded": {"q0", "q1"},
        "curated": {"q0", "q1", "q2"},
        "curated_sme": {"q0", "q1", "q2", "q3"},
        "curated__replicate": {"q0", "q1", "q2"},
    }
    comparisons, _div = _compare_arms(
        {a: rows(r) for a, r in right.items()}, replicate_of="curated"
    )

    # 5 arms -> 10 pairs; 4 of them involve the replicate.
    assert len(comparisons) == 10
    family = [c for c in comparisons if not c["diagnostic_pair"]]
    assert len(family) == 6, (
        "the family must be the four fair arms' six pairs, not the ten on disk"
    )
    assert all(c["family_size"] == 6 for c in family)

    replicate_pairs = [
        c for c in comparisons if "curated__replicate" in (c["arm_a"], c["arm_b"])
    ]
    assert len(replicate_pairs) == 4
    for c in replicate_pairs:
        assert c["diagnostic_pair"] is True, f"{c['arm_a']} vs {c['arm_b']}"
        # Still reported, so the replicate-vs-source pair remains readable as the
        # noise floor — just carrying no adjusted p-value, explicitly rather than
        # by omission.
        assert c["p_value_holm"] is None
        assert c["significant_holm"] is None
        assert "p_value" in c


def test_a_run_without_a_replicate_has_the_same_family_as_one_with():
    """Adding the replicate must not change the correction applied to the fair
    comparisons. If it does, the run that was measured properly is penalised for it,
    and the two runs' p-values are not on the same scale."""
    from governed_bi.eval.run_datalake import _compare_arms

    qids = [f"q{i}" for i in range(24)]

    def rows(right):
        return [
            {"question_id": q, "db_id": "db_a", "correct": q in right, "arm": "x"}
            for q in qids
        ]

    fair = {
        "baseline": set(),
        "seeded": {"q0", "q1"},
        "curated": {"q0", "q1", "q2"},
        "curated_sme": {"q0", "q1", "q2", "q3"},
    }
    without, _ = _compare_arms({a: rows(r) for a, r in fair.items()})
    with_rep, _ = _compare_arms(
        {a: rows(r) for a, r in {**fair, "curated__replicate": fair["curated"]}.items()},
        replicate_of="curated",
    )

    def fair_pairs(comparisons):
        return {
            (c["arm_a"], c["arm_b"]): (c["family_size"], c["p_value"], c["p_value_holm"])
            for c in comparisons
            if not c["diagnostic_pair"]
        }

    assert fair_pairs(without) == fair_pairs(with_rep), (
        "the replicate changed the fair comparisons' correction"
    )


def _pool(arm_rows):
    return {
        arm: [
            {"question_id": q, "db_id": "db_a", "correct": q in right, "arm": arm}
            for q in qids
        ]
        for arm, (qids, right) in arm_rows.items()
    }


def test_the_family_excludes_a_pair_that_shared_no_questions():
    """`p_value = 1.0` from an empty discordance count is the arithmetic of having
    nothing to compare, not a measurement. Counting it tightens every other pair on
    behalf of a test that never ran — and `eval.analysis` already excluded it, so
    leaving it here made the two artifacts correct one run across different family
    sizes."""
    from governed_bi.eval.run_datalake import _compare_arms

    A = [f"q{i}" for i in range(12)]
    Z = [f"z{i}" for i in range(12)]
    comparisons, _ = _compare_arms(
        _pool({
            "baseline": (A, set()),
            "seeded": (A, {"q0", "q1"}),
            "curated": (A, {"q0", "q1", "q2"}),
            "curated_sme": (Z, {"z0"}),  # disjoint ids: shares nothing with the rest
        })
    )

    zero = [c for c in comparisons if c["n_shared"] == 0]
    assert len(zero) == 3, "the fixture should produce three zero-overlap pairs"
    for c in zero:
        assert c["p_value"] == 1.0
        assert c["p_value_holm"] is None, f"{c['arm_a']} vs {c['arm_b']}"
        assert c["significant_holm"] is None
    family = [c for c in comparisons if c["p_value_holm"] is not None]
    assert len(family) == 3, "only the three pairs that shared questions were tested"
    assert all(c["family_size"] == 3 for c in comparisons)


def test_the_replicate_exclusion_does_not_depend_on_replicate_of_being_passed():
    """Marking the replicate diagnostic by reconstructing `f"{replicate_of}__replicate"`
    meant the exclusion held only while the caller passed `replicate_of` consistently
    with the rows. Called without it, the replicate pairs re-entered the family and the
    count went straight back to ten. `_compare_arms` is a plain importable function and
    nothing enforced that the two arguments agreed."""
    from governed_bi.eval.run_datalake import _compare_arms

    Q = [f"q{i}" for i in range(16)]
    pool = _pool({
        "baseline": (Q, set()),
        "seeded": (Q, {"q0", "q1"}),
        "curated": (Q, {"q0", "q1", "q2"}),
        "curated_sme": (Q, {"q0", "q1", "q2", "q3"}),
        "curated__replicate": (Q, {"q0", "q1", "q2"}),
    })

    for replicate_of in ("curated", None):
        comparisons, _ = _compare_arms(pool, replicate_of=replicate_of)
        family = [c for c in comparisons if c["p_value_holm"] is not None]
        assert len(comparisons) == 10
        assert len(family) == 6, (
            f"replicate_of={replicate_of!r} gave a family of {len(family)}, not 6"
        )
        assert not any(
            "replicate" in c["arm_a"] + c["arm_b"] for c in family
        ), f"a replicate pair entered the family with replicate_of={replicate_of!r}"


@pytest.mark.parametrize("replicate_of", ["baseline", "seeded", "curated_sme"])
def test_the_replicate_exclusion_is_not_special_cased_to_curated(replicate_of):
    from governed_bi.eval.run_datalake import _compare_arms

    Q = [f"q{i}" for i in range(16)]
    fair = {
        "baseline": (Q, set()),
        "seeded": (Q, {"q0", "q1"}),
        "curated": (Q, {"q0", "q1", "q2"}),
        "curated_sme": (Q, {"q0", "q1", "q2", "q3"}),
    }
    # The replicate serves the same corpus as its source, so it scores the same.
    arms = {**fair, f"{replicate_of}__replicate": fair[replicate_of]}
    comparisons, _ = _compare_arms(_pool(arms), replicate_of=replicate_of)
    family = [c for c in comparisons if c["p_value_holm"] is not None]
    assert len(family) == 6, replicate_of
    assert not any("replicate" in c["arm_a"] + c["arm_b"] for c in family)


def test_both_reports_agree_on_the_family_for_the_same_rows(tmp_path):
    """The invariant this whole thread of work keeps failing at: `summary.json` and
    `analysis.json` compute the same facts from the same rows in two places, and every
    fix so far landed on one side first. This compares them directly.
    """
    import json

    from governed_bi.eval.analysis import analyse_run
    from governed_bi.eval.arms import ARM_ORDER
    from governed_bi.eval.run_datalake import _compare_arms

    Q = [f"q{i}" for i in range(16)]
    right = {
        "baseline": set(),
        "seeded": {"q0", "q1"},
        "curated": {"q0", "q1", "q2"},
        "curated_sme": {"q0", "q1", "q2", "q3"},
        "curated__replicate": {"q0", "q1", "q2"},
        "oracle_sql": set(Q),
    }
    pool = _pool({a: (Q, r) for a, r in right.items()})

    # analysis.py additionally needs the gold split and richer rows.
    bird = tmp_path / "bird"
    (bird / "eval_dataset").mkdir(parents=True)
    (bird / "eval_dataset" / "test_final.jsonl").write_text(
        "".join(
            json.dumps({"question_id": q, "sql_rename": f"SELECT * FROM t_{q}"}) + "\n"
            for q in Q
        ),
        encoding="utf-8",
    )
    for arm, rows in pool.items():
        enriched = [
            {**r, "split": "test", "routed_hit": True, "gold_frozen": False,
             "gold_schema_rank": 1, "pick_hit": True,
             "retrieved_tables": [f"db_a.t_{r['question_id']}"],
             "generated_sql": (
                 f"SELECT * FROM t_{r['question_id']}" if r["correct"] else "SELECT 1"
             )}
            for r in rows
        ]
        (tmp_path / f"generations.{arm}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in enriched), encoding="utf-8"
        )

    comparisons, _ = _compare_arms(pool, replicate_of="curated")
    analysis_pairs = analyse_run(tmp_path, bird_dir=bird)["mcnemar"]

    def norm(a, b):
        if a in ARM_ORDER and b in ARM_ORDER:
            a, b = sorted((a, b), key=ARM_ORDER.index)
        return f"{a}_vs_{b}"

    summary_side = {
        norm(c["arm_a"], c["arm_b"]): (
            c["single_variable"],
            tuple(c.get("bundles") or ()),
            c["p_value_holm"] is not None,
        )
        for c in comparisons
    }
    analysis_side = {
        k: (
            v.get("single_variable"),
            tuple(v.get("bundles") or ()),
            v.get("p_value_holm") is not None,
        )
        for k, v in analysis_pairs.items()
    }

    assert set(summary_side) == set(analysis_side), "the two reports name different pairs"
    assert summary_side == analysis_side, (
        "the two reports disagree on single_variable / bundles / family membership"
    )
    assert sum(1 for v in analysis_side.values() if v[2]) == 6


def test_the_noise_floor_says_which_noise_it_measured():
    """It serves the same corpus twice, so it bounds serve-side sampling — not variance
    in the corpus, which on this ladder IS the treatment (one stochastic curator draw per
    (arm, db), n=1). Labelled `serve_replicate` so a reader of `summary.json` cannot take
    the derived MDE as applying to the curation treatment."""
    from governed_bi.eval.power import measure_floor

    a = {f"q{i}": i % 2 == 0 for i in range(20)}
    b = dict(a)
    b["q3"] = not b["q3"]
    floor = measure_floor(a, b)
    assert floor.source == "serve_replicate", (
        "the floor must name the noise it measured; a bare 'replicate' reads as though "
        "the whole treatment were replicated"
    )
    assert floor.n_discordant == 1
    # A caller measuring a different kind of noise must be able to say so.
    assert measure_floor(a, b, source="build_replicate").source == "build_replicate"


def test_the_cluster_test_reports_no_p_value_when_nothing_mapped():
    """`p_value: 1.0` for a test that ran on zero databases is a number from nothing.

    Questions with no `db_by_question` entry are dropped; with none mapped, `n_eff` is
    0 and the p-value was hardcoded to 1.0. The runbook's checklist says "the `cluster`
    block agrees", and a 1.0 reads as a measurement that agreed.

    Every database TIED is a different case: that IS a measurement whose answer is "no
    difference", so 1.0 stays right there.
    """
    from governed_bi.eval.power import cluster_sign_test

    a = {"q1": True, "q2": False}
    b = {"q1": False, "q2": True}

    unmapped = cluster_sign_test(a, b, {})
    assert unmapped["n_dbs"] == 0
    assert unmapped["p_value"] is None, "a p-value from zero databases is not a result"
    assert "nothing was tested" in unmapped["reading"]

    # Both questions in one db, one better one worse -> the db ties.
    tied = cluster_sign_test(a, b, {"q1": "db_a", "q2": "db_a"})
    assert tied["n_dbs"] == 1
    assert tied["n_dbs_tied"] == 1
    assert tied["p_value"] == 1.0, "a tie is measured, and its answer is no difference"

    # And a real split still computes.
    real = cluster_sign_test(a, b, {"q1": "db_a", "q2": "db_b"})
    assert real["n_dbs"] == 2
    assert real["p_value"] is not None
