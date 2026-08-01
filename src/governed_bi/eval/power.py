"""How big an effect this experiment could see, and whether what it saw was one.

The serve path is not deterministic and cannot be made so: the model sits behind a
proxy that drops the temperature parameter, so sampling noise is a fixed cost of
the setup rather than a knob. Re-running one arm against itself on the last full
benchmark moved 135 of 2030 questions (RETIRED figure; see docs/measurement.md) —
individual answers flip constantly even
though the headline rate barely moves.

That has a consequence the previous round of reporting missed. An arm comparison
that comes back "+5 questions, not significant" is not evidence that the
intervention does nothing. It is evidence that the experiment could not have
detected it either way: at that discordance an effect needs roughly six times that
size before a 2030-question run can distinguish it from noise. Reporting the null
as a finding — and building a roadmap on it — is the error, and it is invisible
unless the run states its own resolution.

So this module supplies three things, all computed from artifacts:

**A measured floor.** Since the noise cannot be reduced, it is measured, by serving
one arm twice and counting how much it disagrees with itself. That number is a
property of the run, not a constant, so it is re-measured rather than remembered.

**A minimum detectable effect.** From the floor and the question count, the
smallest true difference the run could resolve at conventional confidence. Reported
*before* the deltas, so a reader knows what the run was capable of seeing before
being shown what it saw.

**Paired tests.** Every comparison is McNemar on the same questions, never a
difference of marginal rates. Pairing cancels question difficulty, which is by far
the largest source of variance on a benchmark whose questions range from trivial to
unanswerable — it recovers much of the power the unpinnable temperature costs. The
exact binomial form is used rather than the chi-square approximation because the
discordant counts here are often small enough for the approximation to mislead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "McNemarResult",
    "NoiseFloor",
    "DetectableEffect",
    "mcnemar",
    "measure_floor",
    "minimum_detectable_effect",
    "detectable_effect_for",
    "correct_by_question",
]

_NORMAL = NormalDist()

#: Above this many discordant pairs the exact binomial sum costs more than it is
#: worth and the normal approximation is accurate to well past the digits reported.
_EXACT_LIMIT = 4000


@dataclass(frozen=True)
class McNemarResult:
    """A paired comparison of two arms on the questions they both answered."""

    arm_a: str
    arm_b: str
    n_shared: int
    #: Correct in b, wrong in a — questions b gained.
    n_b_only: int
    #: Correct in a, wrong in b — questions b lost.
    n_a_only: int
    p_value: float

    @property
    def n_discordant(self) -> int:
        return self.n_a_only + self.n_b_only

    @property
    def net(self) -> int:
        """Signed questions gained by ``arm_b`` over ``arm_a``."""
        return self.n_b_only - self.n_a_only

    @property
    def net_rate(self) -> float | None:
        if not self.n_shared:
            return None
        return self.net / self.n_shared

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_a": self.arm_a,
            "arm_b": self.arm_b,
            "n_shared": self.n_shared,
            "n_a_only": self.n_a_only,
            "n_b_only": self.n_b_only,
            "n_discordant": self.n_discordant,
            "net_questions": self.net,
            "net_rate": self.net_rate,
            "p_value": self.p_value,
        }


@dataclass(frozen=True)
class NoiseFloor:
    """Run-to-run disagreement of the pipeline with itself.

    ``net`` should sit near zero for a true replicate — the same arm does not get
    better by being run again. A large ``net`` means the two runs were not actually
    the same configuration, and the floor derived from them is not a floor.

    **What kind of noise this is, and what it therefore bounds.** ``source`` defaults to
    ``"serve_replicate"``: the same *corpus* served a second time, so it measures
    serve-side sampling — decoding, the LLM schema pick, tool-call ordering. It does not
    measure variance in the corpus itself, and on this ladder the corpus *is* the
    treatment: each ``(arm, db)`` corpus is one draw from a stochastic curator agent,
    n=1. So a delta that clears the minimum detectable effect derived from this floor has
    cleared serve noise and says nothing about whether a second curator run on the same
    schema would have produced the same corpus.
    ``docs/plans/experiment-runbook.md`` states this beside the checklist, because a
    reader who takes the MDE as applying to the curation treatment will over-claim.
    """

    n_pairs: int
    n_discordant: int
    net: int
    source: str = "serve_replicate"

    @property
    def discordance_rate(self) -> float | None:
        if not self.n_pairs:
            return None
        return self.n_discordant / self.n_pairs

    @property
    def suspect(self) -> bool:
        """True when the replicate drifted enough that it is not measuring noise."""
        if not self.n_discordant:
            return False
        return abs(self.net) > 2.0 * math.sqrt(self.n_discordant)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_pairs": self.n_pairs,
            "n_discordant": self.n_discordant,
            "net": self.net,
            "discordance_rate": self.discordance_rate,
            "source": self.source,
            "suspect_not_a_replicate": self.suspect,
        }


@dataclass(frozen=True)
class DetectableEffect:
    """The smallest true effect this run could have resolved."""

    n_pairs: int
    discordance_rate: float
    alpha: float
    power: float
    #: ``None`` when there was nothing to measure. Not ``inf``: this lands in
    #: ``summary.json`` via :meth:`to_dict`, and ``json.dumps`` renders a float
    #: infinity as the bare token ``Infinity``, which is not valid JSON and which any
    #: strict reader (jq, another language's parser) rejects — corrupting the whole
    #: artifact over one unmeasured field. ``None`` is valid, and it is the same
    #: "unmeasured is not zero" convention every other rate in this harness follows.
    questions: float | None
    #: Same thing expressed in EX points, which is how results get quoted.
    rate: float | None
    #: True when the replicate observed *zero* discordant pairs and the effect above
    #: is a rule-of-three upper bound rather than a measurement. See
    #: :func:`minimum_detectable_effect`.
    from_zero_discordance: bool = False
    #: False when there was nothing to measure at all (no paired questions). Then
    #: ``resolves`` is unknowable and answers ``False``, because "we could not tell"
    #: must never read as "yes".
    measured: bool = True
    #: How many paired questions the *replicate* that supplied ``discordance_rate``
    #: actually covered. ``None`` when the rate did not come from a replicate.
    #:
    #: This exists because the rate and the population are two different sample sizes
    #: and only one of them used to be recorded. ``n_pairs`` is the population the
    #: effect is measured over; a cheaper replicate estimates the same rate on fewer
    #: questions. Nothing in the artifact said which, so a reader could not tell a floor
    #: measured on 1351 questions from one measured on 200.
    floor_n_pairs: int | None = None

    def resolves(self, net_questions: int) -> bool:
        if not self.measured or self.questions is None:
            return False
        return abs(net_questions) >= self.questions

    def verdict(self, net_questions: int) -> str:
        if not self.measured:
            return (
                "resolution unknown: no paired questions, so this run cannot say "
                "what size of effect it was able to see"
            )
        if self.resolves(net_questions):
            if self.from_zero_discordance:
                return (
                    "resolvable against a rule-of-three bound: the replicate showed "
                    "no disagreement at all, so the floor is the most noise that "
                    "could hide behind that, not a measured one"
                )
            return "resolvable"
        return (
            f"below resolution: {abs(net_questions)} questions vs a minimum "
            f"detectable {self.questions:.0f} — this run cannot tell this "
            f"difference from sampling noise in either direction"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_pairs": self.n_pairs,
            "discordance_rate": self.discordance_rate,
            "alpha": self.alpha,
            "power": self.power,
            "mde_questions": self.questions,
            "mde_rate": self.rate,
            "from_zero_discordance": self.from_zero_discordance,
            "measured": self.measured,
            "floor_n_pairs": self.floor_n_pairs,
            # How much of this comparison's population the floor was measured on. 1.0 is
            # a full replicate; 0.22 is a 300-question subsample against a 1351-question
            # split. The MDE is unbiased either way — it is evaluated at ``n_pairs``, not
            # at the floor's size — but the *rate* it rests on is noisier the smaller
            # this is, and a reader has to be able to see that without opening the run.
            "floor_coverage": (
                None
                if not self.floor_n_pairs or not self.n_pairs
                else round(self.floor_n_pairs / self.n_pairs, 4)
            ),
        }


def correct_by_question(rows: Iterable[Mapping[str, Any]]) -> dict[str, bool]:
    """``{question_id: correct}``, the only projection the paired tests need.

    A repeated question id is fatal, matching
    :mod:`governed_bi.eval.analysis`'s guard on the same input. Last-write-wins
    would silently move a p-value in an unknown direction, and the way a duplicate
    arises — a resumed run that appended a question it had already scored — is
    common enough that the quiet version is worse than the loud one. This path is
    the one the live driver uses, so it should not be the laxer of the two.
    """
    out: dict[str, bool] = {}
    dupes: set[str] = set()
    for row in rows:
        qid = row.get("question_id") or row.get("request_id")
        if qid is None:
            continue
        key = str(qid)
        if key in out:
            dupes.add(key)
        out[key] = bool(row.get("correct"))
    if dupes:
        sample = sorted(dupes)[:5]
        raise ValueError(
            f"{len(dupes)} duplicate question id(s) in the scored rows "
            f"(e.g. {sample}); the generations file is corrupt and any paired test "
            "over it is meaningless"
        )
    return out


def _binomial_two_sided(k: int, n: int) -> float:
    """Exact two-sided p for ``k`` successes of ``n`` under p=0.5.

    The denominator is kept as an exact integer. ``2 ** n`` and ``1 << n`` are the
    same arbitrary-precision int in Python and neither overflows; what would
    overflow is a *float* power (``2.0 ** n``, or ``float(2 ** n)``) above n=1024.
    CPython's int true-division is correctly rounded and the quotient is always in
    [0, 1], so this is safe at any n we will see.

    Worth stating precisely, because an earlier version of this comment claimed
    ``2 ** n`` itself overflows. It does not, and the sibling implementation in
    :mod:`governed_bi.eval.analysis` writes it that way perfectly safely — the
    wrong comment here produced a bug report against correct code over there.
    """
    if n <= 0:
        return 1.0
    k = min(k, n - k)
    if n > _EXACT_LIMIT:
        # Normal approximation with continuity correction.
        z = (abs(n - 2 * k) - 1) / math.sqrt(n)
        return max(0.0, min(1.0, 2.0 * (1.0 - _NORMAL.cdf(z))))
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2.0 * (tail / (1 << n)))


def mcnemar(
    arm_a: str,
    a_correct: Mapping[str, bool],
    arm_b: str,
    b_correct: Mapping[str, bool],
) -> McNemarResult:
    """Exact paired test over the questions both arms answered.

    Only shared question ids participate. An arm that skipped a question has no
    opinion on it, and filling the gap with "wrong" would score a missing row as a
    failure — the precise conflation that made a previous set of numbers unusable.
    """
    shared = set(a_correct) & set(b_correct)
    n_a_only = sum(1 for q in shared if a_correct[q] and not b_correct[q])
    n_b_only = sum(1 for q in shared if b_correct[q] and not a_correct[q])
    p = _binomial_two_sided(min(n_a_only, n_b_only), n_a_only + n_b_only)
    return McNemarResult(
        arm_a=arm_a,
        arm_b=arm_b,
        n_shared=len(shared),
        n_a_only=n_a_only,
        n_b_only=n_b_only,
        p_value=p,
    )


def measure_floor(
    first: Mapping[str, bool],
    second: Mapping[str, bool],
    *,
    source: str = "serve_replicate",
) -> NoiseFloor:
    """Disagreement between two serves of the *same* corpus.

    Serve-side only — see :class:`NoiseFloor` for why that matters on a ladder whose
    treatment is the corpus.
    """
    shared = set(first) & set(second)
    a_only = sum(1 for q in shared if first[q] and not second[q])
    b_only = sum(1 for q in shared if second[q] and not first[q])
    return NoiseFloor(
        n_pairs=len(shared),
        n_discordant=a_only + b_only,
        net=b_only - a_only,
        source=source,
    )


def minimum_detectable_effect(
    n_pairs: int,
    discordance_rate: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> DetectableEffect:
    """Smallest net question difference this run could call significant.

    Derived from the McNemar statistic's null distribution: with ``d`` discordant
    pairs the net difference has standard deviation ``sqrt(d)`` under the null, so a
    detectable effect must clear ``(z_alpha/2 + z_beta) * sqrt(d)``. The result is
    reported in questions and as a rate, because a rate alone hides how few
    questions a "significant" difference can rest on.

    **Zero observed discordance is not zero noise.** This used to return
    ``questions=0.0`` when the replicate happened to agree with itself everywhere,
    which made ``resolves()`` true for *any* effect — including no effect at all.
    That inverts the module's entire purpose, and it bites hardest exactly where it
    is most dangerous: a small run, where zero disagreements is unremarkable. Zero
    events in ``n`` trials bounds the rate at roughly ``3/n`` (the rule of three), so
    the floor falls back to three discordant pairs and flags itself as a bound rather
    than a measurement. With no paired questions at all there is nothing to bound and
    the result is marked unmeasured.
    """
    n_pairs = max(0, int(n_pairs))
    discordance_rate = max(0.0, min(1.0, float(discordance_rate)))
    if n_pairs == 0:
        return DetectableEffect(
            n_pairs=0,
            discordance_rate=discordance_rate,
            alpha=alpha,
            power=power,
            questions=None,
            rate=None,
            measured=False,
        )
    discordant = n_pairs * discordance_rate
    from_zero = discordant <= 0
    if from_zero:
        # Rule of three: with 0 discordant pairs observed, the 95% upper bound on the
        # rate is ~3/n_pairs, i.e. ~3 pairs. Conservative and finite.
        discordant = min(3.0, float(n_pairs))
    z_alpha = _NORMAL.inv_cdf(1.0 - alpha / 2.0)
    z_beta = _NORMAL.inv_cdf(power)
    questions = (z_alpha + z_beta) * math.sqrt(discordant)
    return DetectableEffect(
        n_pairs=n_pairs,
        discordance_rate=discordance_rate,
        alpha=alpha,
        power=power,
        questions=questions,
        # Clipped at 1.0. A "minimum detectable *rate*" above 1 has no reading — it
        # says the smallest effect the run could see is more than every question it
        # asked. Reachable only on a tiny replicate (n_pairs of 1 or 2 against the
        # rule-of-three floor), where the honest statement is "this run can resolve
        # nothing", and 1.0 says that.
        rate=min(1.0, questions / n_pairs),
        from_zero_discordance=from_zero,
    )


def detectable_effect_for(
    result: McNemarResult,
    floor: NoiseFloor,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> DetectableEffect | None:
    """The MDE for ONE comparison: the replicate's *rate*, this pair's *population*.

    The two inputs come from different places and that distinction was collapsed. The
    caller used to build a single ``minimum_detectable_effect(floor.n_pairs,
    floor.discordance_rate)`` and hand the same object to every comparison, so the MDE
    was evaluated at the size of the **replicate** while ``resolves()`` compared it
    against a ``net_questions`` counted over the size of the **comparison**.

    With a full-split replicate the two happen to be the same number and nothing shows.
    They stop being the same the moment anyone economises, and the error runs the
    dangerous way: a 300-question replicate at a 10% discordance yields
    ``2.80 * sqrt(30) = 15.3`` questions, while the honest threshold for a
    1351-question comparison is ``2.80 * sqrt(135.1) = 32.6``. Every delta between
    those two numbers would have been stamped ``resolvable: true``. The same mismatch
    already applies, smaller and in the conservative direction, to the ``no_twin``
    stratum, whose 1085 shared rows were being judged against a 1351-row threshold.

    So: ``discordance_rate`` is a property of the pipeline and travels; ``n_pairs`` is a
    property of the population under test and does not. ``floor_n_pairs`` records which
    replicate the rate came from, because a rate estimated on 300 questions and one
    estimated on 1351 are not equally trustworthy and the artifact has to say so.
    """
    rate = floor.discordance_rate
    if rate is None:
        return None
    effect = minimum_detectable_effect(
        result.n_shared, rate, alpha=alpha, power=power
    )
    return replace(effect, floor_n_pairs=floor.n_pairs)


def comparison_report(
    result: McNemarResult,
    mde: DetectableEffect | None,
    floor: NoiseFloor | None = None,
) -> dict[str, Any]:
    """One arm comparison, with the run's resolution stated beside the effect.

    The ordering is deliberate: ``mde`` and ``floor`` sit next to ``net_questions``
    in the artifact so a delta cannot be read without the context that says whether
    it means anything.
    """
    out = result.to_dict()
    out["noise_floor"] = floor.to_dict() if floor else None
    out["detectable"] = mde.to_dict() if mde else None
    if mde is not None:
        out["resolvable"] = mde.resolves(result.net)
        out["reading"] = mde.verdict(result.net)
    else:
        out["resolvable"] = None
        out["reading"] = (
            "no noise floor measured for this run — significance is reported without "
            "knowing what the run could resolve; replicate an arm to fix this"
        )
    return out


def cluster_sign_test(
    a_correct: Mapping[str, bool],
    b_correct: Mapping[str, bool],
    db_by_question: Mapping[str, str],
) -> dict[str, Any]:
    """A paired test over DATABASES, not questions.

    The question-level McNemar treats 2000 questions as 2000 independent
    observations. They are not: they are nested in ~69 databases of wildly different
    difficulty and schema shape, and a corpus change that happens to suit five of
    them produces a hundred correlated "wins". That is textbook pseudoreplication,
    and it makes the question-level p-value anticonservative by an unknown factor.

    The cheap, assumption-light correction is to move the unit of analysis up: score
    each db by how many questions each arm got right, and ask how many dbs improved
    versus regressed. That is an exact sign test on the cluster, and it cannot be
    inflated by one easy schema contributing a hundred rows. It is deliberately
    *less* powerful than the question-level test — that is the point; the extra power
    the question-level test appears to have is largely borrowed against an
    independence assumption the data does not support.

    Report both. Agreement is reassuring; a question-level "win" that the cluster
    test cannot see is a result resting on a handful of databases, and the honest
    move is to name them.
    """
    shared = set(a_correct) & set(b_correct)
    per_db: dict[str, list[int]] = {}
    for q in shared:
        db = str(db_by_question.get(q) or "")
        if not db:
            continue
        slot = per_db.setdefault(db, [0, 0])
        slot[0] += 1 if a_correct[q] else 0
        slot[1] += 1 if b_correct[q] else 0

    better = sorted(db for db, (a, b) in per_db.items() if b > a)
    worse = sorted(db for db, (a, b) in per_db.items() if b < a)
    tied = sorted(db for db, (a, b) in per_db.items() if a == b)
    n_eff = len(better) + len(worse)
    # Two different situations collapse into ``n_eff == 0`` and they need different
    # answers. Every database TIED is a real measurement whose result is "no
    # difference", so ``1.0`` is honest. But no database mapped at all — ``per_db``
    # empty, because ``db_by_question`` covered none of the shared questions — is not
    # a measurement, and reporting ``1.0`` there hands the runbook's "the cluster block
    # agrees" checklist item a p-value derived from nothing.
    if not per_db:
        p = None
    elif not n_eff:
        p = 1.0
    else:
        p = _binomial_two_sided(min(len(better), len(worse)), n_eff)
    return {
        "n_dbs": len(per_db),
        "n_dbs_better": len(better),
        "n_dbs_worse": len(worse),
        "n_dbs_tied": len(tied),
        "p_value": p,
        "dbs_better": better[:20],
        "dbs_worse": worse[:20],
        "reading": (
            "no question could be attributed to a database, so nothing was tested"
            if not per_db
            else "no database-level difference to test"
            if not n_eff
            else (
                f"{len(better)} of {n_eff} databases improved (p={p:.4g}); "
                "treats each database as one observation, so a question-level win "
                "this test cannot see is carried by a few schemas"
            )
        ),
    }


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm-Bonferroni step-down adjustment, order preserved.

    A run with four arms produces six pairwise tests. At a nominal 0.05 each, the
    chance of at least one false positive across the family is about 26%, and the
    harness previously reported all six as if each stood alone. Holm controls the
    family-wise error rate, is uniformly more powerful than plain Bonferroni, and
    needs no independence assumption — which matters here because the six
    comparisons share arms and are anything but independent.

    Adjusted values are monotone non-decreasing in the sorted order and clipped at
    1.0, so ``p_adj <= alpha`` is the decision rule.
    """
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p_values[i])
    out = [0.0] * n
    running = 0.0
    for rank, idx in enumerate(order):
        scaled = (n - rank) * p_values[idx]
        running = max(running, min(1.0, scaled))
        out[idx] = running
    return out
