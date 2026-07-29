"""The fair ladder must change exactly one thing per step.

`baseline -> curated` used to bundle two independent interventions that always
occurred together: a *mechanical* train-SQL pass (join and few-shot seeding, plus
marking columns absent from gold as decoys) and the *LLM* curator agent on top of
it. `build_curated_corpus` runs the first unconditionally and the second only when
`run_agent` is set, so the switch to separate them already existed and no arm used
it. Every reported "the curator LLM layer is worth N points" was therefore equally
consistent with "parsing the training SQL is worth N points" — which decides
whether the product needs an LLM curator at all.

These tests pin the decomposition, not the plumbing.
"""

from __future__ import annotations

from governed_bi.eval.arms import Arm, step_mechanisms
from governed_bi.eval.run_datalake import (
    _ARMS,
    ladder_steps,
    skipped_rungs,
)


def test_the_ladder_has_a_rung_between_baseline_and_curated():
    assert "seeded" in _ARMS
    assert skipped_rungs("baseline", "curated") == ["seeded"]


def test_every_reported_step_is_adjacent_among_the_arms_that_ran():
    """A non-adjacent delta bundles two interventions and cannot say which paid."""
    for arms in (_ARMS, ("baseline", "seeded", "curated")):
        order = [a for a in _ARMS if a in arms]
        for lo, hi in ladder_steps(arms):
            assert order.index(hi) - order.index(lo) == 1


def test_the_sme_step_is_adjacent_and_still_not_single_variable():
    """The regression guarding the 2026-07-28 removal of ``curated_sme_blind``.

    That rung existed to split the SME step into protocol-vs-human-docs, and it was
    removed as meaningless — it briefed the SME on inputs Phase A already had. The
    hazard is that removing it makes the confound *invisible*: ``curated ->
    curated_sme`` is now adjacent, so ``skipped_rungs`` is empty and a reader could
    take the step for one variable.

    It does not, because ``single_variable`` is ``not bundles and len(mechanisms) ==
    1`` — the same reason ``baseline -> seeded`` is adjacent and still compound. The
    confound now travels as a mechanism count instead of a missing rung.
    """
    steps = ladder_steps(_ARMS)
    assert ("curated", "curated_sme") in steps
    assert skipped_rungs("curated", "curated_sme") == []
    assert step_mechanisms("curated", "curated_sme") == (
        "clarification protocol",
        "BIRD human column documentation (SME brief)",
    ), "the SME step must keep declaring both mechanisms, or the confound vanishes"


def test_a_single_variable_step_bundles_nothing():
    assert skipped_rungs("baseline", "seeded") == []
    assert skipped_rungs("seeded", "curated") == []


def test_the_old_conflated_comparison_is_flagged_as_compound():
    """`baseline -> curated` was the headline and bundled the mechanical seed with
    the LLM pass. If a partial --arms selection reproduces it, it is labelled."""
    steps = ladder_steps(("baseline", "curated"))
    assert steps == [("baseline", "curated")]
    assert skipped_rungs("baseline", "curated") == ["seeded"]


def test_ladder_steps_ignores_arms_that_are_not_rungs():
    """Oracle rungs and the replicate share the summaries dict; they are not steps."""
    steps = ladder_steps(
        ("baseline", "curated", "oracle_sql", "baseline__replicate")
    )
    assert steps == [("baseline", "curated")]


def test_the_oracle_rungs_stay_off_the_fair_ladder():
    from governed_bi.eval.oracle import OracleRung

    assert {a.value for a in Arm}.isdisjoint({r.value for r in OracleRung})


def test_the_seeded_arm_is_built_with_the_agent_switched_off():
    """It must cost no model calls, or the 'free extra rung' argument collapses and
    the arm stops isolating the mechanical half."""
    import inspect

    from governed_bi.eval import run_datalake

    src = inspect.getsource(run_datalake._build_db_corpora)
    seeded_block = src.split('roots["seeded"],', 1)
    assert len(seeded_block) == 2, "the seeded build should target its own root"
    after = seeded_block[1].split("_relocate_sidecars", 1)[0]
    assert "run_agent=False" in after
    assert "model=None" in after




# --------------------------------------------------------------------------- #
# The label belongs on the pair, in the block the checklist sends people to.
#
# `deltas.*_bundles` carried this, keyed by a different convention
# (`curated_sme_minus_curated_bundles`) and only for the adjacent steps. But
# `comparisons[]` is where the p-value lives and where the pre-quote checklist
# points, and it enumerates every pair — including ones running *down* the ladder,
# which have no delta entry to cross-reference at all.
# --------------------------------------------------------------------------- #


def _correct(qids, right):
    return [
        {"question_id": q, "db_id": "db_a", "correct": q in right, "arm": "x"}
        for q in qids
    ]


def _comparisons(arms_right, **kw):
    from governed_bi.eval.run_datalake import _compare_arms

    qids = [f"q{i}" for i in range(20)]
    rows = {arm: _correct(qids, right) for arm, right in arms_right.items()}
    comparisons, _div = _compare_arms(rows, **kw)
    return {(c["arm_a"], c["arm_b"]): c for c in comparisons}


def test_every_fair_comparison_says_whether_one_thing_changed():
    by_pair = _comparisons({
        "baseline": set(),
        "seeded": {"q0", "q1"},
        "curated": {"q0", "q1", "q2"},
        "curated_sme": {"q0", "q1", "q2", "q3"},
    })

    # Adjacent, but not one variable: the rung bundles train-SQL joins, train-SQL
    # metrics and decoy marking. `single_variable` used to mean only "adjacent"
    # (AUDIT E5); the two claims are now reported separately.
    step = by_pair[("baseline", "seeded")]
    assert step["adjacent_rung"] is True
    assert step["single_variable"] is False
    assert step["mechanisms_changed"] == [
        "train-SQL-derived joins",
        "train-SQL-derived metrics",
        "decoy / negative-space column marking",
    ]
    assert "bundles" not in step

    compound = by_pair[("baseline", "curated")]
    assert compound["single_variable"] is False
    assert compound["bundles"] == ["seeded"]

    # Adjacent since the blind rung went, so nothing is skipped — but the step still
    # changes two mechanisms, and that is what keeps it out of single_variable.
    sme = by_pair[("curated", "curated_sme")]
    assert "bundles" not in sme, "nothing is skipped — the rung is adjacent"
    assert sme["single_variable"] is False
    assert len(sme["mechanisms_changed"]) == 2


def test_a_pair_running_down_the_ladder_says_so():
    """`arm_a`/`arm_b` come from `sorted()`, so `curated vs seeded` is alphabetical
    and backwards. Its `net_questions` is signed against ladder direction, and a
    reader scanning for "did this rung help" would read the sign inverted."""
    by_pair = _comparisons({
        "baseline": set(),
        "seeded": {"q0", "q1"},
        "curated": {"q0", "q1", "q2"},
    })

    down = by_pair[("curated", "seeded")]
    assert down["ladder_descending"] is True
    assert by_pair[("baseline", "seeded")]["ladder_descending"] is False
    assert by_pair[("baseline", "curated")]["ladder_descending"] is False


def test_the_label_agrees_with_the_deltas_block_and_with_analysis_json():
    """Three artifacts compute this: `comparisons[]`, `deltas.*_bundles`, and
    `analysis.json`. All three call `skipped_rungs`, and the point of that is they
    cannot disagree about what a single-variable step is."""
    from governed_bi.eval.arms import ARM_ORDER, ladder_steps, skipped_rungs

    arms = ("baseline", "seeded", "curated", "curated_sme")
    by_pair = _comparisons({a: set() for a in arms})

    for (a, b), entry in by_pair.items():
        lo, hi = sorted((a, b), key=ARM_ORDER.index)
        assert entry["adjacent_rung"] is (not skipped_rungs(lo, hi))
        assert entry.get("bundles", []) == skipped_rungs(lo, hi)
        # `single_variable` is strictly stronger than adjacency now.
        if entry["single_variable"]:
            assert entry["adjacent_rung"] is True
            assert len(entry["mechanisms_changed"]) == 1

    # And every adjacent ladder step must appear as a pair, or a step the run
    # measured would have no comparison carrying its p-value.
    for lo, hi in ladder_steps(arms):
        assert (lo, hi) in by_pair or (hi, lo) in by_pair


def test_an_off_ladder_pair_is_not_labelled_single_variable():
    """`skipped_rungs` returns `[]` for an arm it cannot place, which would read as
    "one thing changed" — the opposite of what a diagnostic pair is."""
    by_pair = _comparisons(
        {"baseline": set(), "curated": {"q0"}, "oracle_sql": {"q0", "q1"}},
    )
    for pair, entry in by_pair.items():
        if "oracle_sql" in pair:
            assert entry["diagnostic_pair"] is True
            assert entry["single_variable"] is None, pair
            assert "bundles" not in entry
            assert "ladder_descending" not in entry


def test_a_replicate_pair_is_not_labelled_single_variable():
    """A replicate is one arm served twice — not a ladder step at all, even though
    both its names sit on the ladder."""
    by_pair = _comparisons(
        {"baseline": set(), "curated": {"q0"}, "curated__replicate": {"q0"}},
        replicate_of="curated",
    )
    rep = by_pair[("curated", "curated__replicate")]
    assert rep["diagnostic_pair"] is True
    assert rep["single_variable"] is None


# --------------------------------------------------------------------------- #
# What each rung cost, and what each extra right answer cost.
#
# The ladder's mechanism is that later rungs inject more context, and context is
# billed — so a rung that buys accuracy always buys it with tokens. Per-arm totals
# were recorded, but the decision a reader has to make is whether to ship the layer,
# and that is a ratio. Leaving them to divide two numbers out of two blocks is how
# "the curator is worth N points" gets decided without anyone pricing N.
# --------------------------------------------------------------------------- #


def _deltas(spec):
    """`{arm: (n, n_correct, usd)}` through the driver's REAL delta function.

    Calls `ladder_deltas`, not a copy of it. An earlier version of this test
    re-implemented the arithmetic and therefore tested the copy — which is the failure
    this file's own subject matter is about.
    """
    from governed_bi.eval.run_datalake import ladder_deltas

    summaries = {
        arm: {
            "arm": arm,
            "n": n,
            "n_correct": n_correct,
            "question_ids": [f"q{i}" for i in range(n)],
            "ex_lenient": n_correct / n,
            "ex_gradeable": n_correct / n,
            "routing_recall": None,
            "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": usd, "n_rows_priced": n},
        }
        for arm, (n, n_correct, usd) in spec.items()
    }
    return ladder_deltas(summaries)


def test_a_rung_that_buys_answers_is_priced_per_answer():
    d = _deltas({
        "baseline": (100, 20, 1.00),
        "seeded": (100, 30, 1.00),      # free: no model calls to build or serve extra
        "curated": (100, 40, 3.00),     # +10 answers for +$2.00
    })
    assert d["curated_minus_seeded_correct_answers"] == 10
    assert d["curated_minus_seeded_usd"] == 2.0
    assert d["curated_minus_seeded_usd_per_added_correct"] == 0.2


def test_a_rung_that_bought_nothing_is_not_given_a_price():
    """Dividing by zero added answers would report an infinite price. `None` says the
    step did not buy anything to price — which is the honest reading, and the one the
    JSON can represent (`Infinity` is not valid JSON)."""
    d = _deltas({
        "baseline": (100, 20, 1.00),
        "seeded": (100, 20, 1.00),
        "curated": (100, 20, 5.00),  # spent $4 and moved nothing
    })
    assert d["curated_minus_seeded_correct_answers"] == 0
    assert d["curated_minus_seeded_usd"] == 4.0
    assert d["curated_minus_seeded_usd_per_added_correct"] is None, (
        "a step that bought no answers must not be given a price per answer"
    )


def test_a_rung_that_went_backwards_is_still_priced_under_an_honest_name():
    """Paid and lost ground — a regression that cost money is the most important cell in
    the table and must not be suppressed. It is not suppressed; it moved.

    "Dollars per additional correct answer" with a negative denominator is a different
    quantity wearing the same name, and its sign is uninterpretable — a rung that lost
    answers *and* got cheaper priced *positively* under the old key. So the magnitude is
    reported as `_usd_per_lost_correct` and the gained-answer key refuses. The decision
    this test originally pinned (price regressions, do not hide them) is preserved.
    """
    d = _deltas({
        "baseline": (100, 20, 1.00),
        "seeded": (100, 30, 1.00),
        "curated": (100, 25, 4.00),
    })
    assert d["curated_minus_seeded_correct_answers"] == -5
    assert d["curated_minus_seeded_usd_per_added_correct"] is None
    assert d["curated_minus_seeded_usd_per_lost_correct"] == 0.6, (
        "$3.00 more for 5 fewer correct answers"
    )
    assert "lost 5 correct answer" in d["curated_minus_seeded_not_priced_because"]


def test_an_unpriced_run_reports_no_price_rather_than_zero():
    """`--skip-agent` bills nothing, so `total_cost_est_usd` is `None`. Treating that
    as $0 would report every layer as free."""
    d = _deltas({
        "baseline": (100, 0, None),
        "seeded": (100, 0, None),
        "curated": (100, 0, None),
    })
    assert d["curated_minus_seeded_usd"] is None
    assert d["curated_minus_seeded_usd_per_added_correct"] is None


def test_the_priced_fields_are_json_serialisable():
    """`json.dumps` emits `Infinity` for a float division blow-up, which every other
    JSON parser rejects — the defect that once made summary.json unreadable."""
    import json
    import math

    for spec in (
        {"baseline": (10, 1, 1.0), "seeded": (10, 1, 1.0), "curated": (10, 1, 9.0)},
        {"baseline": (10, 1, 1.0), "seeded": (10, 5, 2.0), "curated": (10, 9, 3.0)},
    ):
        d = _deltas(spec)
        raw = json.dumps(d)
        assert "Infinity" not in raw and "NaN" not in raw
        for v in d.values():
            assert v is None or not isinstance(v, float) or math.isfinite(v)


def test_the_arm_summary_actually_exports_n_correct():
    """`ladder_deltas` needs `n_correct` from `_summarise_rows`. The pricing tests
    fabricate their own summaries, so deleting the export left them green while every
    priced key would have gone `None` on a real run — the tautology moved from
    "the test re-implements the arithmetic" to "the test fabricates the producer's
    contract". This drives the producer.
    """
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        {"question_id": "q1", "db_id": "d", "arm": "curated", "correct": True,
         "correct_strict": True, "split": "test"},
        {"question_id": "q2", "db_id": "d", "arm": "curated", "correct": False,
         "correct_strict": False, "split": "test"},
    ]
    summary = _summarise_rows("curated", rows)
    assert summary["n_correct"] == 1
    assert summary["n"] == 2
    assert summary["ex_lenient"] == 0.5
    # And the per-db block carries it too, since a pooled number must decompose.
    by_db = summary.get("by_db") or {}
    if by_db:
        assert by_db["d"]["n_correct"] == 1


def test_n_correct_counts_the_lenient_predicate_not_the_strict_one():
    """`ex_lenient = n_correct / n`, so `n_correct` must count `correct`. Counting
    `correct_strict` would make the numerator disagree with the rate beside it."""
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        {"question_id": f"q{i}", "db_id": "d", "arm": "curated", "correct": True,
         "correct_strict": i == 0, "split": "test"}
        for i in range(4)
    ]
    summary = _summarise_rows("curated", rows)
    assert summary["n_correct"] == 4, "must be the lenient count"
    assert summary["ex_lenient"] == 1.0
    assert summary["ex_strict"] == 0.25


def test_a_step_between_arms_of_different_size_is_not_priced():
    """The defect that produced a *wrong* number rather than an absent one: `n_correct`
    is a raw count, so at n 100 -> 50 a rung that improved EX and got cheaper reported
    losing 10 answers and priced positive — two errors cancelling into a figure that
    contradicted the `_ex` delta three keys above it."""
    d = _deltas({
        "baseline": (100, 20, 1.0),
        "seeded": (100, 40, 10.0),
        "curated": (50, 30, 6.0),   # EX 0.4 -> 0.6, cheaper, but half the questions
    })
    assert d["curated_minus_seeded_correct_answers"] is None
    assert d["curated_minus_seeded_usd_per_added_correct"] is None
    assert "curated_minus_seeded_correct_answers_unmeasured_because" in d
    assert d["curated_minus_seeded_unpaired_n_correct_delta"] == -10
    assert "scored 100 questions" in d["curated_minus_seeded_not_priced_because"]


def test_a_partially_priced_arm_is_not_priced():
    """`total_cost_est_usd` sums only rows that carried a cost, so a crashed turn burns
    model calls and contributes nothing. Dividing by a total that covers half the rows
    understates the price by exactly the unpriced share."""
    from governed_bi.eval.run_datalake import ladder_deltas

    def arm(n, n_correct, usd, priced):
        return {
            "arm": "x", "n": n, "n_correct": n_correct,
            "question_ids": [f"q{i}" for i in range(n)],
            "ex_lenient": n_correct / n, "ex_gradeable": n_correct / n,
            "routing_recall": None, "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": usd, "n_rows_priced": priced},
        }

    d = ladder_deltas({
        "seeded": arm(100, 20, 1.0, 100),
        "curated": arm(100, 30, 3.0, 50),  # only half the rows priced
    })
    assert d["curated_minus_seeded_correct_answers"] == 10
    assert d["curated_minus_seeded_usd"] == 2.0, "the total is still reported"
    assert d["curated_minus_seeded_usd_per_added_correct"] is None
    assert "50/100" in d["curated_minus_seeded_not_priced_because"]


def test_a_regression_that_got_cheaper_is_not_priced_as_a_gain():
    """The trap that motivated splitting the key. An over-cautious layer refuses more —
    refusals are cheap and wrong — so answers fall AND cost falls, and the old ratio came
    out *positive*: `_correct_answers: -10, _usd: -0.5, _usd_per_added_correct: +0.05`,
    reading as "5 cents per additional correct answer" for a 10-answer regression. The
    runbook's rule about negative figures never fired on it.
    """
    cheaper_and_worse = _deltas({
        "baseline": (100, 10, 1.0),
        "seeded": (100, 30, 2.0),
        "curated": (100, 20, 1.5),
    })
    assert cheaper_and_worse["curated_minus_seeded_correct_answers"] == -10
    assert cheaper_and_worse["curated_minus_seeded_usd"] == -0.5
    assert cheaper_and_worse["curated_minus_seeded_usd_per_added_correct"] is None, (
        "a regression must never appear under the gained-answer key, whatever the sign"
    )
    # Reported, under a name that says what it is: the step shed $0.05 per answer lost.
    assert cheaper_and_worse["curated_minus_seeded_usd_per_lost_correct"] == -0.05

    # And a genuine gain still prices positively under the gained-answer key.
    better = _deltas({
        "baseline": (100, 10, 1.0),
        "seeded": (100, 20, 1.0),
        "curated": (100, 30, 3.0),
    })
    assert better["curated_minus_seeded_usd_per_added_correct"] == 0.2
    assert "curated_minus_seeded_usd_per_lost_correct" not in better


def test_a_small_real_cost_delta_is_not_rounded_to_a_measured_zero():
    """4-decimal rounding turned a real $0.00004 step into `0.0` — a manufactured
    observed-zero in a module that is otherwise strict that 0.0 means measured."""
    d = _deltas({
        "baseline": (100, 10, 1.0),
        "seeded": (100, 20, 1.000000),
        "curated": (100, 30, 1.00004),
    })
    assert d["curated_minus_seeded_usd"] != 0.0
    assert d["curated_minus_seeded_usd"] > 0


def test_a_run_that_billed_nothing_says_so_rather_than_blaming_coverage():
    """`n_rows_priced != n` is also true when NO row carried a cost, so the
    partial-coverage branch fired for a `--skip-agent` run and reported "cost covers 0/9
    and 0/9 rows; a partial total understates the price" — the wrong diagnosis for the
    commonest case, in the field the runbook sends readers to for disambiguation."""
    from governed_bi.eval.run_datalake import ladder_deltas

    def unbilled(n, c):
        return {
            "arm": "x", "n": n, "n_correct": c, "ex_lenient": c / n,
            "question_ids": [f"q{i}" for i in range(n)],
            "ex_gradeable": c / n, "routing_recall": None,
            "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": None, "n_rows_priced": 0},
        }

    d = ladder_deltas({"seeded": unbilled(9, 0), "curated": unbilled(9, 0)})
    why = d["curated_minus_seeded_not_priced_because"]
    assert "billed nothing" in why or "recorded a cost" in why, why
    assert "unpriced share" not in why, (
        "a run that billed nothing has no unpriced share to blame"
    )


def test_every_unpriced_step_says_why():
    """The runbook promises `_not_priced_because` disambiguates the cases. A case with no
    message leaves a reader with a bare `null` and the doc's word for it."""
    from governed_bi.eval.run_datalake import ladder_deltas

    def arm(n, c, usd, priced):
        return {
            "arm": "x", "n": n, "n_correct": c, "ex_lenient": c / n,
            "question_ids": [f"q{i}" for i in range(n)],
            "ex_gradeable": c / n, "routing_recall": None,
            "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": usd, "n_rows_priced": priced},
        }

    cases = {
        "different n": {"seeded": arm(100, 20, 1.0, 100), "curated": arm(50, 15, 2.0, 50)},
        "nothing billed": {"seeded": arm(100, 20, None, 0), "curated": arm(100, 30, None, 0)},
        "partly priced": {"seeded": arm(100, 20, 1.0, 100), "curated": arm(100, 30, 3.0, 50)},
        "bought nothing": {"seeded": arm(100, 20, 1.0, 100), "curated": arm(100, 20, 5.0, 100)},
    }
    for label, spec in cases.items():
        d = ladder_deltas(spec)
        assert d["curated_minus_seeded_usd_per_added_correct"] is None, label
        assert d.get("curated_minus_seeded_not_priced_because"), (
            f"{label}: price withheld with no explanation"
        )

    # ...and a fully priced, answer-buying step carries no explanation, or the key would
    # be noise on every healthy run.
    good = ladder_deltas(
        {"seeded": arm(100, 20, 1.0, 100), "curated": arm(100, 30, 3.0, 100)}
    )
    assert good["curated_minus_seeded_usd_per_added_correct"] == 0.2
    assert "curated_minus_seeded_not_priced_because" not in good


def _priced_arm(n, n_correct, usd, priced=None, *, question_ids=None):
    return {
        "arm": "x", "n": n, "n_correct": n_correct,
        "question_ids": (
            list(question_ids) if question_ids is not None
            else [f"q{i}" for i in range(n)]
        ),
        "ex_lenient": None if n_correct is None else n_correct / n,
        "ex_gradeable": None if n_correct is None else n_correct / n,
        "routing_recall": None, "cond_ex_given_routing": None,
        "cost": {"total_cost_est_usd": usd,
                 "n_rows_priced": n if priced is None else priced},
    }


def test_one_arm_billed_and_one_not_says_which():
    """The branch was `lo_cost is None or hi_cost is None` with text claiming "no row on
    either side recorded a cost" — false when one side was billed, and it also swallowed
    any real partial-coverage problem on the side that was. Reachable live: per-arm cost
    blocks are computed independently, so a resume replaying an arm scored before cost
    instrumentation gives exactly one-sided cost."""
    from governed_bi.eval.run_datalake import ladder_deltas

    d = ladder_deltas({
        "seeded": _priced_arm(100, 20, None, priced=0),
        "curated": _priced_arm(100, 30, 2.0),
    })
    why = d["curated_minus_seeded_not_priced_because"]
    assert "either side" not in why, why
    assert "seeded" in why and "curated" in why, (
        "the message must name which arm was billed and which was not"
    )
    assert d["curated_minus_seeded_usd_per_added_correct"] is None

    # And the mirror case names them the other way round.
    d2 = ladder_deltas({
        "seeded": _priced_arm(100, 20, 2.0),
        "curated": _priced_arm(100, 30, None, priced=0),
    })
    assert "seeded recorded a cost" in d2["curated_minus_seeded_not_priced_because"]


def test_an_unmeasured_gain_is_not_reported_as_a_measured_zero():
    """`added is None` fell into `elif not added` and said "the step bought no additional
    correct answers" — an unmeasured count reported as a measurement. That conflation is
    the thing this module spends most of its comments preventing elsewhere."""
    from governed_bi.eval.run_datalake import ladder_deltas

    d = ladder_deltas({
        "seeded": _priced_arm(100, None, 1.0),
        "curated": _priced_arm(100, 30, 3.0),
    })
    assert d["curated_minus_seeded_correct_answers"] is None
    why = d["curated_minus_seeded_not_priced_because"]
    assert "unmeasured" in why, why
    assert "bought no additional" not in why, (
        "an absent n_correct is not a step that bought nothing"
    )


def test_unrecorded_coverage_is_not_reported_as_partial_coverage():
    """Same shape one branch up: with `n_rows_priced` absent the message printed
    "cost covers None/100 and None/100 rows" as though coverage had been measured."""
    from governed_bi.eval.run_datalake import ladder_deltas

    lo = _priced_arm(100, 20, 1.0)
    hi = _priced_arm(100, 30, 3.0)
    del lo["cost"]["n_rows_priced"]
    d = ladder_deltas({"seeded": lo, "curated": hi})
    why = d["curated_minus_seeded_not_priced_because"]
    assert "None/" not in why, why
    assert "unknown" in why or "not recorded" in why, why


def test_every_price_verdict_tag_is_reached_and_each_message_is_true():
    """Walk the state space by TAG, not by assertion about the fixtures.

    The previous version of this test claimed to enumerate the space and did not: its
    `n_correct` bounds made the gain only ever absent or +10, so the zero-gain and
    lost-answer branches were never entered. The guard added to catch that was itself
    tautological — it recomputed the reached values from the loop's own literals — and a
    reviewer showed that narrowing the bounds back to the buggy version left it green.

    So the source now returns a tag per outcome and publishes the full set. This asserts
    every tag is reached, which cannot be satisfied by a fixture that misses branches, and
    then checks the properties that have each been violated at least once:

    * a message appears exactly when no per-added-answer price is reported;
    * "neither side was billed" only when neither was;
    * a coverage caveat never displaces a missing or zero divisor;
    * no message ever prints a `None/` count;
    * the two price keys are mutually exclusive, and a lost-answer price requires the
      same full coverage the gained-answer price does.
    """
    from itertools import product

    from governed_bi.eval.run_datalake import (
        PRICE_VERDICT_TAGS,
        ladder_deltas,
        price_verdict,
    )

    def arm(n, n_correct, usd, priced, *, question_ids=None, drop_ids=False):
        d = {
            "arm": "x", "n": n, "n_correct": n_correct, "ex_lenient": None,
            "ex_gradeable": None, "routing_recall": None,
            "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": usd},
        }
        if not drop_ids:
            d["question_ids"] = (
                list(question_ids) if question_ids is not None
                else [f"q{i}" for i in range(n)]
            )
        if priced is not ...:  # ``...`` means the key is absent entirely
            d["cost"]["n_rows_priced"] = priced
        return d

    seen: set[str | None] = set()
    for (ln, hn), lc, hc, lp, hp, lnc, hnc in product(
        [(100, 100), (100, 50)],
        [None, 1.0], [None, 2.0],
        [..., None, 50, 100], [..., None, 50, 100],
        [None, 20], [None, 10, 20, 30],
    ):
        lo_arm, hi_arm = arm(ln, lnc, lc, lp), arm(hn, hnc, hc, hp)
        ids_lo = set(lo_arm["question_ids"])
        ids_hi = set(hi_arm["question_ids"])
        added = None if (lnc is None or hnc is None or ln != hn) else hnc - lnc
        tag, why = price_verdict(
            lo="seeded", hi="curated", n_lo=ln, n_hi=hn,
            lo_cost=lc, hi_cost=hc,
            lo_priced=None if lp is ... else lp,
            hi_priced=None if hp is ... else hp,
            added=added,
            ids_lo=ids_lo,
            ids_hi=ids_hi,
        )
        seen.add(tag)
        assert tag in PRICE_VERDICT_TAGS, f"undeclared tag {tag!r}"
        # A message accompanies every tag except the plain priceable one. gain_negative
        # carries both a message and a price — the message is what redirects the reader to
        # the key the price landed under.
        assert (why is None) == (tag is None), (
            f"tag {tag!r} and message presence disagree"
        )

        d = ladder_deltas({"seeded": lo_arm, "curated": hi_arm})
        gained = d["curated_minus_seeded_usd_per_added_correct"]
        lost = d.get("curated_minus_seeded_usd_per_lost_correct")
        state = (ln, hn, lc, hc, lp, hp, lnc, hnc)

        assert (gained is None) == (why is not None), (
            f"price and explanation disagree at {state}: {gained} / {why}"
        )
        assert not (gained is not None and lost is not None), (
            f"a step cannot both gain and lose answers at {state}"
        )
        assert (lost is not None) == (tag == "gain_negative"), (
            f"lost-answer key does not track its tag at {state}"
        )
        if why is None:
            continue

        assert not ("either side" in why and not (lc is None and hc is None)), (
            f"claimed neither side was billed at {state}: {why}"
        )
        assert "None/" not in why, f"printed an absent count at {state}: {why}"
        if tag.startswith("coverage_"):
            assert added not in (None, 0), (
                f"coverage caveat displaced a missing divisor at {state}: {why}"
            )

    # ID-set refusals are outside the scalar product above.
    tag, why = price_verdict(
        lo="seeded", hi="curated", n_lo=100, n_hi=100,
        lo_cost=1.0, hi_cost=2.0, lo_priced=100, hi_priced=100, added=10,
        ids_lo={f"q{i}" for i in range(100)},
        ids_hi={f"other{i}" for i in range(100)},
    )
    assert tag == "mismatched_ids" and why
    seen.add(tag)
    tag, why = price_verdict(
        lo="seeded", hi="curated", n_lo=100, n_hi=100,
        lo_cost=1.0, hi_cost=2.0, lo_priced=100, hi_priced=100, added=10,
        ids_lo=None, ids_hi=None,
    )
    assert tag == "ids_unrecorded" and why
    seen.add(tag)

    assert seen == set(PRICE_VERDICT_TAGS), (
        "the enumeration did not reach every outcome; missing "
        f"{sorted(str(t) for t in set(PRICE_VERDICT_TAGS) - seen)}"
    )


def test_a_lost_answer_price_also_requires_full_coverage():
    """The gained-answer price is withheld under partial coverage. The lost-answer price
    divides the same understated numerator, so it must be too — otherwise a run reports
    "cost covers 50/100 rows; a partial total understates the price" beside a published
    figure derived from it."""
    from governed_bi.eval.run_datalake import ladder_deltas

    def arm(n, c, usd, priced):
        return {
            "arm": "x", "n": n, "n_correct": c, "ex_lenient": c / n,
            "question_ids": [f"q{i}" for i in range(n)],
            "ex_gradeable": c / n, "routing_recall": None,
            "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": usd, "n_rows_priced": priced},
        }

    partial = ladder_deltas({
        "seeded": arm(100, 30, 1.0, 50),
        "curated": arm(100, 20, 4.0, 60),
    })
    assert partial["curated_minus_seeded_correct_answers"] == -10
    assert "curated_minus_seeded_usd_per_lost_correct" not in partial, (
        "a regression was priced from a cost total covering half the rows"
    )
    assert "partial total" in partial["curated_minus_seeded_not_priced_because"]

    # Fully priced, and the regression is reported.
    full = ladder_deltas({
        "seeded": arm(100, 30, 1.0, 100),
        "curated": arm(100, 20, 4.0, 100),
    })
    assert full["curated_minus_seeded_usd_per_lost_correct"] == 0.3


def test_a_zero_gain_step_still_hears_about_partial_coverage():
    """Reordering coverage after the divisor checks dropped the caveat: a step with equal
    `n_correct` and half its rows unpriced was told only "bought no additional answers"
    while the `_usd` total it was pointed at covered half the rows."""
    from governed_bi.eval.run_datalake import ladder_deltas

    def arm(n, c, usd, priced):
        return {
            "arm": "x", "n": n, "n_correct": c, "ex_lenient": c / n,
            "question_ids": [f"q{i}" for i in range(n)],
            "ex_gradeable": c / n, "routing_recall": None,
            "cond_ex_given_routing": None,
            "cost": {"total_cost_est_usd": usd, "n_rows_priced": priced},
        }

    d = ladder_deltas({
        "seeded": arm(100, 20, 1.0, 50),
        "curated": arm(100, 20, 4.0, 60),
    })
    why = d["curated_minus_seeded_not_priced_because"]
    assert "bought no additional" in why
    assert "understated" in why, (
        "the reader is sent to a _usd total that does not cover every row, with no caveat"
    )


def test_the_replicate_is_served_last_so_its_floor_absorbs_drift():
    """Arms serve sequentially, hours apart on a scale run, against a hosted provider — so
    provider drift maps monotonically onto the ladder and is indistinguishable from a
    rung's effect.

    Where the replicate sits decides how much drift the noise floor captures. Appended
    last, it is maximally distant from the arm it replicates, so the floor measured from
    that pair spans at least one arm's serve rather than being a within-moment figure.
    Served adjacent to its source it would measure only sampling, and the resolution it
    reports would be optimistic.
    """
    import inspect

    from governed_bi.eval.run_datalake import run_datalake

    src = inspect.getsource(run_datalake)
    append_replicate = src.index('serve_order.append(f"{replicate_of}__replicate")')
    loop = src.index("for arm in serve_order:")
    assert append_replicate < loop, "the replicate must be in serve_order before serving"
    # Nothing may append after it, or it stops being last.
    tail = src[append_replicate:loop]
    assert "serve_order.append" not in tail.replace(
        'serve_order.append(f"{replicate_of}__replicate")', "", 1
    )
    assert "serve_order.extend" not in tail


def test_each_arm_records_where_it_sat_in_wall_clock_time():
    """The confound above is not removed — interleaving arms per question would restructure
    the serve loop, the per-arm generations files and the resume contract. Recording the
    position makes it *detectable*: if EX tracks `serve_index` rather than the ladder, the
    ladder is measuring the provider's afternoon."""
    import inspect

    from governed_bi.eval.run_datalake import run_datalake

    src = inspect.getsource(run_datalake)
    for field in ("serve_index", "serve_started_utc", "serve_seconds"):
        assert f'summary["{field}"]' in src, f"{field} is no longer recorded per arm"


def test_every_metric_the_ladder_names_exists_in_a_real_summary():
    """`ladder_deltas` moved to `.get(metric)` so an archived summary lacking a metric
    yields `None` instead of a KeyError — which also removed the tripwire that caught a
    misspelled metric name. Deleting or typo'ing `coverage_best_effort_rate` from the loop
    left the whole suite green.

    This closes it generally rather than per-metric: every name the loop reads must be a
    key `_summarise_rows` actually produces.
    """
    import inspect
    import re

    from governed_bi.eval.run_datalake import _summarise_rows, ladder_deltas

    src = inspect.getsource(ladder_deltas)
    block = src[src.index("for metric, label in ("):]
    block = block[: block.index("):")]
    # `[a-z0-9_]+`, and the count is checked against the number of tuples in the block
    # rather than a hardcoded floor: a digit-bearing name like `ex_at_5` was invisible
    # to the old pattern while seven valid names still cleared a `>= 7` guard.
    names = re.findall(r'\("([a-z0-9_]+)",\s*"[a-z0-9_]+"\)', block)
    assert len(names) == block.count('("'), (
        f"parsed {len(names)} metric names from {block.count(chr(40) + chr(34))} tuples: {names}"
    )

    produced = _summarise_rows(
        "x",
        [
            {
                "question_id": "q1", "db_id": "d", "arm": "x", "split": "test",
                "correct": True, "generated_sql": "SELECT 1", "routed_schemas": ["d"],
                "routed_hit": True, "safety_clearance": True, "graded_delivery": False,
                "coverage_best_effort": False, "tier": "governed",
            }
        ],
    )
    missing = [n for n in names if n not in produced]
    assert not missing, (
        f"ladder_deltas reads metric(s) no arm summary produces: {missing} — with .get "
        "these silently become None deltas instead of failing"
    )
    # And the governance ones are genuinely among them, or this test drifts into vacuity.
    for expected in (
        "graded_delivery_rate",
        "safety_clearance_rate",
        "coverage_best_effort_rate",
    ):
        assert expected in names, f"{expected} is no longer a reported ladder delta"
