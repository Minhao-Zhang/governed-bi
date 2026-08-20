"""Quotability gates keyed on ``register.record.GATE_CONDITIONS``.

Three verdicts: pass / fail / cannot_evaluate. Missing fields never pass.
Refuse the comparison; do not warn (ADR 0005 §4.1). Import assert closes the
declared↔implemented sets.
"""


from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Mapping

from ..register.knobs import resume_drift_keys
from ..register.quantity import Measured
from ..register.record import GATE_CONDITIONS, RECORD_REGISTER, Absence
from ..register.stages import Outcome, Stage
from .degradation import channel_anomalies
from .population import Population, TurnRow

__all__ = ["Verdict", "GateResult", "GATE_IMPLEMENTATIONS", "evaluate", "quotable"]


class Verdict(str, Enum):
    """Three-valued on purpose; see the module docstring."""

    #: The condition held over a population large enough to have failed.
    passed = "pass"
    #: The condition was violated.
    failed = "fail"
    #: The inputs were not there. **Not a pass.** A comparison with a
    #: ``cannot_evaluate`` gate is not quotable, because the check did not happen.
    cannot_evaluate = "cannot_evaluate"


@dataclass(frozen=True)
class GateResult:
    """One gate's outcome, with everything needed to audit it."""

    field: str
    condition: str
    verdict: Verdict
    observed: Measured[float]
    population: str
    detail: str = ""

    def render(self) -> str:
        return (
            f"[{self.verdict.value:16s}] {self.field:22s} {self.observed.render(4)} "
            f"over {self.population}"
            + (f" -- {self.detail}" if self.detail else "")
        )


#: A gate takes the arm's turn records and returns its result.
GateFn = Callable[[Population], GateResult]


def _result(
    field: str,
    verdict: Verdict,
    observed: Measured[float],
    population: Population,
    detail: str = "",
) -> GateResult:
    return GateResult(
        field=field,
        condition=GATE_CONDITIONS[field],
        verdict=verdict,
        observed=observed,
        population=population.describe(),
        detail=detail,
    )


def _zero_count_gate(field: str, counter: str) -> GateFn:
    """A gate of the form "``counter`` is zero across the arm".

    Factored because most gates have this shape, and each hand-written copy is another
    chance to invert the comparison.
    """

    def gate(arm: Population) -> GateResult:
        counted = arm.count(counter)
        if not counted.is_measured:
            return _result(
                field,
                Verdict.cannot_evaluate,
                Measured.unmeasured(counted.why),
                arm,
                f"{counter!r} was not recorded on every turn, so this gate did not run",
            )
        rate = arm.rate(counter)
        if counted.value > 0:
            return _result(
                field, Verdict.failed, rate, arm, f"{counted.value} turn(s) with {counter!r}"
            )
        return _result(field, Verdict.passed, rate, arm)

    return gate


def _outcome_gate(arm: Population) -> GateResult:
    """No turn classified ``crashed``.

    A crash counted as a refusal contaminates every arm-to-arm delta by a *different*
    amount, since arms do not crash at the same rate. Gated on the classification, never
    on an error string.
    """
    return _zero_count_gate("outcome", "crashed")(arm)


def _facet_channels_gate(arm: Population) -> GateResult:
    """No channel state differs from its declared expectation — on turns that ran.

    Not a ``_zero_count_gate``: ``facet_channels`` is stage-conditional (a guard-blocked
    turn never runs the fan-out), so under a naive rate an empty ``facet_channels`` reads
    as "no channel differed" on a turn where no channel ran. The denominator is therefore
    turns where the fan-out ran, published; zero such turns is
    :attr:`Verdict.cannot_evaluate`, never a pass.
    """
    ran = arm.restrict(lambda r: r.get("facet_channels") not in (None, [], {}), "fan-out ran")
    if ran.n == 0:
        return _result(
            "facet_channels",
            Verdict.cannot_evaluate,
            Measured.unmeasured(f"no turn in {arm.describe()} ran the fan-out"),
            arm,
            "a degradation rate of 0 over 0 turns is not a pass",
        )
    result = _zero_count_gate("facet_channels", "facet_degraded")(ran)
    if result.verdict is not Verdict.failed:
        return result
    return replace(result, detail=f"{result.detail}; {_drift(ran)}")


def _drift(arm: Population) -> str:
    """Which facet's which channel differed, for a gate that has already failed.

    Naming only — the verdict comes from the stamped ``facet_degraded`` counter. Judged
    through :func:`~.degradation.channel_anomalies`, the same function ``serve.stamp``
    decides the counter with, so this cannot disagree with the record it reports on.
    ``extra_channel`` is included: it is drift, and it did not refuse this run.
    """
    seen: dict[str, int] = {}
    unjudgeable = 0
    why = ""
    for row in arm.rows:
        try:
            anomalies = channel_anomalies(row.get("facet_channels"))
        except ValueError as err:
            unjudgeable += 1
            why = why or str(err)
            continue
        for key, anomaly in anomalies.items():
            seen[f"{key}={anomaly}"] = seen.get(f"{key}={anomaly}", 0) + 1
    parts = [f"{key} on {count} turn(s)" for key, count in sorted(seen.items())]
    if unjudgeable:
        parts.append(
            f"{unjudgeable} turn(s) whose facet_channels could not be judged against the "
            f"register, so their drift is not named here: {why}"
        )
    return "; ".join(parts) if parts else "no channel state differs from its declaration"


def _context_hash_gate(arm: Population) -> GateResult:
    """The delivery gate's single-arm half: every turn carries a ``context_hash``. L-R2.

    On ``context_hash`` and not ``delivery_hash``, because the latter depends on which tool
    calls the model chose, conflating "the treatment differs" with "the model behaved
    differently".

    **This gate does not test that the treatment differed, and nothing does it by hash any
    more.** The cross-arm half used to be a >= 95% distinctness threshold in
    ``eval/report.context_hashes_distinct``; audit D9 retired it, because retrieval is
    nondeterministic and the hashes differ whether or not the treatment did — it passed at
    0.9993 on a pair differing only by a random seed. That function is now an existence check
    over the shared questions, and the declared treatment is judged from knobs by
    ``eval/report.knobs_comparable``. What one arm can answer is only this: a turn with no
    ``context_hash`` assembled no context, so no later comparison against it holds.
    """
    coverage = arm.coverage("context_hash")
    # Three states, not two. **Never recorded**: the arm is not instrumented, nothing to judge
    # (`cannot_evaluate`). **Recorded on some turns and not others**: the instrumentation exists
    # and is dropping turns, so the treatment cannot be identified and no later comparison
    # against this arm holds (`failed`). Collapsing the two either excuses broken
    # instrumentation or fails an arm for not having any.
    if not coverage.is_measured or coverage.value == 0.0:
        return _result(
            "context_hash",
            Verdict.cannot_evaluate,
            coverage,
            arm,
            "no turn carries a context_hash, so this arm is not instrumented for the "
            "delivery gate at all",
        )
    if coverage.value < 1.0:
        return _result(
            "context_hash",
            Verdict.failed,
            coverage,
            arm,
            "context_hash is recorded on some turns and missing on others, so the treatment "
            "this arm delivered cannot be identified and no later comparison against it can "
            "be trusted",
        )
    return _result(
        "context_hash",
        Verdict.passed,
        coverage,
        arm,
        "every turn carries a context_hash. Whether the treatment differed is a two-arm "
        "question and audit D9 retired hash distinctness as its test; "
        "eval/report.knobs_comparable judges it from the declared knobs instead",
    )


#: The **other** treatment identity, used as the witness that ``stamp`` ran on a row.
#:
#: Both hashes are declared ``Absence.never`` and owned by ``Stage.stamp``, and ``Session``
#: mints the pair together, so a row carrying neither was never stamped and a row carrying one
#: but not the other is the partial-instrumentation failure the gate exists to fail. Using the
#: gated field itself as its own denominator test would be circular — every arm would pass over
#: the rows that already have it — which is why the witness has to be a different field.
#: :func:`_assert_the_stamp_witness_is_still_a_stamp_field` holds the premise.
_STAMP_WITNESS_FIELD = "prompt_set_hash"


def _paused_before_stamp(row: TurnRow) -> bool:
    """Did this row end at ``ask_user`` **without** reaching ``stamp``?

    Two conditions, and the conjunction is the whole point. ``outcome: clarification`` has meant
    two different endings since ``ask_user`` learned to fail closed (see
    ``register/stages.Outcome``): a turn that paused and was never resumed, which no node ever
    stamped, and a turn whose reader declined or cancelled, which *is* stamped and carries every
    field ``stamp`` writes. The first must leave the corpus gate's denominator and the second
    must not, and the outcome alone cannot tell them apart.

    So the second condition asks the row directly: a turn that reached ``stamp`` names its prompt
    set, because that is written by the same node from the same ``Session`` identity as the corpus
    hash. Measured over ``runs/eval/`` on 2026-08-20: on the five instrumented artifacts every row
    with a null ``corpus_content_hash`` is a clarification whose ``prompt_set_hash`` is null too
    (6, 8, 4, 13 and 5 rows), and no other row has either null — so this predicate selects exactly
    the rows the outcome test used to, and stops selecting the one case it was wrong about. On the
    two runs of the null replicate neither key exists at all, so their clarifications drop and the
    remaining 2 688 rows keep the arms at ``cannot_evaluate``, unchanged.

    **What it still cannot see:** a stamped decline in a run that recorded *neither* identity.
    That row is dropped, and its null corpus hash with it. Every field the drop could have been
    keyed on is written by the same node in the same dict, so no row-local test separates "never
    stamped" from "stamped by a run that recorded no identity at all"; what does is
    ``register/arm_profiles.reconcile``, which the driver runs against the arm's declared digest
    before the first paid question.
    """
    return (
        str(row.get("outcome")) == Outcome.clarification.value
        and row.get(_STAMP_WITNESS_FIELD) is None
    )


def _corpus_content_hash_gate(arm: Population) -> GateResult:
    """One corpus per arm, and it is named. D7.

    ``AGENTS.md`` and the register both call the corpus the treatment identity of every
    measurement, and until 2026-08-10 no gate read it. Two consequences were live at once: an arm
    whose rows carry no corpus hash passed every gate (both runs of the designated null replicate
    are in that state, 1351/1351 null), and two arms measured over *different* corpora also
    passed, because nothing compared the field across them.

    Single-arm half only, like :func:`_context_hash_gate`: every row present and all rows equal.
    Two arms carrying *different* single hashes is the desired case for a corpus intervention and
    the disqualifying case for everything else, so which one it is cannot be decided from one arm.

    **And nothing decides it.** ``eval/report.comparison_quotable`` runs two cross-arm gates,
    ``context_hashes_distinct`` (an existence check on ``context_hash``) and
    ``knobs_comparable`` (over ``comparability_keys()``, which does not contain
    ``corpus_content_hash`` — it is a ``RecordField``, not a knob). So the second consequence
    named above, two arms measured over different corpora both passing, is still live for the
    cross-arm case; only the within-arm case is closed here. What exists instead is
    ``register/arm_profiles.reconcile``, which the driver runs before the first paid question
    and which checks one arm's rows against the digest that arm's profile declares.

    Three-valued for the same reason as ``context_hash``: an arm predating the field is not
    instrumented (``cannot_evaluate``), whereas an arm that records it on some turns and not
    others, or that changed corpus mid-run, cannot be identified (``failed``).

    **Stage-conditional, like ``facet_channels``.** ``stamp`` is what writes this field, and a
    turn that paused on ``ask_user`` and was never resumed never reaches it — so such a row
    legitimately carries no corpus hash. Judging those as missing instrumentation would fail
    every arm that ever asked a question, which is a gate nobody can keep green and therefore a
    preference rather than a gate. The denominator is turns that reached ``stamp``, published;
    zero such turns is ``cannot_evaluate``, never a pass.

    Which rows those are is :func:`_paused_before_stamp`'s question, **not**
    ``outcome == clarification``'s. This gate read the outcome alone until 2026-08-20 under the
    label "reached stamp", and that stopped being the same fact when ``ask_user`` gained a
    fail-closed decline: such a turn ends the agent loop, *reaches* ``stamp``, is stamped
    ``outcome: clarification`` and carries a full record — so the outcome test dropped a row that
    both belongs in the denominator and would have failed it if its hash were null.
    """
    field = "corpus_content_hash"
    arm = arm.restrict(
        lambda r: not _paused_before_stamp(r), "excluded turns that paused before `stamp`"
    )
    if arm.n == 0:
        return _result(
            field,
            Verdict.cannot_evaluate,
            Measured.unmeasured("every turn paused for clarification, so none reached stamp"),
            arm,
            "an arm that never finished a turn has no treatment identity to check",
        )
    coverage = arm.coverage(field)
    if not coverage.is_measured or coverage.value == 0.0:
        return _result(
            field,
            Verdict.cannot_evaluate,
            coverage,
            arm,
            "no turn names a corpus, so this arm carries no treatment identity and nothing "
            "measured against it is comparable to anything",
        )
    if coverage.value < 1.0:
        return _result(
            field,
            Verdict.failed,
            coverage,
            arm,
            "corpus_content_hash is recorded on some turns and missing on others, so which "
            "corpus this arm served is not answerable from its own rows",
        )
    distinct = {str(row.get(field)) for row in arm.rows}
    if len(distinct) > 1:
        return _result(
            field,
            Verdict.failed,
            coverage,
            arm,
            f"{len(distinct)} different corpus_content_hash values inside one arm, so the "
            "corpus changed while the arm was running and its turns are not one treatment",
        )
    return _result(
        field,
        Verdict.passed,
        coverage,
        arm,
        "every turn names the same corpus. Whether it differs from another arm's is a two-arm "
        "condition and no gate evaluates it: comparison_quotable compares context_hash and the "
        "comparability knobs, and corpus_content_hash is neither",
    )


def _knobs_resolved_gate(arm: Population) -> GateResult:
    """Every row in one arm ran under the same configuration.

    ``resume_drift_keys()`` is the set by its own definition — comparability plus operational
    plus scope, i.e. everything whose change *within one run directory* is fatal, and an arm
    is one run directory. A row resolving ``route_top_n`` to 3 beside one resolving it to 5
    is not one arm, so any rate over the pair is a rate over a population that does not exist
    (L-R3, with the filter moved into the configuration).

    Declared keys only: an undeclared key is caught by ``undeclared_keys``, and failing here
    too would refuse a run for a reason a reader would look for in the wrong place.

    Absent ``knobs_resolved`` is unmeasured, not passing — ``Absence.never`` already reports
    it as ``missing_required``, and passing here would be two gates disagreeing about one hole.
    """
    keys = resume_drift_keys()
    seen: dict[tuple[tuple[str, str], ...], int] = {}
    absent = 0
    for row in arm.rows:
        knobs = row.get("knobs_resolved")
        if not isinstance(knobs, Mapping):
            absent += 1
            continue
        # `repr`, so two runs differing in a knob's *type* (3 vs "3") are two configurations;
        # a comparison that coerced them would report drift as agreement.
        signature = tuple(sorted((k, repr(knobs.get(k))) for k in keys))
        seen[signature] = seen.get(signature, 0) + 1

    if absent:
        return _result(
            "knobs_resolved",
            Verdict.cannot_evaluate,
            Measured.unmeasured(f"{absent}/{arm.n} row(s) carry no knobs_resolved mapping"),
            arm,
            detail=(
                f"{absent} of {arm.n} rows have no knobs_resolved, so whether the arm ran under "
                "one configuration is unknown. Absence.never already reports these as missing."
            ),
        )
    if not seen:
        return _result(
            "knobs_resolved",
            Verdict.cannot_evaluate,
            Measured.unmeasured(f"{arm.label!r} is empty"),
            arm,
        )
    distinct = len(seen)
    observed = Measured.of(float(distinct))
    if distinct == 1:
        return _result("knobs_resolved", Verdict.passed, observed, arm)
    return _result(
        "knobs_resolved",
        Verdict.failed,
        observed,
        arm,
        detail=(
            f"{distinct} distinct configurations across {arm.n} rows of one arm. Every rate over "
            "this arm is a rate over a population that does not exist. Differing keys: "
            + ", ".join(sorted(_differing_keys(seen)))
        ),
    )


def _differing_keys(seen: Mapping[tuple[tuple[str, str], ...], int]) -> set[str]:
    """Which knob names actually differ, so a failure names them rather than the count."""
    per_key: dict[str, set[str]] = {}
    for signature in seen:
        for name, value in signature:
            per_key.setdefault(name, set()).add(value)
    return {name for name, values in per_key.items() if len(values) > 1}


GATE_IMPLEMENTATIONS: Mapping[str, GateFn] = {
    "outcome": _outcome_gate,
    "facet_channels": _facet_channels_gate,
    "context_hash": _context_hash_gate,
    "corpus_content_hash": _corpus_content_hash_gate,
    "knobs_resolved": _knobs_resolved_gate,
    "guardrail_errors": _zero_count_gate("guardrail_errors", "guardrail_error"),
    "negative": _zero_count_gate("negative", "negative_failed_open"),
}


def evaluate(arm: Population) -> tuple[GateResult, ...]:
    """Run every declared gate over one arm, in declaration order."""
    return tuple(GATE_IMPLEMENTATIONS[field](arm) for field in sorted(GATE_CONDITIONS))


def quotable(arm: Population) -> tuple[bool, tuple[GateResult, ...]]:
    """Whether this arm's numbers may be quoted, and every gate's result.

    Both, because a bare ``False`` hides which gate refused and a bare ``True`` hides that
    some could not run. **``cannot_evaluate`` blocks quotation**: a check that did not
    happen is not a check that passed.
    """
    results = evaluate(arm)
    return all(r.verdict is Verdict.passed for r in results), results


def _assert_every_declared_gate_is_implemented() -> None:
    """Import-time closure: declared GATE_CONDITIONS <-> GATE_IMPLEMENTATIONS."""
    declared, implemented = set(GATE_CONDITIONS), set(GATE_IMPLEMENTATIONS)
    if declared != implemented:  # pragma: no cover - import-time guard
        raise AssertionError(
            "quotability gates out of closure: declared-not-implemented "
            f"{sorted(declared - implemented)}, implemented-not-declared "
            f"{sorted(implemented - declared)}. v1 shipped eight of the former and "
            "every one of them passed."
        )


def _assert_the_stamp_witness_is_still_a_stamp_field() -> None:
    """Import-time: :data:`_STAMP_WITNESS_FIELD` and the gated field are one node's writes.

    :func:`_paused_before_stamp` drops rows from the corpus gate's denominator on the strength of
    one claim — that ``stamp`` writes both treatment identities or neither. That claim lives in
    ``register/record.py`` as a pair of ``RecordField`` declarations, so it can be read here
    instead of restated: if either field stops being ``Absence.never`` or stops being owned by
    ``Stage.stamp``, the witness witnesses nothing and the drop becomes a silent exclusion. The
    version of this rule that shipped before was a sentence in a docstring, which is how a
    stamped clarification came to be dropped from the denominator without anything noticing.
    """
    declared = {f.name: f for f in RECORD_REGISTER}
    for name in (_STAMP_WITNESS_FIELD, "corpus_content_hash"):
        field = declared.get(name)
        if field is None:  # pragma: no cover - import-time guard
            raise AssertionError(
                f"{name!r} is not in RECORD_REGISTER, so the corpus gate's denominator rests on "
                "a field the register does not declare."
            )
        if field.absence is not Absence.never or field.owner is not Stage.stamp:
            raise AssertionError(  # pragma: no cover - import-time guard
                f"{name!r} is declared owner={field.owner.value} absence={field.absence.value}; "
                "the corpus gate reads a null prompt_set_hash as 'this turn never reached "
                "stamp', which holds only while both treatment identities are written by "
                "`stamp` on every terminal path."
            )


_assert_every_declared_gate_is_implemented()
_assert_the_stamp_witness_is_still_a_stamp_field()
