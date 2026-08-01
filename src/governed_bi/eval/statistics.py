"""Aggregation and comparison over scored eval rows.

Everything here reads rows that have already been graded and turns them into the
numbers a run publishes: one summary per arm, the adjacent-rung deltas between
arms, and the pairwise comparisons with their resolution. Nothing here serves a
question, calls a model, touches Postgres, or writes a file. Give it rows, get
statistics.

**This was never really private.** It lived inside ``run_datalake`` as eight
underscore-named functions plus fifteen helpers -- 1,531 lines, `_summarise_rows`
alone 629 of them, longer than most files in this repo -- while nineteen test
modules imported it by underscore name, 181 references in all. The move (M4b N19)
does not make private code public; it admits what was already true and gives it a
home. The driver keeps forwarding aliases so those references can migrate in
batches rather than in one unreviewable diff.

The move was **pure carriage**: not one statistic changed. That is checked, not
asserted -- ``scripts/statistics_golden.py`` replays a finished 1351-question x
4-arm run through these functions and requires the serialised output to be
byte-identical across the move.

Entry points, in the order a run uses them:

* :func:`summarise_rows` -- one arm's rows to one arm summary (87 fields).
* :func:`ladder_deltas` -- adjacent-rung deltas over those summaries: what each
  step moved, what it cost, and what each extra correct answer cost.
* :func:`compare_arms` -- every arm pair, paired, each carrying what the run
  could *resolve* alongside what it measured.

Read :func:`price_verdict` before trusting a dollar-per-correct figure: most
steps are not priceable, and it says which and why.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from ..stages import REFUSED_BY_TO_STAGE, Outcome, classify_row
from .analysis import corpus_census, rank_report
from .arms import ARM_ORDER, ladder_steps, skipped_rungs, step_mechanisms
from .error_taxonomy import attribute_rows, summarise_attributions
from .harness import _cost_block
from .hash_grade import free_pass_counts
from .leakage import is_gradeable_eval_row
from .oracle import OracleRung
from .power import (
    cluster_sign_test,
    comparison_report,
    correct_by_question,
    detectable_effect_for,
    holm_adjust,
    mcnemar,
    measure_floor,
)

# ``treatment.compare_arms`` compares what two arms were *served* (context
# fingerprints, one pair at a time); :func:`compare_arms` below compares how they
# *scored* (paired, every pair). Two different questions that ended up with one
# name, which only became a collision when this module gave the second one its
# real name instead of a leading underscore. Aliased rather than renamed upstream:
# renaming a published function to resolve a local clash would be a change to
# another module in a commit that is supposed to move code, not edit it.
from .treatment import (  # noqa: E402
    DEFAULT_MIN_DIVERGENCE,
    fingerprint_arm,
)
from .treatment import compare_arms as compare_treatments  # noqa: E402


def fmt_rate(value: float | None, places: int = 3) -> str:
    """Render a rate for the console, tolerating "not measured".

    Every rate in the summary is ``None`` when its denominator is empty, so a
    format spec applied straight to one raises ``TypeError``. Doing that here
    would abort the run *after* the whole serve loop and *before* ``summary.json``
    is written — hours of live model calls discarded to print a progress line.
    """
    return "n/a" if value is None else f"{value:.{places}f}"


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    """Mean of a numeric row field over the rows that recorded it, else ``None``.

    ``None`` rather than ``0.0``: a field absent from an older run means "not
    measured", and reporting that as zero would read as a real observation.
    """
    vals = [float(r[key]) for r in rows if isinstance(r.get(key), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _sum_counters(rows: list[dict[str, Any]], key: str) -> dict[str, int] | None:
    """Sum per-row ``{name: count}`` dicts across rows, or ``None`` when no row has one.

    ``None`` rather than ``{}`` for the same reason as :func:`_mean`: an empty dict
    asserts the arm made zero calls, which is a different claim from "nothing
    reported any" — and these counters come from a producer that may be older than
    this reader.
    """
    total: dict[str, int] = {}
    seen = False
    for r in rows:
        counts = r.get(key)
        if not isinstance(counts, dict):
            continue
        seen = True
        for name, value in counts.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total[str(name)] = total.get(str(name), 0) + int(value)
    return dict(sorted(total.items())) if seen else None


#: Every outcome :func:`price_verdict` can reach. Named so a test can assert the
#: enumeration walked all of them, instead of asserting something about the shape of its
#: own fixtures — a guard written that way was tautological and could not detect an
#: enumeration that missed two branches, which is the defect it existed to prevent.
PRICE_VERDICT_TAGS: tuple[str | None, ...] = (
    None,  # priceable: dollars per additional correct answer
    "gain_negative",  # priceable, but the step lost answers — priced under its own key
    "unpaired_n",
    "mismatched_ids",  # equal N, different question-id sets — not a paired gain
    "ids_unrecorded",  # equal N but neither arm recorded its question-id set
    "no_cost",
    "one_sided_cost",
    "gain_unmeasured",
    "gain_zero",
    "coverage_partial",
    "coverage_unrecorded_one_side",
    "coverage_unrecorded_both",
)


def price_verdict(
    *,
    lo: str,
    hi: str,
    n_lo: int | None,
    n_hi: int | None,
    lo_cost: float | None,
    hi_cost: float | None,
    lo_priced: int | None,
    hi_priced: int | None,
    added: int | None,
    ids_lo: "set[str] | frozenset[str] | None" = None,
    ids_hi: "set[str] | frozenset[str] | None" = None,
) -> tuple[str | None, str | None]:
    """Can this step be priced per additional correct answer, and if not, why not?

    Returns ``(tag, why)``. ``tag is None`` means priceable under
    ``_usd_per_added_correct``; ``"gain_negative"`` means priceable under
    ``_usd_per_lost_correct``; any other tag is a refusal, with ``why`` explaining it in
    words. Every reachable tag is listed in :data:`PRICE_VERDICT_TAGS`.

    Pricing is **paired**: equal row counts alone are not enough. The two arms must
    share an identical question-id set (or supply rows so a paired net gain can be
    computed). Equal-N different ID pools used to emit a dollar-per-added-correct
    figure that priced two different question sets against each other.

    A pure function over scalars, because this chain was wrong in a different cell in four
    consecutive commits and every one of them was a branch nobody had constructed a case
    for. Inline, the only way to check coverage was to assert something about a test's own
    fixtures — which is how a guard against "the enumeration misses branches" ended up
    unable to detect exactly that.

    **Order is by cause, most fundamental first:** whether the two arms are comparable at
    all, then whether the numerator exists, then whether the divisor exists, then whether
    the numerator can be trusted, and last the sign of the divisor. Coverage sits after
    the divisor checks because it is a caveat on a number that exists, while an absent or
    zero gain leaves the ratio undefined whatever the coverage — but it sits *before* the
    negative-gain case, because that case publishes a figure and so needs a numerator it
    can trust.
    """
    if n_lo != n_hi:
        return "unpaired_n", (
            f"{lo} scored {n_lo} questions and {hi} scored {n_hi}; an unpaired count "
            "difference is not a number of answers gained"
        )
    if ids_lo is None or ids_hi is None:
        return "ids_unrecorded", (
            "question-id sets were not recorded on one or both arms, so equal-N "
            "cannot be shown to be the same pool — refuse rather than price unpaired "
            "counts"
        )
    if set(ids_lo) != set(ids_hi):
        return "mismatched_ids", (
            f"{lo} and {hi} scored {n_lo} questions each but on different question "
            "ids; an equal-N unpaired delta is not a paired net gain"
        )
    if lo_cost is None and hi_cost is None:
        return "no_cost", (
            "no row on either side recorded a cost — an --oracle-only run bills "
            "nothing, and a model absent from the price table cannot be priced"
        )
    if lo_cost is None or hi_cost is None:
        # One side billed and the other not, which an "either side" wording used to
        # describe as neither. Reachable live: per-arm cost blocks are computed
        # independently, so a resume replaying an arm scored before cost instrumentation
        # gives exactly one-sided cost.
        unbilled, billed = (lo, hi) if lo_cost is None else (hi, lo)
        return "one_sided_cost", (
            f"{billed} recorded a cost and {unbilled} recorded none, so the difference "
            "between them is not a price this step paid"
        )

    fully_priced = (
        lo_priced is not None
        and hi_priced is not None
        and lo_priced == n_lo
        and hi_priced == n_hi
    )
    # Appended to the divisor refusals below. Without it, reordering coverage after those
    # checks silently dropped the caveat: a step with equal n_correct and partial coverage
    # was told only "bought no additional answers" while the _usd total it was pointed at
    # covered half the rows.
    coverage_note = (
        ""
        if fully_priced
        else (
            " (and the cost totals do not cover every row, so the total itself is "
            "understated)"
        )
    )

    if added is None:
        # Unmeasured, not zero. ``not added`` treated the two the same and reported
        # "bought no additional correct answers" for a side that never recorded
        # ``n_correct`` — the absent-versus-zero conflation this module exists to prevent.
        return "gain_unmeasured", (
            "n_correct was not recorded on one side, so the gain is unmeasured rather "
            "than zero" + coverage_note
        )
    if added == 0:
        return "gain_zero", (
            "the step bought no additional correct answers, so there is no price per "
            "answer to report — read the total in the _usd key instead" + coverage_note
        )

    if not fully_priced:
        if lo_priced is None and hi_priced is None:
            # Both absent. Naming one arm and quoting the other's number printed
            # "covers None/100", which is the shape the one-sided message was written to
            # avoid, reintroduced by writing that message without this case.
            return "coverage_unrecorded_both", (
                "priced-row coverage was not recorded on either arm, so whether the cost "
                "totals are complete cannot be established"
            )
        if lo_priced is None or hi_priced is None:
            absent, known = (lo, hi) if lo_priced is None else (hi, lo)
            known_priced = hi_priced if lo_priced is None else lo_priced
            known_n = n_hi if lo_priced is None else n_lo
            return "coverage_unrecorded_one_side", (
                f"priced-row coverage was not recorded on {absent}; {known} covers "
                f"{known_priced}/{known_n} rows, so the cost totals cannot be shown "
                "complete"
            )
        return "coverage_partial", (
            f"cost covers {lo_priced}/{n_lo} and {hi_priced}/{n_hi} rows; a partial "
            "total understates the price by the unpriced share"
        )

    if added < 0:
        # A regression is still priced — that decision is deliberate and tested — but not
        # under the gained-answer key. "Dollars per additional correct answer" with a
        # negative denominator is a different quantity wearing the same name, and its sign
        # is uninterpretable: a rung that lost 10 answers *and* got cheaper gave ``+0.05``,
        # reading as "5 cents per additional correct answer" for a regression.
        return "gain_negative", (
            f"the step lost {-added} correct answer(s), so there is no price per answer "
            "gained — the cost per answer lost is in the _usd_per_lost_correct key, and "
            "its sign says nothing about whether the step was good"
        )
    return None, None


def _question_ids_of(summary: dict[str, Any]) -> set[str] | None:
    """Question-id set recorded on an arm summary, or ``None`` if absent."""
    raw = summary.get("question_ids")
    if raw is None:
        return None
    return {str(q) for q in raw}


def _paired_net_gain(
    lo_correct: dict[str, bool] | None,
    hi_correct: dict[str, bool] | None,
) -> int | None:
    """Paired net gain on the shared question ids: hi-only correct minus lo-only.

    ``None`` when either map is missing. On identical ID sets this equals
    ``n_correct_hi - n_correct_lo``.
    """
    if lo_correct is None or hi_correct is None:
        return None
    shared = set(lo_correct) & set(hi_correct)
    hi_only = sum(1 for q in shared if hi_correct[q] and not lo_correct[q])
    lo_only = sum(1 for q in shared if lo_correct[q] and not hi_correct[q])
    return hi_only - lo_only


def ladder_deltas(
    summaries: dict[str, Any],
    *,
    rows_by_arm: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Adjacent-rung deltas: what each step moved, what it cost, and what each extra
    correct answer cost.

    Module-level so it can be exercised. As an inline block inside
    :func:`run_datalake` the only way to test it was to re-implement it in the test,
    which tests the copy — the exact failure this module has already had to fix twice.

    Every contrast is reported on EX, on the gradeable denominator, and split into its
    routing and generation halves, because ``EX = routing_recall x
    cond_ex_given_routing`` and a delta that moves only one term says *where* the rung
    helped.

    Cost-per-added-correct and ``*_correct_answers`` use **paired** question-id sets
    (from ``question_ids`` on each summary, or derived from ``rows_by_arm`` when
    supplied). Equal-N different pools refuse both the price and the canonical gain
    field; a descriptive ``*_unpaired_n_correct_delta`` may still appear under that
    unmistakable name and never feeds pricing.
    """
    correct_maps: dict[str, dict[str, bool]] = {}
    if rows_by_arm:
        from .power import correct_by_question

        correct_maps = {
            arm: correct_by_question(rows) for arm, rows in rows_by_arm.items()
        }

    deltas: dict[str, float | None] = {}
    for lo, hi in ladder_steps(summaries):
        for metric, label in (
            ("ex_lenient", "ex"),
            ("ex_gradeable", "ex_gradeable"),
            ("routing_recall", "routing_recall"),
            ("cond_ex_given_routing", "cond_ex_given_routing"),
            # Governance, as a step rather than only per arm. A rung that raises EX while
            # delivering more answers below the assurance bar, or shedding safety
            # clearance, has traded governance for score — and the ladder exists to
            # support a claim about *governed* answers, so that trade has to be as visible
            # as the EX it bought.
            ("graded_delivery_rate", "graded_delivery_rate"),
            ("safety_clearance_rate", "safety_clearance_rate"),
            ("coverage_best_effort_rate", "coverage_best_effort_rate"),
        ):
            # ``None`` when either side is unmeasured, never a subtraction. Every
            # rate here is legitimately ``None`` at an empty denominator — and
            # ``routing_recall`` is ``None`` for a whole class of runs now that a
            # bypassed pool reports "not measured" rather than 0.0 — so the direct
            # subtraction would raise TypeError after the entire serve loop and
            # before ``summary.json`` is written, discarding hours of model calls to
            # compute a delta nobody could have read anyway.
            # ``.get``, not ``[]``: a summary that predates a metric legitimately lacks
            # the key, and this function is also read over archived ``summary.json`` files.
            # Indexing turned a missing metric into a KeyError that killed every delta,
            # including the ones the summary did have.
            lo_v, hi_v = summaries[lo].get(metric), summaries[hi].get(metric)
            deltas[f"{hi}_minus_{lo}_{label}"] = (
                None if lo_v is None or hi_v is None else hi_v - lo_v
            )
        # Dollars per additional correct answer. Later rungs buy accuracy with tokens,
        # so the ship/don't-ship question is a ratio, not two totals in two blocks.
        #
        # ``None`` rather than a number wherever it would be meaningless;
        # ``_not_priced_because`` names the reason on the pair itself. Pricing requires
        # identical question-id sets — equal-N different pools used to emit a plausible
        # figure that was not a paired gain. ``tests/test_ladder_design.py`` enumerates
        # the whole state space rather than sampling it; this chain has been wrong in a
        # different cell three times.
        lo_cost = (summaries[lo].get("cost") or {}).get("total_cost_est_usd")
        hi_cost = (summaries[hi].get("cost") or {}).get("total_cost_est_usd")
        # Priced-row coverage. ``total_cost_est_usd`` sums only the rows that carried a
        # cost, so a crashed turn — which burned model calls and recorded no meta —
        # contributes nothing and silently deflates the numerator.
        lo_priced = (summaries[lo].get("cost") or {}).get("n_rows_priced")
        hi_priced = (summaries[hi].get("cost") or {}).get("n_rows_priced")
        # ``None`` when coverage is unknown, not ``False``. Guarding only on
        # ``n_rows_priced is None`` made that branch unreachable: ``_cost_block`` builds
        # the field with ``sum(...)``, so it is always an int and ``0`` when nothing was
        # priced. Every real "we did not measure" case therefore came out as ``False``,
        # which is the reading this field exists to distinguish from "we measured and
        # it was incomplete" — an arm that priced 0 of 12 rows because nothing bills
        # (an ``--oracle-only`` run) is not the same as one that priced 9 of 12 because
        # three turns crashed.
        #
        # Unknown is: no cost recorded at all on a side. Incomplete is: some rows
        # priced, not all.
        def _coverage(arm: str) -> bool | None:
            block = summaries[arm].get("cost") or {}
            priced = block.get("n_rows_priced")
            if priced is None or (not priced and block.get("total_cost_est_usd") is None):
                return None
            return priced == summaries[arm].get("n")

        lo_cov, hi_cov = _coverage(lo), _coverage(hi)
        fully_priced = (
            None if lo_cov is None or hi_cov is None else (lo_cov and hi_cov)
        )
        deltas[f"{hi}_minus_{lo}_usd"] = (
            None if lo_cost is None or hi_cost is None else round(hi_cost - lo_cost, 6)
        )
        # ...and whether that dollar delta is over the whole arm or only the rows that
        # happened to record a cost. It was computed and then dropped, so a cost delta
        # deflated by unpriced crashes read exactly like a real saving.
        deltas[f"{hi}_minus_{lo}_usd_fully_priced"] = fully_priced
        ids_lo = (
            set(correct_maps[lo])
            if lo in correct_maps
            else _question_ids_of(summaries[lo])
        )
        ids_hi = (
            set(correct_maps[hi])
            if hi in correct_maps
            else _question_ids_of(summaries[hi])
        )
        lo_n, hi_n = summaries[lo].get("n_correct"), summaries[hi].get("n_correct")
        # Canonical ``*_correct_answers`` is paired-only: identical question-id sets,
        # then the paired net gain (from rows when present, else ``n_correct`` delta —
        # which equals the paired gain when the pools are the same). Equal-N different
        # pools used to write ``hi_n - lo_n`` here while pricing refused — a statistically
        # misleading unpaired field wearing the paired name.
        ids_identical = (
            ids_lo is not None and ids_hi is not None and ids_lo == ids_hi
        )
        paired_added = (
            _paired_net_gain(correct_maps.get(lo), correct_maps.get(hi))
            if ids_identical and lo in correct_maps and hi in correct_maps
            else None
        )
        correct_unmeasured_because: str | None = None
        if ids_identical:
            if paired_added is not None:
                added = paired_added
            elif lo_n is not None and hi_n is not None:
                added = hi_n - lo_n
            else:
                added = None
                correct_unmeasured_because = (
                    "n_correct was not recorded on one side, so the paired gain is "
                    "unmeasured rather than zero"
                )
        else:
            added = None
            if ids_lo is None or ids_hi is None:
                correct_unmeasured_because = (
                    "question-id sets were not recorded on one or both arms, so "
                    "equal-N cannot be shown to be the same pool — "
                    f"{hi}_minus_{lo}_correct_answers is paired-only"
                )
            elif summaries[lo].get("n") != summaries[hi].get("n"):
                correct_unmeasured_because = (
                    f"{lo} scored {summaries[lo].get('n')} questions and {hi} scored "
                    f"{summaries[hi].get('n')}; an unpaired count difference is not a "
                    "paired net gain"
                )
            else:
                correct_unmeasured_because = (
                    f"{lo} and {hi} scored the same number of questions but on "
                    "different question ids; an equal-N unpaired delta is not a "
                    "paired net gain"
                )
            # Descriptive only — unmistakably not the paired claim, never feeds pricing.
            if lo_n is not None and hi_n is not None:
                deltas[f"{hi}_minus_{lo}_unpaired_n_correct_delta"] = hi_n - lo_n

        deltas[f"{hi}_minus_{lo}_correct_answers"] = added
        if correct_unmeasured_because is not None:
            deltas[f"{hi}_minus_{lo}_correct_answers_unmeasured_because"] = (
                correct_unmeasured_because
            )
        tag, why = price_verdict(
            lo=lo,
            hi=hi,
            n_lo=summaries[lo].get("n"),
            n_hi=summaries[hi].get("n"),
            lo_cost=lo_cost,
            hi_cost=hi_cost,
            lo_priced=lo_priced,
            hi_priced=hi_priced,
            added=added,
            ids_lo=ids_lo,
            ids_hi=ids_hi,
        )
        if why is not None:
            deltas[f"{hi}_minus_{lo}_not_priced_because"] = why
        deltas[f"{hi}_minus_{lo}_usd_per_added_correct"] = (
            round((hi_cost - lo_cost) / added, 6) if tag is None else None
        )
        if tag == "gain_negative":
            # Priced, under a name that is true. See :func:`price_verdict`.
            deltas[f"{hi}_minus_{lo}_usd_per_lost_correct"] = round(
                (hi_cost - lo_cost) / -added, 6
            )
        skipped = skipped_rungs(lo, hi)
        if skipped:
            # A compound step. Named in the artifact so the delta is not read as a
            # single-variable result.
            deltas[f"{hi}_minus_{lo}_bundles"] = skipped
            print(
                f"\n*** NOTE: the {lo} -> {hi} delta bundles more than one change — "
                f"the ladder rung(s) {', '.join(skipped)} were not scored, so this "
                "difference cannot be attributed to either intervention alone ***\n"
            )
    return deltas


def compare_arms(
    rows_by_arm: dict[str, list[dict[str, Any]]],
    *,
    replicate_of: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Paired comparisons and treatment-divergence checks for every arm pair.

    The noise floor comes from ``replicate_of``: the arm that was deliberately
    served twice. Temperature cannot be pinned through the proxy, so the floor is a
    measured property of this run rather than a constant, and without it no
    comparison can say whether its delta is resolvable. When no replicate was run,
    comparisons still carry their exact p-value but say plainly that the run's
    resolution is unknown.
    """
    names = sorted(rows_by_arm)
    correct = {arm: correct_by_question(rows) for arm, rows in rows_by_arm.items()}
    # The twin-free stratum, keyed the same way, so the headline delta can be tested
    # on the questions the curator could not have recalled. A rate alone is not enough
    # here: dropping 12% of the split widens the interval, and a delta that survives
    # on the full split can stop being resolvable on the subset — which is exactly the
    # thing a reader needs told, not left to infer from two numbers.
    # ``is False``, not falsy: an unstamped row (a resume predating the flag) is not
    # known to be twin-free, and treating it as such turned this into the pooled
    # comparison wearing the stratum's name.
    #
    # ``is_gradeable_eval_row`` as well, and that filter is the whole point of the
    # AUDIT A6 fix. ``summarise_rows`` computes the pre-registered headline
    # ``ex_no_twin`` over ``gradeable`` rows that are twin-free (denominator
    # ``n_no_twin_gradeable``); this block used to build its population from the twin
    # stamp ALONE. On the 2026-07-31 ladder that is 1236 rows here against 1085 there
    # — 125 frozen-``VALUES`` golds and 26 order-sensitive golds, questions the
    # generator can never win and which the project deliberately excludes from
    # ``ex_gradeable``. They still flipped between arms, contributing 7-25 discordant
    # pairs, so ONE pre-registered quantity had TWO values in one ``summary.json``:
    # ``curated -> curated_sme`` read +0.0922pp as the headline and -0.1618pp in the
    # block that carries the p-value — opposite signs. Sharing the denominator is what
    # makes ``no_twin.net_rate`` reconstruct the headline delta exactly.
    twin_free = {
        arm: correct_by_question(
            [
                r
                for r in rows
                if r.get("gold_twin_in_train") is False and is_gradeable_eval_row(r)
            ]
        )
        for arm, rows in rows_by_arm.items()
    }
    # A pair is only comparable on the stratum when EVERY scored row on BOTH arms
    # carries an explicit stamp. ``any(...)`` let a partially stamped file emit
    # ``comparisons[].no_twin`` from the stamped subset while pooled metrics still
    # included unstamped rows. Same population as :func:`_twin_stamps_complete`.
    twin_stamped = {
        arm: _twin_stamps_complete(rows) for arm, rows in rows_by_arm.items()
    }

    floor = None
    replicate_arm = f"{replicate_of}__replicate" if replicate_of else None
    replicate_drifted = False
    if replicate_arm and replicate_arm in correct and replicate_of in correct:
        # Whether the replicate replicated has to be settled BEFORE its numbers are
        # used. A pair that served different context was not the same configuration,
        # so what it measures is that difference, not the pipeline's noise — and a
        # floor that is not a floor produces a minimum detectable effect that is not
        # one either. Publishing them anyway would hand a reader a resolution figure
        # derived from a broken control.
        replicate_drift_check = compare_treatments(
            replicate_of,
            rows_by_arm[replicate_of],
            replicate_arm,
            rows_by_arm[replicate_arm],
        )
        divergence = replicate_drift_check.divergence
        replicate_drifted = divergence is not None and divergence >= DEFAULT_MIN_DIVERGENCE
        # ``divergence is None`` means the check could not run — no shared question
        # carried a context hash on both sides — which is NOT the same as "checked and
        # identical", and it took the same branch. That published
        # ``detectable.measured: true`` off a control whose sameness was never
        # established, which is the one claim the replicate exists to earn.
        replicate_unverified = divergence is None
        if replicate_unverified:
            print(
                f"\n*** WARNING: the {replicate_arm} control served no question with a "
                "context hash on both sides, so it could not be shown to have "
                "replicated. No noise floor is published from it. ***\n"
            )
        if not replicate_drifted and not replicate_unverified:
            floor = measure_floor(correct[replicate_of], correct[replicate_arm])
            # The MDE is NOT built here. It is a per-comparison quantity: the
            # discordance *rate* travels from the replicate, the *population* is
            # whichever pair is being tested. One shared object evaluated at the
            # replicate's own size was compared against net counts from a differently
            # sized population — see ``power.detectable_effect_for``.

    # ``{question_id: db_id}`` for the cluster test below. Built from the rows the
    # run already wrote, so it costs nothing and cannot disagree with them.
    db_by_question: dict[str, str] = {}
    for arm_rows in rows_by_arm.values():
        for row in arm_rows:
            qid, db = row.get("question_id"), row.get("db_id")
            if qid is not None and db:
                db_by_question.setdefault(str(qid), str(db))

    comparisons: list[dict[str, Any]] = []
    divergences: list[dict[str, Any]] = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            pooled = mcnemar(a, correct[a], b, correct[b])
            report = comparison_report(
                pooled,
                detectable_effect_for(pooled, floor) if floor else None,
                floor,
            )
            # A second, weaker test that does not assume questions are independent.
            # Questions are nested in databases, so the question-level p-value is
            # anticonservative by an unknown factor; this one treats a database as
            # the unit and cannot be inflated by one easy schema contributing a
            # hundred correlated rows.
            report["cluster"] = cluster_sign_test(
                correct[a], correct[b], db_by_question
            )
            # Same paired test, restricted to questions with no train twin. Reported
            # even when it agrees, because "the effect survives the stratum" is a
            # claim someone has to be able to check without re-running anything.
            # ``None`` when the flag was never stamped (a run predating it), which is
            # not the same as a stratum that came out empty.
            tf_a, tf_b = twin_free.get(a) or {}, twin_free.get(b) or {}
            if tf_a and tf_b and twin_stamped.get(a) and twin_stamped.get(b):
                stratum = mcnemar(a, tf_a, b, tf_b)
                report["no_twin"] = comparison_report(
                    stratum,
                    detectable_effect_for(stratum, floor) if floor else None,
                    floor,
                )
            else:
                report["no_twin"] = None
            if report["no_twin"] is not None:
                # The floor's RATE comes from the full-split replicate; the MDE built
                # from it is evaluated on this stratum's own ``n_shared`` (1085, not
                # 1351), because a threshold in questions is only meaningful against
                # the population the net questions were counted over. It used to be the
                # full-split threshold applied to a stratum's net — conservative here,
                # and anti-conservative the moment the replicate is the smaller of the
                # two. And Holm runs over the top-level family only, so this p is
                # raw — comparing it against the pooled ``p_value_holm``, which the
                # runbook used to tell readers to do, is biased toward "the effect
                # survives".
                report["no_twin"]["p_value_is_raw"] = True
                report["no_twin"]["floor_from_full_split"] = True
                # Which population this block was computed over, stamped rather than
                # inferred: an archived artifact from before AUDIT A6 carries the
                # twin-stamp-only population under the same key name, and its
                # ``n_shared`` is the only hint. Absence of this flag is what tells a
                # reader they are looking at the old, headline-disagreeing definition.
                report["no_twin"]["gradeable_only"] = True
            # Only fair-ladder pairs enter the Holm family. Anything off the ladder is
            # a diagnostic by construction — the oracle rungs, the replicate arm, and
            # any arm not yet in ``ARM_ORDER`` — and its p-value is not a result. The
            # test is ``ARM_ORDER`` membership, not the reconstructed
            # ``{arm}__replicate`` string, so the exclusion cannot depend on
            # ``replicate_of`` being passed consistently with the rows. Every pair the
            # replicate appears in duplicates the pair its source arm already forms, so
            # admitting them inflates Holm's multiplier on every real comparison. The
            # divergence list keeps its own, wider flag: over-reporting a treatment
            # problem is safe, under-reporting is not.
            off_ladder = a not in ARM_ORDER or b not in ARM_ORDER
            # ONE definition, stamped on both blocks below. It used to be computed
            # here for ``comparisons[]`` and separately — and more narrowly, oracle
            # pairs only — for ``treatment_divergence[]``, so a pair like
            # ``baseline vs seeded__replicate`` read ``diagnostic_pair: true`` in one
            # block and ``null`` in the other. ``index._undelivered`` skips on the
            # divergence one, so such a pair could block quotability while its own
            # comparison entry declared it a diagnostic.
            is_diagnostic = (
                off_ladder
                or bool(_is_oracle(a) or _is_oracle(b))
                or bool(replicate_arm and replicate_arm in (a, b))
            )
            report["diagnostic_pair"] = is_diagnostic
            # What this pair bundles, stamped on the pair itself: the pre-quote
            # checklist sends readers to ``comparisons[]``, so the adjacency label
            # belongs where the p-value is. Computed by the same ``skipped_rungs`` as
            # the deltas block and ``analysis.json``, so the three cannot disagree.
            # Off-ladder pairs are ``None``, not ``[]`` — ``skipped_rungs`` returns
            # ``[]`` for an arm it cannot place, which would read as single-variable.
            if report["diagnostic_pair"]:
                report["single_variable"] = None
            else:
                lo, hi = sorted((a, b), key=ARM_ORDER.index)
                bundles = skipped_rungs(lo, hi)
                report["adjacent_rung"] = not bundles
                # `single_variable` used to mean exactly `adjacent_rung`, so
                # `baseline -> seeded` claimed one variable while changing three
                # (AUDIT E5). It now means what it says, and the mechanisms are
                # listed either way so a bundled step is quotable-with-disclosure
                # rather than silently mislabelled.
                mechanisms = step_mechanisms(lo, hi)
                report["mechanisms_changed"] = list(mechanisms)
                report["single_variable"] = not bundles and len(mechanisms) == 1
                if bundles:
                    report["bundles"] = bundles
                elif len(mechanisms) > 1:
                    # An adjacent rung that still changes more than one thing —
                    # ``curated -> curated_sme`` is the live case (the clarification
                    # protocol AND BIRD's human column docs, see ``arms.Arm``). With
                    # no rung skipped there is no ``bundles`` key, so a reader keying
                    # on ``adjacent_rung`` or on ``bundles`` sees a clean
                    # single-variable step and only ``len(mechanisms_changed)`` says
                    # otherwise. Named here so the confound is a key rather than an
                    # inference — and NOT by faking a skipped rung, which would send
                    # every reader looking for an arm that does not exist.
                    report["confounded_mechanisms"] = list(mechanisms)
                # ``arm_a``/``arm_b`` come from ``sorted(rows_by_arm)``, which is
                # alphabetical, so a pair can run *down* the ladder (``curated`` vs
                # ``seeded``). ``net_questions`` is then signed against ladder
                # direction, and a reader scanning the block for "did this rung help"
                # would read the sign backwards.
                report["ladder_descending"] = (a, b) != (lo, hi)
            comparisons.append(report)
            pair = compare_treatments(a, rows_by_arm[a], b, rows_by_arm[b]).to_dict()
            if replicate_arm and {a, b} == {replicate_of, replicate_arm}:
                # The one exception to ``is_diagnostic``, and it goes the other way:
                # the control-vs-source pair is the only pair whose *sameness* is the
                # measurement, so it must stay in the gate. ``_exempt_replicate_pair``
                # inverts the expectation rather than excusing the pair.
                _exempt_replicate_pair(pair)
            elif is_diagnostic:
                # A pair involving a counterfactual rung, the replicate against some
                # other arm, or an arm nobody put on the ladder. Non-divergence there
                # means that pair's own number is meaningless, which is worth
                # reporting — but it must not void the fair ladder's comparisons,
                # which are what the run is for. The verdict is kept in the artifact
                # and excluded from the gate.
                pair["diagnostic_pair"] = True
            divergences.append(pair)

    # Family-wise error control across the fair ladder. Four arms is six pairwise
    # tests, so at a nominal 0.05 each the chance of one false positive is ~26%.
    #
    # The family is narrower than "every pair" in two ways. Diagnostic pairs are out
    # (oracle rungs and the replicate, via ``diagnostic_pair`` above). And a pair
    # sharing NO questions is out: its ``p_value`` of 1.0 comes from an empty
    # discordance count, so counting it tightens every real pair on behalf of a test
    # that never ran. ``eval.analysis`` excludes it too, or the two artifacts would
    # correct the same run across different family sizes.
    family = [
        c
        for c in comparisons
        if not c.get("diagnostic_pair") and (c.get("n_shared") or 0) > 0
    ]
    adjusted = holm_adjust([float(c["p_value"]) for c in family])
    for comparison, p_adj in zip(family, adjusted):
        comparison["p_value_holm"] = p_adj
        comparison["family_size"] = len(family)
        comparison["significant_holm"] = p_adj < 0.05
    in_family = {id(c) for c in family}
    for comparison in comparisons:
        if id(comparison) not in in_family:
            # Explicitly ``None`` rather than silently missing: a reader scanning for
            # the adjusted column should see why this row has none. ``family_size`` is
            # still stamped so the row says how large the family it was left out of
            # was.
            comparison["p_value_holm"] = None
            comparison["family_size"] = len(family)
            comparison["significant_holm"] = None
    return comparisons, divergences


def _is_oracle(arm: str) -> bool:
    return arm in {r.value for r in OracleRung}


def _exempt_replicate_pair(pair: dict[str, Any]) -> None:
    """Turn the divergence verdict inside out for a replicate pair.

    A replicate is the same corpus served twice, so identical context is the whole
    design. Left alone, the divergence gate reads that as "these two arms are the
    same experiment run twice" — which is exactly right and exactly the wrong
    conclusion to draw from it — marks the pair undelivered, and
    :func:`governed_bi.eval.index.quotable` refuses the run. Measuring the noise
    floor would disqualify every run that measured it, and worse, a run would
    become quotable again only once the replicate drifted enough to stop being a
    replicate.

    ``treatment_delivered=None`` rather than ``True``: the ledger tests ``is False``,
    so ``None`` clears the gate without asserting a treatment that was never
    supposed to exist. The measured divergence is kept — it is the context-level
    noise floor, which nothing else records — and the assertion a replicate
    actually needs is applied instead: *high* divergence means the two serves were
    not the same configuration, so the floor derived from them is not a floor.
    """
    pair["expected_identical"] = True
    divergence = pair.get("divergence")
    if divergence is not None and divergence >= DEFAULT_MIN_DIVERGENCE:
        # A replicate that did not replicate. This DOES disqualify the run, and via
        # the ordinary gate: ``treatment_delivered=False`` is what ``_undelivered``
        # reads, so setting reasons without it would file a complaint nothing ever
        # opens. The run also publishes no floor and no MDE (see ``compare_arms``),
        # because both would be derived from a control that did not hold.
        pair["treatment_delivered"] = False
        pair["replicate_drifted"] = True
        pair["reasons"] = [
            f"{pair.get('arm_a')} and {pair.get('arm_b')} are the same corpus served "
            f"twice, but their delivered context differs on {divergence:.1%} of "
            "comparable questions — they were not the same configuration, so the "
            "noise floor derived from them is not a floor, and this run reports no "
            "resolution at all rather than a wrong one"
        ]
    else:
        # Identical context is the design. ``None`` rather than ``True``: the ledger
        # tests ``is False``, so this clears the gate without asserting a treatment
        # that was never meant to exist.
        pair["treatment_delivered"] = None
        pair["replicate_drifted"] = False
        pair["reasons"] = []
        # ...but "checked and identical" and "could not be checked" are different
        # facts, and both arrive here as ``divergence is None``. Recorded so a reader
        # of the pair can tell, and so ``compare_arms``'s decision not to publish a
        # floor has a visible cause. Not a gate: an unverifiable control costs the run
        # its resolution figure, it does not invalidate the arms' own scores.
        pair["replicate_verified"] = divergence is not None


def _shortlists_from_rows(
    rows: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """``{question_id: shortlisted_schemas}`` for the rows that recorded one.

    Lets the taxonomy separate the two routing failures: a picker that chose wrongly
    from a shortlist containing the right schema is a different problem from a
    shortlist that never contained it. Rows without the field are simply absent, so
    they attribute to the picker — the conservative reading, blaming the component
    we can actually see.
    """
    out: dict[str, list[str]] = {}
    for row in rows:
        qid = row.get("question_id") or row.get("request_id")
        shortlisted = row.get("shortlisted_schemas")
        if qid is not None and isinstance(shortlisted, list) and shortlisted:
            out[str(qid)] = [str(s) for s in shortlisted]
    return out


#: The three width figures :func:`schema_width_census` reports per schema, and the
#: three keys :func:`summarise_rows` emits from them. Named once so the "not
#: measured" shape below cannot drift from the measured one.
WIDTH_FIELDS: tuple[str, ...] = ("n_tables", "n_columns", "max_table_columns")


#: The channels :func:`governed_bi.retrieval.schema_router.shortlist_schemas` can
#: report. Named here rather than imported so the summary's key set does not depend
#: on the retrieval package, and so an unknown value is a loud KeyError-shaped gap in
#: the census rather than a silently absent count.
ROUTE_CHANNELS: frozenset[str] = frozenset({"embedding", "bm25_fallback", "none"})


def routing_channel_counts(rows: "Sequence[dict[str, Any]]") -> dict[str, Any]:
    """Per-arm routing-channel census, over the rows that recorded a channel.

    Modelled on how ``routing_escaped`` is aggregated a few lines below: rows whose
    value is ``None`` leave the denominator and are counted separately, never folded
    into the healthy value. An arm that never recorded a channel reports
    ``n_routing_channel_observed = 0`` and ``routing_degraded_rate = None`` -- "not
    measured" -- rather than a 0.0 degradation rate, which reads as "the embedding
    channel ran everywhere and never failed" and is the most misleading thing this
    field could say.

    It matters because the two channels are not equivalent: on the curated corpus the
    embedding channel reaches 0.953 shortlist recall@10 against BM25's 0.906, so a
    silent fallback is a real drop with nothing else in the record to explain it
    (AUDIT R8).
    """
    observed = [r for r in rows if r.get("schema_route_channel") is not None]
    degraded_seen = [r for r in observed if r.get("schema_route_degraded") is not None]
    n_degraded = sum(1 for r in degraded_seen if r.get("schema_route_degraded"))
    counts: dict[str, Any] = {
        "n_routing_channel_observed": len(observed),
        "n_routing_degraded_observed": len(degraded_seen),
        "n_routing_degraded": n_degraded,
        "routing_degraded_rate": (
            n_degraded / len(degraded_seen) if degraded_seen else None
        ),
    }
    for channel in sorted(ROUTE_CHANNELS):
        counts[f"n_routing_channel_{channel}"] = sum(
            1 for r in observed if r.get("schema_route_channel") == channel
        )
    return counts


def schema_width_census(corpus: Any) -> dict[str, dict[str, int]]:
    """``{schema: {n_tables, n_columns, max_table_columns}}`` for one arm's corpus.

    Schema WIDTH was the one property of the pool no artifact recorded. ``by_db``
    carried EX, routing and cost per schema and not one number saying how big that
    schema is, and :func:`governed_bi.eval.analysis.corpus_census` counts tables and
    columns per **arm**, not per schema. So every wide-table question — is EX lower on
    wide schemas, does the 118-column table cost the model the answer — had to be
    answered by querying the live Postgres catalog, which means the analysis cannot be
    reproduced from a run directory and cannot be run at all once the database moves.

    The counting is :func:`~governed_bi.eval.analysis.corpus_census` itself, called
    over a per-schema view of the same assets, rather than a second implementation of
    "count the columns". A second implementation is how ``n_columns`` in ``by_db``
    would come to mean something ``corpus_census`` does not.

    ``max_table_columns`` is the one figure ``corpus_census`` has no equivalent for,
    and it is the one the wide-table hypothesis is actually about: a schema of 70
    narrow tables and a schema with one 118-column table have similar totals and
    nothing else in common.
    """
    from types import SimpleNamespace

    from ..corpus.schemas import TableAsset

    by_schema: dict[str, list[Any]] = {}
    for asset in getattr(corpus, "assets", None) or ():
        if isinstance(asset, TableAsset):
            by_schema.setdefault(str(asset.schema), []).append(asset)

    out: dict[str, dict[str, int]] = {}
    for schema, tables in sorted(by_schema.items()):
        census = corpus_census(SimpleNamespace(assets=tables))
        out[schema] = {
            "n_tables": census["n_tables"],
            "n_columns": census["n_columns"],
            "max_table_columns": max((len(t.columns) for t in tables), default=0),
        }
    return out


def _width_of(
    rows: list[dict[str, Any]],
    schema_widths: "dict[str, dict[str, int]] | None",
) -> dict[str, int | None]:
    """Roll :func:`schema_width_census` up over the schemas these rows came from.

    ``None`` on all three when no census was supplied, and ``None`` on all three when
    the census does not cover every schema present in ``rows``. The second case is the
    one worth spelling out: a sum over the schemas that happen to be in the census
    would be published under a name that claims to describe the pool, and a pool width
    that is short by one schema is indistinguishable from a narrower pool. Absent
    beats understated — the rule the rest of this module applies to rates.

    Not derived from the per-row ``gold_table_max_columns`` field, deliberately. That
    field is the widest table the GOLD SQL touches, which is a property of the
    question; these three are properties of the schema. Reporting the first under the
    name of the second would put a number in ``max_table_columns`` that is a lower
    bound on it, on a run where nothing said so.
    """
    absent: dict[str, int | None] = dict.fromkeys(WIDTH_FIELDS, None)
    if not schema_widths:
        return absent
    present = {str(db) for r in rows if (db := r.get("db_id")) is not None}
    if not present or any(db not in schema_widths for db in present):
        return absent
    known = [schema_widths[db] for db in sorted(present)]
    return {
        "n_tables": sum(int(w.get("n_tables") or 0) for w in known),
        "n_columns": sum(int(w.get("n_columns") or 0) for w in known),
        "max_table_columns": max(int(w.get("max_table_columns") or 0) for w in known),
    }


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """``{value: rows}`` for one row field, skipping rows that do not carry it.

    Rows without the key are dropped rather than pooled under ``"None"``: a phantom
    bucket named after a missing field reads as a real database.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        out.setdefault(str(value), []).append(row)
    return out


def _drop_keys(block: dict[str, Any], keys: "tuple[str, ...]") -> dict[str, Any]:
    """``block`` without ``keys``. Absence beats a value that means the wrong thing."""
    return {k: v for k, v in block.items() if k not in keys} if keys else block


def _ungrouped(rows: list[dict[str, Any]], key: str) -> int:
    """How many rows :func:`_group_by` dropped for lacking ``key``.

    Dropping them is right — a bucket named after a missing field reads as a real
    database — but dropping them *unaccounted for* is the failure this harness exists
    to stop: ``sum(by_db[*].n)`` would quietly stop equalling the arm's ``n`` and no
    field would say why. ``_grade_one`` always stamps ``db_id``, including on a crashed
    turn, so a non-zero count here means rows arrived from somewhere else — a
    ``--resume-from`` across a row-shape change, or a hand-edited file.
    """
    return sum(1 for row in rows if row.get(key) is None)


def routing_escaped(
    used_schemas: "set[str] | None",
    routed: "list[str]",
    *,
    bypassed: bool,
    unresolved_ids: "list[str] | tuple[str, ...] | None" = None,
) -> bool | None:
    """Did the ANSWER use a table outside the schemas the router selected?

    ``used_schemas`` is resolved from the turn's ``tables_used`` — the tables parsed out of
    the SQL that was actually delivered. Not from ``licensed_tables``: that is the
    assemble-time seed license, computed from the *routed* corpus and never amended, so it
    cannot contain an out-of-routed schema no matter what the agent went on to do. Scored
    that way, a turn that reached past the router via ``search_corpus`` and
    ``inspect_schema`` — with the guardrail passing the out-of-routed table — was reported
    as compliant, and the metric could only ever return ``False`` or ``None``.

    ``None`` when there is nothing to judge **or** when judgment is unknown:

    * the router was bypassed (single-schema corpus, or an oracle rung handed its schema);
    * the turn produced no SQL / empty ``tables_used`` (genuinely unobserved);
    * ``tables_used`` was non-empty but some asset ids did not resolve — we cannot claim
      compliance, so escape is unknown rather than silently ``None``-as-unobserved.

    A non-empty unresolved set that still has a resolved schema outside the routed set
    is a definitive escape (``True``). Unresolved-only or unresolved-with-all-resolved-
    inside yields ``None`` (unknown); stamp ``routing_escape_unknown`` on the row.
    """
    unresolved = [str(x) for x in (unresolved_ids or ()) if x]
    if bypassed:
        return None
    if not used_schemas and not unresolved:
        return None
    routed_set = set(routed or ())
    if not routed_set:
        return None
    if used_schemas and (used_schemas - routed_set):
        return True
    if unresolved:
        return None
    return False


def _bool_rate(rows: list[dict[str, Any]], key: str) -> float | None:
    """Share of rows where ``key`` is ``True``, over the rows that recorded it at all.

    ``None`` at an empty denominator, and the denominator is rows where the field is not
    ``None`` — not every row. A boolean that was never stamped is not a boolean that was
    stamped ``False``, and averaging over all rows would report a governance failure
    wherever the instrumentation simply did not run.
    """
    observed = [r for r in rows if r.get(key) is not None]
    if not observed:
        return None
    return sum(1 for r in observed if r.get(key)) / len(observed)


def _rate_over(rows: list[dict[str, Any]]) -> float | None:
    """EX over a subset, ``None`` on an empty one. Absent is not zero."""
    return (sum(1 for r in rows if r.get("correct")) / len(rows)) if rows else None


def _twin_stamps_complete(rows: "Sequence[dict[str, Any]]") -> bool:
    """True when every scored row carries an explicit ``gold_twin_in_train`` stamp.

    Summary twin EX strata and ``comparisons[].no_twin`` share this gate. The
    population is **all scored rows**, not the gradeable subset: an unstamped
    frozen or order-sensitive row is still an incomplete stamp, and letting the
    summary emit strata while comparisons refused (or the reverse) was the
    residual after the unstamped-as-false fix. Empty arms are incomplete.
    """
    return bool(rows) and all(r.get("gold_twin_in_train") is not None for r in rows)


def _guardrail_ceiling(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Upper bound on answers a guardrail block may have cost.

    Module-level so it can be tested without building a whole summary. See the call
    site for why this is a ceiling and not a measurement.
    """

    def blocked_layers(row: dict[str, Any]) -> list[str]:
        raw = row.get("by_guardrail_layer")
        if not isinstance(raw, dict):
            return []
        return sorted(k for k, v in raw.items() if isinstance(v, int) and v > 0)

    observed = [r for r in rows if isinstance(r.get("by_guardrail_layer"), dict)]
    blocked = [r for r in rows if blocked_layers(r)]
    wrong = [r for r in blocked if not r.get("correct")]
    by_layer: Counter[str] = Counter()
    for r in wrong:
        by_layer.update(blocked_layers(r))
    return {
        # Rows that recorded guardrail layers AT ALL. ``n_blocked: 0`` has two
        # readings — nothing was blocked, or nothing was instrumented — and only the
        # first is a governance result. A row with no ``by_guardrail_layer`` cannot
        # be told from a clean one by ``blocked_layers``, which returns ``[]`` for
        # both, so the denominator has to be counted separately or the ceiling is
        # quoted over an unknown share of the arm.
        "n_observed": len(observed),
        "n_blocked": len(blocked),
        "n_blocked_and_wrong": len(wrong),
        "blocked_then_wrong_rate": (len(wrong) / len(blocked)) if blocked else None,
        "by_layer": dict(by_layer.most_common()),
    }


def _ex_by_stamp(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """EX per stamped value of ``key``, plus a count of the rows that lacked it.

    Deliberately not :func:`_bucket`, which groups on ``str(r.get(key))`` and so
    renders an unstamped row as a ``"None"`` bucket sitting beside the real stamp
    values. An instrumentation gap must not look like a stamp level.

    But excluding those rows and counting nothing was the other half of the same
    defect: the calibration line then reads as a confident statement over an unknown
    fraction of the arm, and on a run where the stamp was mostly missing it looks
    identical to one where it was mostly present. ``n_unstamped`` sits INSIDE this
    block rather than beside it so the exclusion travels with the numbers it
    qualifies — which means every reader of this dict must skip the non-stamp key
    (see the calibration print in the serve loop).

    Module-level so it can be tested without building a whole summary, like
    :func:`_guardrail_ceiling`.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    unstamped = 0
    for r in rows:
        raw = r.get(key)
        if not raw:
            unstamped += 1
            continue
        groups.setdefault(str(getattr(raw, "value", raw)), []).append(r)
    out: dict[str, Any] = {
        k: {"n": len(v), "ex_lenient": _rate_over(v)} for k, v in sorted(groups.items())
    }
    out["n_unstamped"] = unstamped
    return out


def _split(
    population: list[dict[str, Any]],
    flag: "Callable[[dict[str, Any]], bool | None]",
    outcome: str,
) -> dict[str, Any]:
    """Rate of ``outcome`` on both sides of a per-row predicate.

    ``flag`` returns ``None`` for a row that never recorded the input, and those
    rows are counted out rather than filed on the negative side — the failure mode
    the twin strata already document: ``not r.get(...)`` puts an ABSENT key in the
    FALSE stratum, which silently turns one side into the pooled figure.

    Module-level so it can be tested without building a whole summary.
    """
    yes: list[dict[str, Any]] = []
    no: list[dict[str, Any]] = []
    unstamped = 0
    for r in population:
        v = flag(r)
        if v is None:
            unstamped += 1
        elif v:
            yes.append(r)
        else:
            no.append(r)

    def rate(group: list[dict[str, Any]]) -> float | None:
        return (sum(1 for r in group if r.get(outcome)) / len(group)) if group else None

    return {
        "with": rate(yes),
        "without": rate(no),
        "n_with": len(yes),
        "n_without": len(no),
        "n_unstamped": unstamped,
    }


def _positive(key: str) -> "Callable[[dict[str, Any]], bool | None]":
    """``_split`` predicate: did the row record a positive count for ``key``?"""

    def flag(r: dict[str, Any]) -> bool | None:
        v = r.get(key)
        return None if v is None else bool(v) and v > 0

    return flag


def summarise_rows(
    arm: str,
    rows: list[dict[str, Any]],
    *,
    gold: dict[str, str] | None = None,
    corpus_note_assets: int | None = None,
    # Per-schema table/column counts from :func:`schema_width_census`, built off the
    # corpus the arm actually served. Optional and defaulted to ``None`` because this
    # function is also run over archived ``generations.*.jsonl`` with no corpus to
    # hand; when it is absent the three width fields are ``None`` rather than zero.
    schema_widths: "dict[str, dict[str, int]] | None" = None,
    nested: bool = False,
) -> dict[str, Any]:
    """Aggregate scored rows into one arm summary.

    Aggregating from **rows** (not from in-flight task results) is what lets a
    resumed run summarise identically to an uninterrupted one: replayed rows and
    freshly scored rows go through exactly this function.

    ``gold`` maps question_id to gold SQL. When supplied, wrong answers are also
    attributed to a stage and an error class
    (:mod:`governed_bi.eval.error_taxonomy`); without it the summary still reports
    outcomes, it just cannot say what kind of wrong a wrong answer was.

    ``corpus_note_assets`` is how many notes the served corpus held. It is what
    lets :func:`governed_bi.eval.treatment.treatment_reasons` say "this arm held
    notes and injected none" — the signature of both interventions that were
    reported as measured nulls. Without it that check is unreachable, since its
    guard is ``if count and not injected``.

    ``schema_widths`` is :func:`schema_width_census` over the corpus this arm served.
    It is what puts ``n_tables`` / ``n_columns`` / ``max_table_columns`` on the summary
    and on every ``by_db`` block, so wide-schema analysis reads a run directory instead
    of the live catalog. It has to be *passed*: rows carry no schema inventory (the
    widest field on one, ``licensed_tables``, is what the turn was licensed for, not
    what the schema holds), and this module never reaches for a corpus itself — the
    driver has ``arm_corpus`` in scope at the call site and hands it over there. Left
    ``None``, the three fields are ``None``: not measured, not zero.
    """
    n = len(rows)
    n_correct = sum(1 for r in rows if r.get("correct"))
    n_strict = sum(1 for r in rows if r.get("correct_strict"))
    produced = [r for r in rows if r.get("generated_sql")]
    n_produced = len(produced)
    # Correct answers among the rows that PRODUCED SQL — the numerator
    # ``conditional_ex_lenient`` needs, drawn from the same rows as its denominator.
    # It used to divide ``n_correct`` (every row) by ``n_produced``, the identical
    # population mix ``cond_ex_given_routing`` was rewritten to remove; see the
    # comment there. It happened to agree only because ``hash_grade`` never marks a
    # row correct without SQL, an invariant nothing asserts — and the day a grading
    # free pass (empty gold, no FROM) scores a refusal correct, this exceeds 1.0.
    n_correct_produced = sum(1 for r in produced if r.get("correct"))
    n_decoy = sum(1 for r in produced if r.get("decoy_touch"))
    # One vocabulary for how each turn ended (``governed_bi.stages``). A crash and a
    # refusal used to be the same row shape, so ``refusal_rate`` absorbed the crash
    # count and EX absorbed the loss — by a different amount per arm, since the arms
    # do not crash equally. Counting them apart is the whole point.
    by_outcome: dict[str, int] = {}
    by_failed_stage: dict[str, int] = {}
    for row in rows:
        outcome, stage, _recognised = classify_row(row)
        by_outcome[outcome.value] = by_outcome.get(outcome.value, 0) + 1
        if stage is not None:
            by_failed_stage[stage.value] = by_failed_stage.get(stage.value, 0) + 1
    n_answered = by_outcome.get(Outcome.answered.value, 0)
    n_refused = by_outcome.get(Outcome.refused.value, 0)
    n_crashed = by_outcome.get(Outcome.crashed.value, 0)
    # Routing metrics are computed over ONE population, excluding three kinds of row
    # that carry no routing decision: crashes (no meta at all, so ``routed_hit=False``
    # would charge the crash to the router), bypassed turns (a single-schema corpus is
    # pinned, not routed — counting them as misses reports 0.0, as hits reports 1.0,
    # and on an oracle rung that would be the rung grading its own gift), and turns
    # that ended before ``assemble`` (``routed_hit is None``). The denominator is
    # defined on POSITIVE evidence — a recorded decision — so absence cannot read as a
    # miss, and each exclusion is reported as ``n_routing_*`` so it stays visible.
    # Applying an exclusion to the numerator but not the denominator lets recall
    # exceed 1.0; one population is what prevents that.
    unbypassed = [r for r in rows if not r.get("routing_bypassed")]
    n_routing_bypassed = n - len(unbypassed)
    uncrashed = [r for r in unbypassed if classify_row(r)[0] is not Outcome.crashed]
    n_routing_crashed = len(unbypassed) - len(uncrashed)
    routing_rows = [r for r in uncrashed if r.get("routed_hit") is not None]
    n_routing_unrecorded = len(uncrashed) - len(routing_rows)
    n_routed_hit = sum(1 for r in routing_rows if r.get("routed_hit"))
    # The RETRIEVAL channel's own recall, which `routing_recall` above cannot report.
    #
    # Under ``route_llm_pick=True`` the serve path sets ``routed = frozenset([picked])``
    # (``analyst/agent.py``), so ``routed_hit`` IS ``pick_hit`` — verified row-by-row on
    # all 1351 rows of the 2026-07-31 ladder, where ``routing_recall`` and
    # ``schema_pick_accuracy`` agree to sixteen decimal places on every arm. What the
    # shortlist actually surfaced was recoverable only by summing the non-miss buckets
    # of ``by_gold_rank``; on that run it is 1286/1351 = 0.952 against a pick accuracy
    # of 1180/1351 = 0.873, i.e. two thirds of the routing loss is the picker discarding
    # a schema retrieval had already found. That is a different repair from widening
    # the shortlist, and no scalar in any artifact said so.
    #
    # Same carve-outs as ``routing_recall``, in the same order and off the same
    # populations: bypassed turns are gone (``unbypassed``), crashes are gone
    # (``uncrashed``), and the denominator is then defined on POSITIVE evidence — a
    # recorded shortlist — so a turn that ended before retrieval ran is excluded rather
    # than counted as a miss. ``gold_schema_rank is not None`` is the hit test
    # (``run_datalake`` sets it to ``shortlisted.index(db) + 1``), and it is checked
    # against ``is not None`` rather than truthiness because rank 1 is the *best*
    # possible value and ``not 1`` is False only by luck of the 1-based indexing.
    #
    # A row that recorded a rank but no shortlist list still counts as observed: the
    # rank is itself the positive evidence, and dropping it would silently shrink the
    # denominator toward the rows that happen to carry the wider field.
    shortlist_rows = [
        r
        for r in uncrashed
        if isinstance(r.get("shortlisted_schemas"), list)
        or r.get("gold_schema_rank") is not None
    ]
    n_shortlist_observed = len(shortlist_rows)
    n_shortlist_hit = sum(
        1 for r in shortlist_rows if r.get("gold_schema_rank") is not None
    )
    # How often an answer reached past the router. Drawn from ``routing_rows``, so it
    # inherits all three carve-outs above (bypassed, crashed, unrecorded) and then adds
    # its own: rows where ``routing_escaped`` is ``None``, because a refusal licensed
    # nothing and so neither obeyed nor escaped.
    escape_rows = [r for r in routing_rows if r.get("routing_escaped") is not None]
    n_routing_escaped = sum(1 for r in escape_rows if r.get("routing_escaped"))
    n_correct_escaped = sum(
        1 for r in escape_rows if r.get("routing_escaped") and r.get("correct")
    )
    # Rows with non-empty ``tables_used`` that could not be fully resolved — escape is
    # unknown, not unobserved. Excluded from the escape-rate denominator (which is
    # definitive True/False only) and counted here so undercount is visible.
    n_routing_escape_unknown = sum(
        1 for r in routing_rows if r.get("routing_escape_unknown")
    )
    n_tables_used_unresolved = sum(
        int(r.get("n_tables_used_unresolved") or 0) for r in rows
    )
    # Literally the population above. Recomputing it with its own filter is what let
    # the two drift apart in the first place.
    n_routing_observed = len(routing_rows)
    # EX *given* the router got the schema right needs a numerator drawn from the
    # same rows as its denominator. Dividing every correct row by only the routed
    # ones is EX/routing_recall wearing a conditional's name, and it exceeds 1.0 the
    # moment a question is answered correctly off a schema the router missed.
    n_correct_routed = sum(
        1 for r in routing_rows if r.get("routed_hit") and r.get("correct")
    )
    # Correct answers on questions whose schema the router missed — drawn from the
    # routed population only, so a bypassed row (which had no routing decision) is not
    # booked as a correct answer the router failed to enable.
    n_correct_routing_rows = sum(1 for r in routing_rows if r.get("correct"))
    n_correct_unrouted = n_correct_routing_rows - n_correct_routed
    # ...which leaves the rows excluded above, and they must be reported separately
    # rather than swept into one residual. ``EX`` is computed over EVERY row, so once
    # excluded rows exist the decomposition ``EX == routing_recall *
    # cond_ex_given_routing`` silently stops holding while ``n_correct_unrouted`` —
    # the field documented as the escape hatch — still reads 0.
    #
    # Each is counted DIRECTLY, not by subtraction. ``n_correct_bypassed`` used to be
    # ``n_correct - n_correct_routing_rows``, which was correct only while "bypassed"
    # was the sole exclusion; once unrecorded turns were also excluded, a correct
    # answer on a turn that recorded no routing decision was booked into a field named
    # "bypassed" — producing ``n_correct_bypassed > n_routing_bypassed``, an
    # impossible pair for anyone cross-checking the identity.
    n_correct_bypassed = sum(
        1 for r in rows if r.get("routing_bypassed") and r.get("correct")
    )
    n_correct_routing_unrecorded = sum(
        1 for r in uncrashed if r.get("routed_hit") is None and r.get("correct")
    )
    # Counted directly, like the other four. Deriving it by subtraction made
    # ``n_correct_unaccounted`` below identically zero — the residual absorbed
    # whatever the four named buckets missed and then the check subtracted the same
    # five terms, so it could never fire. That is the defect this block was rewritten
    # to remove (``n_correct_bypassed`` as a residual), relocated one bucket over: a
    # sixth exclusion produced ``n_correct_routing_crashed = 2`` beside
    # ``n_routing_crashed = 0`` with the check still reading 0.
    n_correct_routing_crashed = sum(
        1
        for r in unbypassed
        if classify_row(r)[0] is Outcome.crashed and r.get("correct")
    )
    # Over the SAME uncrashed population as every other routing metric. Left over
    # ``rows``, a crash that happened to record a pick counted in
    # ``schema_pick_accuracy`` while the same crash recording a route was struck from
    # ``routing_recall`` — two members of one family, named together in the runbook,
    # disagreeing about whether a crashed turn is an observation. Under the rate-limit
    # storm this block was rewritten for, that is a systematic split.
    picks = [r for r in uncrashed if r.get("schema_pick") is not None]
    n_pick_hit = sum(1 for r in picks if r.get("pick_hit"))
    genuine_picks = [r for r in picks if not r.get("schema_pick_fallback")]
    n_missing_gold = sum(1 for r in rows if r.get("error") == "missing_gold_hash")
    # The other ungradeable shape: a gold hash exists but the artifact recorded it as
    # unusable. Those rows score correct=False and sit in every EX denominator, so
    # without a count the understatement they cause is nameless.
    n_gold_unusable = sum(
        1 for r in rows if str(r.get("error") or "").startswith("gold_unusable:")
    )

    gradeable = [r for r in rows if is_gradeable_eval_row(r)]
    n_gradeable = len(gradeable)
    width = _width_of(rows, schema_widths)
    _measured_notes = [
        r for r in rows if isinstance(r.get("n_notes_injected"), (int, float))
    ]

    def _bucket(key: str) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(str(r.get(key)), []).append(r)
        return {
            k: {
                "ex_lenient": sum(1 for r in v if r.get("correct")) / len(v),
                "n": len(v),
            }
            for k, v in sorted(groups.items())
        }

    # Every rate below is ``None`` when its denominator is empty. An arm that scored
    # zero rows measured nothing, and rendering that as 0.0 makes a run that never
    # ran look like a run that failed everything — which the ledger's quotability
    # check then reads as "crash_rate recorded, and it was fine".
    return {
        "arm": arm,
        "n": n,
        # The count, not only the rate. A marginal cost per additional correct answer
        # needs a numerator in answers, and reconstructing it from ``ex_lenient * n``
        # re-introduces rounding into a figure that is exactly an integer.
        "n_correct": n_correct,
        # Sorted question ids for paired pricing. Equal-N different pools must not
        # price; :func:`ladder_deltas` / :func:`price_verdict` require identical sets.
        "question_ids": sorted(
            {
                str(qid)
                for r in rows
                if (qid := r.get("question_id") or r.get("request_id")) is not None
            }
        ),
        "ex_lenient": (n_correct / n) if n else None,
        "ex_strict": (n_strict / n) if n else None,
        # EX over questions a generator can actually win (frozen-VALUES golds
        # removed from the denominator). Every arm-to-arm delta is proportionally
        # larger here, which is what makes small real effects visible.
        "n_gradeable": n_gradeable,
        "n_frozen_gold": sum(1 for r in rows if r.get("gold_frozen")),
        "n_order_sensitive_gold": sum(
            1 for r in rows if r.get("gold_order_sensitive")
        ),
        "ex_gradeable": (
            sum(1 for r in gradeable if r.get("correct")) / n_gradeable
            if n_gradeable
            else None
        ),
        # EX split by whether the gold statement already existed in train (see
        # ``eval.leakage``). ``ex_no_twin`` is the defensible headline — the curator had
        # nothing to recall there, so a lift is generalisation; ``ex_twin`` sits beside
        # it rather than hidden.
        #
        # ``None`` when a stratum is empty OR when stamp coverage is incomplete, gated
        # by :func:`_twin_stamps_complete` (the same gate ``comparisons[].no_twin``
        # uses). Test on ``is not None``, never truthiness: an unstamped row has the key
        # ABSENT, and ``not r.get(...)`` would file it as twin-FREE, which silently
        # turns ``ex_no_twin`` into the pooled EX on a resumed run.
        "n_gold_twin_in_train": sum(
            1 for r in rows if r.get("gold_twin_in_train") is True
        ),
        "n_twin_unstamped": sum(
            1 for r in rows if r.get("gold_twin_in_train") is None
        ),
        "ex_no_twin": (
            _rate_over(
                [r for r in gradeable if r.get("gold_twin_in_train") is False]
            )
            if _twin_stamps_complete(rows)
            else None
        ),
        "n_no_twin_gradeable": sum(
            1 for r in gradeable if r.get("gold_twin_in_train") is False
        ),
        "ex_twin": (
            _rate_over(
                [r for r in gradeable if r.get("gold_twin_in_train") is True]
            )
            if _twin_stamps_complete(rows)
            else None
        ),
        "n_twin_gradeable": sum(
            1 for r in gradeable if r.get("gold_twin_in_train") is True
        ),
        # GENUINE refusals only — the product declining. A crash is our bug and gets
        # its own rate; mixing them measured two things and reported one.
        "refusal_rate": (n_refused / n) if n else None,
        "n_answered": n_answered,
        "n_refused": n_refused,
        "n_crashed": n_crashed,
        "crash_rate": (n_crashed / n) if n else None,
        # The complete partition, so the three headline counts above can be checked
        # against ``n`` — `capped` and `clarification` turns exist and would
        # otherwise be an invisible remainder.
        "by_outcome": by_outcome,
        # Where the failures were decided. A bucket only appears when something
        # actually observed it: an unattributable failure leaves no stage weight
        # rather than a guess.
        "by_failed_stage": by_failed_stage,
        # ``refused_by`` is free text with no central declaration, so a typo would
        # otherwise mint a failure category no report ever mentions.
        "n_unmapped_refused_by": sum(
            1
            for r in rows
            if r.get("refused_by") is not None
            and str(r.get("refused_by")) not in REFUSED_BY_TO_STAGE
        ),
        # ``None``, not 0.0, when no row produced SQL: an arm that refused or crashed
        # on everything touched no decoys because it wrote no SQL, and reporting that
        # as a perfect governance score rewards the worst possible run.
        "decoy_touch_rate": (n_decoy / n_produced) if n_produced else None,
        "n_decoy_touch": n_decoy,
        "conditional_ex_lenient": (
            (n_correct_produced / n_produced) if n_produced else None
        ),
        # Routing recall: share of questions whose TRUE schema survived routing,
        # over the turns that actually reached the router (crashes excluded — see
        # ``n_routing_observed``). This is the ceiling on EX in the data lake.
        "routing_recall": (n_routed_hit / n_routing_observed) if n_routing_observed else None,
        "n_routing_observed": n_routing_observed,
        # The retrieval channel, reported apart from the picker. See the derivation
        # above for why ``routing_recall`` cannot stand in for it under
        # ``route_llm_pick=True``.
        "shortlist_recall": (
            (n_shortlist_hit / n_shortlist_observed) if n_shortlist_observed else None
        ),
        "n_shortlist_hit": n_shortlist_hit,
        # Its denominator, which is NOT ``n_routing_observed``: a turn can record a
        # shortlist and no routing decision, or the reverse. Reported so
        # ``shortlist_recall`` cannot be read over an unknown share of the arm — and so
        # ``n_routing_observed - n_shortlist_observed`` names the gap rather than
        # leaving a reader to assume the two rates share a population.
        "n_shortlist_observed": n_shortlist_observed,
        # Turns with no routing decision to score (a one-schema corpus, or an oracle
        # rung that was handed its schema). Excluded from every routing metric above;
        # reported here so a ``routing_recall: null`` is legible as "nothing to route"
        # rather than "the field went missing".
        "n_routing_bypassed": n_routing_bypassed,
        # Turns that recorded no routing decision at all — they ended before
        # ``assemble`` ran. Also excluded from the denominator, and also counted, for
        # the same reason: the alternative is charging the router for turns it never
        # saw. Non-zero on a live arm means the serve path is losing provenance.
        "n_routing_unrecorded": n_routing_unrecorded,
        # Rows the per-db grouping could not place. Non-zero means
        # ``sum(by_db[*].n)`` does not add up to ``n``, and says so instead of
        # leaving the reader to notice.
        "n_rows_no_db_id": _ungrouped(rows, "db_id"),
        # Exception classes behind the crashes, counted. A crash rate already blocks
        # quotability; this says whether to re-run at lower concurrency (a wall of
        # ``RateLimitError``) or to go and fix something (anything else). Empty when
        # nothing crashed, absent-as-empty being unambiguous here because
        # ``crash_rate`` sits beside it.
        "by_error_type": dict(
            Counter(
                str(r.get("error_type"))
                for r in rows
                if r.get("error_type")
            ).most_common()
        ),
        # --- The governance stamp, aggregated -----------------------------------
        # Every row carries these and nothing reported them, so a run could say EX moved
        # and not whether the answers were *governed* — which is half of what the corpus
        # is claimed to buy. Reliability is graded on ``semantic_assurance`` (safety stays
        # hard), so an arm that raises EX while shifting mass from ``verified`` toward
        # ``unverified`` has not made the product better in the way the claim means.
        #
        # Counted over the rows that RECORDED each field, with the denominator reported
        # beside it. A row that never recorded one is not a row that recorded a bad value,
        # and lumping them together is how an instrumentation gap reads as a governance
        # failure — or the reverse.
        "by_tier": dict(
            Counter(
                # ``getattr(..., "value", ...)`` rather than ``str``: every producer
                # stamps ``.value`` today, but ``str(ReliabilityTier.governed)`` is
                # ``'ReliabilityTier.governed'`` while the same value round-trips
                # through JSON as ``'governed'`` — so a future enum-stamping producer
                # would split one tier across two keys on a resume, silently.
                str(getattr(r.get("tier"), "value", r.get("tier")))
                for r in rows
                if r.get("tier")
            ).most_common()
        ),
        "by_semantic_assurance": dict(
            Counter(
                str(
                    getattr(
                        r.get("semantic_assurance"),
                        "value",
                        r.get("semantic_assurance"),
                    )
                )
                for r in rows
                if r.get("semantic_assurance")
            ).most_common()
        ),
        "n_with_governance_stamp": sum(1 for r in rows if r.get("tier")),
        # ── Conditional diagnostics: does the governance actually do anything? ──
        #
        # Every input below was already recorded per row and aggregated against
        # nothing. The summary had two shapes of breakdown — EX per bucket
        # (``by_difficulty``, ``by_gold_rank``) and bare counts
        # (``by_semantic_assurance``, ``by_tier``) — and the counts landed on exactly
        # the slices a reader needs a rate for.
        #
        # These are all within-arm, so they cost no extra serve and apply retroactively
        # to any generations file. They give a partial answer to the question the
        # placebo and mask-ablation arms exist to isolate: which part of the corpus is
        # doing the work.
        #
        # Calibration of the two-axis stamp. ``by_semantic_assurance`` says how many
        # turns were ``unflagged``; it never said whether those turns were more often
        # right. That is the whole claim of the stamp, and ``analyst.md`` calls the
        # tiers uncalibrated heuristics to be tuned in eval — this is the number that
        # tunes them. If ``unflagged`` does not out-score ``heuristic``, the stamp is
        # decoration. Both blocks exclude unstamped rows (see ``_ex_by_stamp``).
        "ex_by_semantic_assurance": (
            {} if nested else _ex_by_stamp(rows, "semantic_assurance")
        ),
        "ex_by_tier": {} if nested else _ex_by_stamp(rows, "tier"),
        # Does the suspect caveat stop the model reaching for the decoy? Conditioned on
        # DELIVERY, matching ``decoy_touch_rate``'s denominator. Unconditioned, the rate
        # cannot separate "the caveat worked" from "the model never wanted that column".
        # Within one arm a stratum is often empty (baseline injects no caveats at all),
        # so ``None`` here is routine and means exactly that — read it across arms.
        "decoy_touch_by_caveat": (
            {}
            if nested
            else _split(produced, _positive("n_caveats_injected"), "decoy_touch")
        ),
        # Do injected notes help? ADR 0003's claim, never scored. ``share_with_a_note``
        # reported the injection rate and stopped there.
        "ex_by_note_injected": (
            {} if nested else _split(rows, _positive("n_notes_injected"), "correct")
        ),
        # Does self-repair recover correctness, or produce valid-but-wrong SQL? The
        # serve path stamps a repaired answer ``heuristic`` and never ``unflagged``;
        # that assertion has been unfalsified rather than verified. ``with`` = took more
        # than one ``run_query`` attempt.
        "ex_by_repair": (
            {}
            if nested
            else _split(
                rows,
                lambda r: (
                    None if r.get("attempts") is None else int(r["attempts"]) > 1
                ),
                "correct",
            )
        ),
        # A CEILING on guardrail-induced loss, not the loss. Blocked SQL cannot be
        # graded: grading it means executing un-guardrailed SQL, which is the one thing
        # the gateway exists to prevent. So this counts turns where a layer blocked at
        # least once AND the turn still ended wrong — an upper bound, because some of
        # those were wrong for reasons the block had nothing to do with.
        #
        # ``by_guardrail_layer`` creates a key with 0 when a layer is merely EVALUATED
        # and increments only on failure (``governance.py``: ``+ (0 if passed else 1)``),
        # so a clean turn carries all five layers at zero. "Blocked" is therefore
        # ``any(v > 0)``, never a truthiness test on the dict.
        #
        # Worth reporting because ``by_guardrail_layer`` counts blocks as though they
        # were free. For a system whose thesis is governance-by-construction, the
        # false-positive cost is the missing counterweight to safety.
        "guardrail_cost_ceiling": {} if nested else _guardrail_ceiling(rows),
        # All three are conditioned on DELIVERY (``produced``), not on every row.
        # Refusing is neither delivering nor clearing, so over all rows an arm that
        # refuses more looks like an arm that governs better; refusal behaviour is
        # ``refusal_rate``'s job.
        #
        # On the current serve path the first two are complements: only ``assemble``
        # (clears, not graded) and ``graded_delivery`` (graded, does not clear) carry
        # SQL, so their rates sum to 1 and their ladder deltas are exact negatives.
        # Both are kept for the path that delivers an answer which clears safety *and*
        # is graded; ``test_the_two_delivery_rates_are_currently_complements`` fails
        # when that arrives, which is the signal to update the runbook.
        #
        # ``n_*_observed`` is therefore ``len(produced)`` in a live run, not a
        # partial-instrumentation detector — absent legitimately means ``False`` here.
        "safety_clearance_rate": _bool_rate(produced, "safety_clearance"),
        "n_safety_clearance_observed": sum(
            1 for r in produced if r.get("safety_clearance") is not None
        ),
        # Delivered under grading semantics (D5): the turn handed back SQL
        # whose semantic assurance was below the bar rather than refusing. A rung that
        # raises EX mostly by delivering more of these is trading governance for score,
        # and that is invisible without the rate.
        "graded_delivery_rate": _bool_rate(produced, "graded_delivery"),
        "n_graded_delivery_observed": sum(
            1 for r in produced if r.get("graded_delivery") is not None
        ),
        "coverage_best_effort_rate": _bool_rate(produced, "coverage_best_effort"),
        # Its denominator was simply missing, so ``0.0`` could not be told from 0-of-1 —
        # in a block whose whole point is that a rate without its denominator is not a
        # measurement.
        "n_coverage_best_effort_observed": sum(
            1 for r in produced if r.get("coverage_best_effort") is not None
        ),
        # EX *given* the router got the schema right, both terms over routed rows.
        # Separates generation quality from routing quality.
        "cond_ex_given_routing": (n_correct_routed / n_routed_hit) if n_routed_hit else None,
        # The router is not a gate: the agent's ``search_corpus`` sees the pooled corpus,
        # so a turn can license a table from a schema the router excluded. These make that
        # visible, and they are the reason ``routing_recall`` and ``cond_ex_given_routing``
        # do not multiply out to EX.
        #
        # ``None`` at an empty denominator, as everywhere else here: no row was in a
        # position to escape.
        # Which channel produced each ranking, and how often it was the degraded one.
        # Emitted from inside `summarise_rows` rather than injected by the driver
        # afterwards: the register's "declared fields the summary never emits" test
        # only sees this function, so a driver-side injection is declared, emitted,
        # and unguarded -- and a second injector under the same names with a
        # different denominator would silently win.
        **routing_channel_counts(rows),
        "n_routing_escape_observed": len(escape_rows),
        "n_routing_escaped": n_routing_escaped,
        "routing_escape_rate": (
            (n_routing_escaped / len(escape_rows)) if escape_rows else None
        ),
        # Non-empty ``tables_used`` that failed to fully resolve. Not in the escape-rate
        # denominator (unknown ≠ observed escape); counted so unresolved ids cannot
        # silently look like "nothing to judge".
        "n_routing_escape_unknown": n_routing_escape_unknown,
        "n_tables_used_unresolved": n_tables_used_unresolved,
        # Correct answers that used a schema the router had excluded — the population that
        # breaks the decomposition, since they are wins the router did not enable.
        "n_correct_via_routing_escape": n_correct_escaped,
        # Correct answers on questions the router missed. Normally 0; when it is not,
        # EX != routing_recall x cond_ex_given_routing, and this is the discrepancy.
        "n_correct_unrouted": n_correct_unrouted,
        # Correct answers on turns that had no routing decision at all. Non-zero here
        # is why EX can exceed ``routing_recall * cond_ex_given_routing`` without
        # ``n_correct_unrouted`` firing: those two terms are computed over routed rows
        # only, and EX is computed over all of them.
        "n_correct_bypassed": n_correct_bypassed,
        # The remaining two terms of the identity. ``n_correct_routed`` was the one
        # the decomposition is built on and it was never written to the artifact, so
        # `docs/measurement.md` told the reader to check a sum they could not compute.
        # All five are counted over disjoint populations and must total ``n_correct``;
        # ``n_correct_unaccounted`` is that check, published rather than asserted so a
        # future exclusion shows up as a non-zero number instead of a crash at the end
        # of a paid run.
        "n_correct_routed": n_correct_routed,
        "n_correct_routing_unrecorded": n_correct_routing_unrecorded,
        "n_correct_routing_crashed": n_correct_routing_crashed,
        "n_correct_unaccounted": n_correct
        - (
            n_correct_routed
            + n_correct_unrouted
            + n_correct_bypassed
            + n_correct_routing_unrecorded
            + n_correct_routing_crashed
        ),
        # Turns excluded from every routing metric because they crashed. Distinct from
        # ``n_crashed``: a crash on a bypassed turn is not in this count.
        "n_routing_crashed": n_routing_crashed,
        # Single-schema pick accuracy (only when schema_route_llm_pick is on).
        "schema_pick_accuracy": (n_pick_hit / len(picks)) if picks else None,
        # Rows with SQL but no gold hash to compare it to. They score ``correct=False``
        # and stay in every denominator on purpose: the gap is identical across arms
        # (``gold_hashes`` and ``pairs`` are shared), so deltas are unaffected, and
        # excluding them here alone would make this ``ex_gradeable`` disagree with
        # ``analysis.py``'s. Read it as the size of the understatement in absolute EX.
        "n_missing_gold": n_missing_gold,
        "n_gold_unusable": n_gold_unusable,
        # Wrong answer, right row count: the projection / ordering / formatting
        # class. Sizes how much of the remaining gap is a grading-contract artifact
        # rather than a semantic error, which is the difference between fixing the
        # generator and changing the grader.
        "n_wrong_but_nrows_match": sum(
            1 for r in rows if not r.get("correct") and r.get("nrows_match")
        ),
        # Correct answers that are grading free passes (Audit E2). Empty-gold
        # matches, no-FROM predictions, and (when table sets are available)
        # zero table overlap — visible so an arm that over-filters into free
        # passes cannot look identical to one that actually got the SQL right.
        **free_pass_counts(rows, gold=gold),
        # Delivery: did the curated corpus actually reach the prompt? A curation arm
        # whose notes never arrive is indistinguishable from one whose notes are
        # useless unless this is recorded.
        "mean_notes_injected": _mean(rows, "n_notes_injected"),
        # Over the rows that actually recorded note injection, not over every row: a
        # row that never measured it is not a row that measured zero, and treating
        # it as one reports "the notes never reached the prompt" next to a
        # ``mean_notes_injected`` of null.
        "share_with_a_note": (
            (
                sum(1 for r in _measured_notes if (r.get("n_notes_injected") or 0) > 0)
                / len(_measured_notes)
            )
            if _measured_notes
            else None
        ),
        # ...and how many rows that actually is. The rate is declared over all scored
        # rows and computed over the measured ones, so without this the exclusion is
        # invisible: an arm that recorded injection on three rows out of two thousand
        # publishes a share indistinguishable from one measured over the whole arm.
        # The denominator, not a flag — ``n - n_notes_observed`` is the size of the
        # instrumentation gap, and that is the number worth reading.
        "n_notes_observed": len(_measured_notes),
        "mean_few_shots_injected": _mean(rows, "n_few_shots_injected"),
        "mean_context_chars": _mean(rows, "context_chars"),
        # What this arm actually handed the model, as an identity rather than a
        # size. Compared across arms by ``eval.treatment``: two arms whose corpora
        # differ but whose prompts do not are one experiment run twice, and their
        # difference is nondeterminism. Two separate interventions on this project
        # were reported as measured nulls before anyone checked this.
        # ``corpus_note_assets`` is a whole-corpus count with no per-db decomposition,
        # so the nested block drops the key rather than reporting it as ``None``.
        # ``None`` in this module means "not verified", and a field that reads
        # unverified when it is simply not applicable is the ambiguity
        # ``eval/treatment.py`` exists to remove.
        "treatment": _drop_keys(
            fingerprint_arm(arm, rows, corpus_note_assets=corpus_note_assets).to_dict(),
            ("corpus_note_assets", "note_injection_rate") if nested else (),
        ),
        # Where the WRONG answers went wrong. Outcome buckets above cover turns
        # that refused, capped or crashed; this covers the much larger population
        # that answered and answered incorrectly, which otherwise carries no stage
        # at all. ``None`` when no gold was supplied — not an empty dict, which
        # would assert that nothing was miscategorised.
        "errors": (
            summarise_attributions(
                attribute_rows(rows, gold, shortlists=_shortlists_from_rows(rows))
            )
            if gold
            else None
        ),
        "mean_attempts": _mean(rows, "attempts"),
        "mean_ledger_len": _mean(rows, "ledger_len"),
        # Tool calls by name and guardrail decisions by layer. ``search_corpus`` /
        # ``inspect_schema`` dominate a turn and had no durable count anywhere, so
        # "which layer blocks most" and "how much exploring did this arm do" were
        # unanswerable from the artifacts.
        "tool_calls": _sum_counters(rows, "n_tool_calls"),
        "by_guardrail_layer": _sum_counters(rows, "by_guardrail_layer"),
        # Cost and wall-clock, deliberately nested and kept OUT of the scored
        # fields above. Widening the picker context raises input tokens on every
        # question, so a total is needed — but latency is scheduler-dependent by
        # design, and a serial run and a pooled run must still agree on every
        # number that is a *result* (docs/measurement.md).
        # Shared with ``eval.harness`` so a measured 0.0 stays 0.0 rather than
        # collapsing into "not measured".
        "cost": _cost_block(rows),
        # Picks that are really the logged rank-1 fallback after an LLM failure or
        # an unparseable reply. They are indistinguishable from genuine picks in
        # `schema_pick_accuracy`, so the count is reported alongside it.
        "n_pick_fallback": sum(1 for r in rows if r.get("schema_pick_fallback")),
        # ...and the same accuracy with those rows removed from BOTH numerator and
        # denominator, so the model's real pick rate needs no hand subtraction.
        "schema_pick_accuracy_excl_fallback": (
            sum(1 for r in genuine_picks if r.get("pick_hit")) / len(genuine_picks)
            if genuine_picks
            else None
        ),
        "by_difficulty": (
            {}
            if nested
            else {k: v["ex_lenient"] for k, v in _bucket("difficulty").items()}
        ),
        # ~85% of this dataset's rows carry no difficulty, so ``by_difficulty``
        # collapses into one "unknown" bucket. Without this count that reads as a
        # uniform distribution across difficulties instead of an empty measurement.
        "n_with_difficulty": sum(
            1 for r in rows if r.get("difficulty") not in (None, "", "unknown")
        ),
        # Per-database diagnosis, not just per-database EX: the cluster sign test
        # reports which databases moved, so the artifact has to be able to answer why.
        # It is the same function over a subset of the same rows, so a per-db figure
        # cannot disagree with the run-wide one by construction.
        #
        # One caution when rolling up: a *conditional* rate weights by its own
        # denominator, not by ``n`` (``cond_ex_given_routing`` by routed rows,
        # ``schema_pick_accuracy`` by rows with a pick), so the pooled figure is not
        # the ``n``-weighted mean of the per-db ones. ``nested`` stops the recursion.
        # How WIDE the schemas behind these rows are. At the top level this is the
        # whole pool the arm was scored over; inside a ``by_db`` block it is that one
        # schema, which is where the wide-table hypothesis is actually testable —
        # pooled, schema difficulty confounds it (see
        # ``docs/plans/luna-max-routing-experiment.md`` §0.5: the pooled EX curve falls
        # 26pp with gold-table width, and within-schema the same split is p=0.23).
        #
        # ``None``, not 0, when no census was passed: a schema with no tables and a
        # summary computed off an archived generations file with no corpus to hand must
        # not read the same.
        "n_tables": width["n_tables"],
        "n_columns": width["n_columns"],
        "max_table_columns": width["max_table_columns"],
        "by_db": (
            {}
            if nested
            else {
                db: summarise_rows(
                    arm,
                    db_rows,
                    gold=gold,
                    schema_widths=schema_widths,
                    nested=True,
                )
                for db, db_rows in sorted(
                    _group_by(rows, "db_id").items()
                )
            }
        ),
        # EX by the true schema's rank in the embedding shortlist: separates "the
        # picker overrode a correct rank-1" from "retrieval never surfaced it".
        # Delegated so summary.json and the offline report cannot diverge (the
        # local bucket helper sorts keys as strings: 1, 10, 2, ...).
        # Uncrashed rows, for the same reason as ``picks`` above: a crashed turn's
        # shortlist says nothing about retrieval, and the bucket it lands in is read
        # as a spending decision (``docs/prompt-experiments.md``).
        "by_gold_rank": {} if nested else rank_report(uncrashed),
    }
