"""Quotability checks keyed on ``registry.record.CHECK_CONDITIONS``.

Three results: pass / fail / cannot_evaluate. Missing fields never pass.
Refuse the comparison; do not warn (ADR 0005 §4.1). Import assert closes the
declared↔implemented sets.
"""


from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Mapping

from ..registry.quantity import Measured
from ..registry.record import CHECK_CONDITIONS, RECORD_REGISTER, Absence
from ..registry.settings import setting_names
from ..registry.stages import Outcome, Stage
from .degradation import channel_anomalies
from .population import Population, TurnRow

__all__ = ["Result", "CheckResult", "CHECK_IMPLEMENTATIONS", "evaluate", "quotable"]


class Result(str, Enum):
    """Three-valued on purpose; see the module docstring."""

    #: The condition held over a population large enough to have failed.
    passed = "pass"
    #: The condition was violated.
    failed = "fail"
    #: The inputs were not there. **Not a pass.** A comparison with a
    #: ``cannot_evaluate`` check is not quotable, because the check did not happen.
    cannot_evaluate = "cannot_evaluate"


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome, with everything needed to audit it."""

    field: str
    condition: str
    result: Result
    observed: Measured[float]
    population: str
    detail: str = ""

    def render(self) -> str:
        return (
            f"[{self.result.value:16s}] {self.field:22s} {self.observed.render(4)} "
            f"over {self.population}"
            + (f" -- {self.detail}" if self.detail else "")
        )


#: A check takes the config's turn records and returns its result.
CheckFn = Callable[[Population], CheckResult]


def _result(
    field: str,
    result: Result,
    observed: Measured[float],
    population: Population,
    detail: str = "",
) -> CheckResult:
    return CheckResult(
        field=field,
        condition=CHECK_CONDITIONS[field],
        result=result,
        observed=observed,
        population=population.describe(),
        detail=detail,
    )


def _zero_count_check(field: str, counter: str) -> CheckFn:
    """A check of the form "``counter`` is zero across the config".

    Factored because most checks have this shape, and each hand-written copy is another
    chance to invert the comparison.
    """

    def check(config: Population) -> CheckResult:
        counted = config.count(counter)
        if not counted.is_measured:
            return _result(
                field,
                Result.cannot_evaluate,
                Measured.unmeasured(counted.why),
                config,
                f"{counter!r} was not recorded on every turn, so this check did not run",
            )
        rate = config.rate(counter)
        if counted.value > 0:
            return _result(
                field, Result.failed, rate, config, f"{counted.value} turn(s) with {counter!r}"
            )
        return _result(field, Result.passed, rate, config)

    return check


def _outcome_check(config: Population) -> CheckResult:
    """No turn classified ``crashed``.

    A crash counted as a refusal contaminates every config-to-config delta by a *different*
    amount, since configs do not crash at the same rate. Gated on the classification, never
    on an error string.
    """
    return _zero_count_check("outcome", "crashed")(config)


def _channel_channels_check(config: Population) -> CheckResult:
    """No channel state differs from its declared expectation — on turns that ran.

    Not a ``_zero_count_check``: ``facet_channels`` is stage-conditional (a guard-blocked
    turn never runs the fan-out), so under a naive rate an empty ``facet_channels`` reads
    as "no channel differed" on a turn where no channel ran. The denominator is therefore
    turns where the fan-out ran, published; zero such turns is
    :attr:`Result.cannot_evaluate`, never a pass.
    """
    ran = config.restrict(lambda r: r.get("facet_channels") not in (None, [], {}), "fan-out ran")
    if ran.n == 0:
        return _result(
            "facet_channels",
            Result.cannot_evaluate,
            Measured.unmeasured(f"no turn in {config.describe()} ran the fan-out"),
            config,
            "a degradation rate of 0 over 0 turns is not a pass",
        )
    result = _zero_count_check("facet_channels", "facet_degraded")(ran)
    if result.result is not Result.failed:
        return result
    return replace(result, detail=f"{result.detail}; {_drift(ran)}")


def _drift(config: Population) -> str:
    """Which channel's which channel differed, for a check that has already failed.

    Naming only — the result comes from the written ``facet_degraded`` counter. Judged
    through :func:`~.degradation.channel_anomalies`, which is *not* the function
    ``serve.finish`` decides the counter with (that is
    :func:`~.degradation.facets_degraded`). The two share
    :func:`~.degradation.channel_observations`, so they read the same observations, and they
    differ on purpose: ``extra_channel`` is included here and excluded there.
    ``extra_channel`` is included: it is drift, and it did not refuse this run.
    """
    seen: dict[str, int] = {}
    unjudgeable = 0
    why = ""
    for row in config.rows:
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


def _context_hash_check(config: Population) -> CheckResult:
    """The delivery check's single-config half: every turn carries a ``context_hash``. L-R2.

    On ``context_hash`` and not ``delivery_hash``, because the latter depends on which tool
    calls the model chose, conflating "the treatment differs" with "the model behaved
    differently".

    **This check does not test that the treatment differed, and nothing does it by hash any
    more.** The cross-config half used to be a >= 95% distinctness threshold in
    ``eval/report.context_hashes_distinct``; audit D9 retired it, because retrieval is
    nondeterministic and the hashes differ whether or not the treatment did — it passed at
    0.9993 on a pair differing only by a random seed. That function is now an existence check
    over the shared questions, and the declared treatment is judged from settings by
    ``eval/report.settings_comparable``. What one config can answer is only this: a turn with no
    ``context_hash`` assembled no context, so no later comparison against it holds.
    """
    coverage = config.coverage("context_hash")
    # Three states, not two. **Never recorded**: the config is not instrumented, nothing to judge
    # (`cannot_evaluate`). **Recorded on some turns and not others**: the instrumentation exists
    # and is dropping turns, so the treatment cannot be identified and no later comparison
    # against this config holds (`failed`). Collapsing the two either excuses broken
    # instrumentation or fails a config for not having any.
    if not coverage.is_measured or coverage.value == 0.0:
        return _result(
            "context_hash",
            Result.cannot_evaluate,
            coverage,
            config,
            "no turn carries a context_hash, so this config is not instrumented for the "
            "delivery check at all",
        )
    if coverage.value < 1.0:
        return _result(
            "context_hash",
            Result.failed,
            coverage,
            config,
            "context_hash is recorded on some turns and missing on others, so the treatment "
            "this config delivered cannot be identified and no later comparison against it can "
            "be trusted",
        )
    return _result(
        "context_hash",
        Result.passed,
        coverage,
        config,
        "every turn carries a context_hash. Whether the treatment differed is a two-config "
        "question and audit D9 retired hash distinctness as its test; "
        "eval/report.settings_comparable judges it from the declared settings instead",
    )


#: The **other** treatment identity, used as the witness that ``finish`` ran on a row.
#:
#: Both hashes are declared ``Absence.never`` and owned by ``Stage.finish``, and ``Session``
#: mints the pair together, so a row carrying neither was never written and a row carrying one
#: but not the other is the partial-instrumentation failure the check exists to fail. Using the
#: gated field itself as its own denominator test would be circular — every config would pass over
#: the rows that already have it — which is why the witness has to be a different field.
#: :func:`_assert_the_finish_witness_is_still_a_finish_field` holds the premise.
_FINISH_WITNESS_FIELD = "prompt_set_hash"


def _paused_before_finish(row: TurnRow) -> bool:
    """Did this row end at ``ask_user`` **without** reaching ``finish``?

    Two conditions, and the conjunction is the whole point. ``outcome: clarification`` has meant
    two different endings since ``ask_user`` learned to fail closed (see
    ``registry/stages.Outcome``): a turn that paused and was never resumed, which no node ever
    wrote, and a turn whose reader declined or cancelled, which *is* written and carries every
    field ``finish`` writes. The first must leave the corpus check's denominator and the second
    must not, and the outcome alone cannot tell them apart.

    So the second condition asks the row directly: a turn that reached ``finish`` names its prompt
    set, because that is written by the same node from the same ``Session`` identity as the corpus
    hash. Measured over ``runs/eval/`` on 2026-08-20: on the five instrumented artifacts every row
    with a null ``corpus_content_hash`` is a clarification whose ``prompt_set_hash`` is null too
    (6, 8, 4, 13 and 5 rows), and no other row has either null — so this predicate selects exactly
    the rows the outcome test used to, and stops selecting the one case it was wrong about. On the
    two runs of the null replicate neither key exists at all, so their clarifications drop and the
    remaining 2 688 rows keep the configs at ``cannot_evaluate``, unchanged.

    **What it still cannot see:** a written decline in a run that recorded *neither* identity.
    That row is dropped, and its null corpus hash with it. Every field the drop could have been
    keyed on is written by the same node in the same dict, so no row-local test separates "never
    written" from "written by a run that recorded no identity at all"; what does is
    ``registry/config_profiles.reconcile``, which the driver runs against the config's declared digest
    before the first paid question.
    """
    return (
        str(row.get("outcome")) == Outcome.clarification.value
        and row.get(_FINISH_WITNESS_FIELD) is None
    )


def _corpus_content_hash_check(config: Population) -> CheckResult:
    """One corpus per config, and it is named. D7.

    ADR 0007 calls the corpus the treatment identity of every
    measurement, and until 2026-08-10 no check read it. Two consequences were live at once: a config
    whose rows carry no corpus hash passed every check (both runs of the designated null replicate
    are in that state, 1351/1351 null), and two configs measured over *different* corpora also
    passed, because nothing compared the field across them.

    Single-config half only, like :func:`_context_hash_check`: every row present and all rows equal.
    Two configs carrying *different* single hashes is the desired case for a corpus intervention and
    the disqualifying case for everything else, so which one it is cannot be decided from one config.

    **And nothing decides it.** ``eval/report.comparison_quotable`` runs two cross-config checks,
    ``context_hashes_distinct`` (an existence check on ``context_hash``) and
    ``settings_comparable`` (over ``setting_names()``, which does not contain
    ``corpus_content_hash`` — it is a ``RecordField``, not a setting). So the second consequence
    named above, two configs measured over different corpora both passing, is still live for the
    cross-config case; only the within-config case is closed here. What exists instead is
    ``registry/config_profiles.reconcile``, which the driver runs before the first paid question
    and which checks one config's rows against the digest that config's profile declares.

    Three-valued for the same reason as ``context_hash``: a config predating the field is not
    instrumented (``cannot_evaluate``), whereas a config that records it on some turns and not
    others, or that changed corpus mid-run, cannot be identified (``failed``).

    **Stage-conditional, like ``facet_channels``.** ``finish`` is what writes this field, and a
    turn that paused on ``ask_user`` and was never resumed never reaches it — so such a row
    legitimately carries no corpus hash. Judging those as missing instrumentation would fail
    every config that ever asked a question, which is a check nobody can keep green and therefore a
    preference rather than a check. The denominator is turns that reached ``finish``, published;
    zero such turns is ``cannot_evaluate``, never a pass.

    Which rows those are is :func:`_paused_before_finish`'s question, **not**
    ``outcome == clarification``'s. This check read the outcome alone until 2026-08-20 under the
    label "reached finish", and that stopped being the same fact when ``ask_user`` gained a
    fail-closed decline: such a turn ends the agent loop, *reaches* ``finish``, is written
    ``outcome: clarification`` and carries a full record — so the outcome test dropped a row that
    both belongs in the denominator and would have failed it if its hash were null.
    """
    field = "corpus_content_hash"
    config = config.restrict(
        lambda r: not _paused_before_finish(r), "excluded turns that paused before `finish`"
    )
    if config.n == 0:
        return _result(
            field,
            Result.cannot_evaluate,
            Measured.unmeasured("every turn paused for clarification, so none reached finish"),
            config,
            "a config that never finished a turn has no treatment identity to check",
        )
    coverage = config.coverage(field)
    if not coverage.is_measured or coverage.value == 0.0:
        return _result(
            field,
            Result.cannot_evaluate,
            coverage,
            config,
            "no turn names a corpus, so this config carries no treatment identity and nothing "
            "measured against it is comparable to anything",
        )
    if coverage.value < 1.0:
        return _result(
            field,
            Result.failed,
            coverage,
            config,
            "corpus_content_hash is recorded on some turns and missing on others, so which "
            "corpus this config served is not answerable from its own rows",
        )
    distinct = {str(row.get(field)) for row in config.rows}
    if len(distinct) > 1:
        return _result(
            field,
            Result.failed,
            coverage,
            config,
            f"{len(distinct)} different corpus_content_hash values inside one config, so the "
            "corpus changed while the config was running and its turns are not one treatment",
        )
    return _result(
        field,
        Result.passed,
        coverage,
        config,
        "every turn names the same corpus. Whether it differs from another config's is a two-config "
        "condition and no check evaluates it: comparison_quotable compares context_hash and the "
        "comparability settings, and corpus_content_hash is neither",
    )


def _knobs_resolved_check(config: Population) -> CheckResult:
    """Every row in one config ran under the same settings.

    Absent ``knobs_resolved`` is unmeasured, not passing. A row resolving ``route_top_n``
    to 3 beside one resolving it to 5 is not one config.
    """
    keys = setting_names()
    seen: dict[tuple[tuple[str, str], ...], int] = {}
    absent = 0
    for row in config.rows:
        settings = row.get("knobs_resolved")
        if not isinstance(settings, Mapping):
            absent += 1
            continue
        # `repr`, so two runs differing in a setting's *type* (3 vs "3") are two configurations;
        # a comparison that coerced them would report drift as agreement.
        signature = tuple(sorted((k, repr(settings.get(k))) for k in keys))
        seen[signature] = seen.get(signature, 0) + 1

    if absent:
        return _result(
            "knobs_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured(f"{absent}/{config.n} row(s) carry no knobs_resolved mapping"),
            config,
            detail=(
                f"{absent} of {config.n} rows have no knobs_resolved, so whether the config ran under "
                "one configuration is unknown. Absence.never already reports these as missing."
            ),
        )
    if not seen:
        return _result(
            "knobs_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured(f"{config.label!r} is empty"),
            config,
        )
    distinct = len(seen)
    observed = Measured.of(float(distinct))
    if distinct == 1:
        return _result("knobs_resolved", Result.passed, observed, config)
    return _result(
        "knobs_resolved",
        Result.failed,
        observed,
        config,
        detail=(
            f"{distinct} distinct configurations across {config.n} rows of one config. Every rate over "
            "this config is a rate over a population that does not exist. Differing keys: "
            + ", ".join(sorted(_differing_keys(seen)))
        ),
    )


def _differing_keys(seen: Mapping[tuple[tuple[str, str], ...], int]) -> set[str]:
    """Which setting names actually differ, so a failure names them rather than the count."""
    per_key: dict[str, set[str]] = {}
    for signature in seen:
        for name, value in signature:
            per_key.setdefault(name, set()).add(value)
    return {name for name, values in per_key.items() if len(values) > 1}


CHECK_IMPLEMENTATIONS: Mapping[str, CheckFn] = {
    "outcome": _outcome_check,
    "facet_channels": _channel_channels_check,
    "context_hash": _context_hash_check,
    "corpus_content_hash": _corpus_content_hash_check,
    "knobs_resolved": _knobs_resolved_check,
    "guardrail_errors": _zero_count_check("guardrail_errors", "guardrail_error"),
}


def evaluate(config: Population) -> tuple[CheckResult, ...]:
    """Run every declared check over one config, in declaration order."""
    return tuple(CHECK_IMPLEMENTATIONS[field](config) for field in sorted(CHECK_CONDITIONS))


def quotable(config: Population) -> tuple[bool, tuple[CheckResult, ...]]:
    """Whether this config's numbers may be quoted, and every check's result.

    Both, because a bare ``False`` hides which check refused and a bare ``True`` hides that
    some could not run. **``cannot_evaluate`` blocks quotation**: a check that did not
    happen is not a check that passed.
    """
    results = evaluate(config)
    return all(r.result is Result.passed for r in results), results


def _assert_every_declared_check_is_implemented() -> None:
    """Import-time closure: declared CHECK_CONDITIONS <-> CHECK_IMPLEMENTATIONS."""
    declared, implemented = set(CHECK_CONDITIONS), set(CHECK_IMPLEMENTATIONS)
    if declared != implemented:  # pragma: no cover - import-time guard
        raise AssertionError(
            "quotability checks out of closure: declared-not-implemented "
            f"{sorted(declared - implemented)}, implemented-not-declared "
            f"{sorted(implemented - declared)}. v1 shipped eight of the former and "
            "every one of them passed."
        )


def _assert_the_finish_witness_is_still_a_finish_field() -> None:
    """Import-time: :data:`_FINISH_WITNESS_FIELD` and the gated field are one node's writes.

    :func:`_paused_before_finish` drops rows from the corpus check's denominator on the strength of
    one claim — that ``finish`` writes both treatment identities or neither. That claim lives in
    ``registry/record.py`` as a pair of ``RecordField`` declarations, so it can be read here
    instead of restated: if either field stops being ``Absence.never`` or stops being owned by
    ``Stage.finish``, the witness witnesses nothing and the drop becomes a silent exclusion. The
    version of this rule that shipped before was a sentence in a docstring, which is how a
    written clarification came to be dropped from the denominator without anything noticing.
    """
    declared = {f.name: f for f in RECORD_REGISTER}
    for name in (_FINISH_WITNESS_FIELD, "corpus_content_hash"):
        field = declared.get(name)
        if field is None:  # pragma: no cover - import-time guard
            raise AssertionError(
                f"{name!r} is not in RECORD_REGISTER, so the corpus check's denominator rests on "
                "a field the register does not declare."
            )
        if field.absence is not Absence.never or field.owner is not Stage.finish:
            raise AssertionError(  # pragma: no cover - import-time guard
                f"{name!r} is declared owner={field.owner.value} absence={field.absence.value}; "
                "the corpus check reads a null prompt_set_hash as 'this turn never reached "
                "finish', which holds only while both treatment identities are written by "
                "`finish` on every terminal path."
            )


_assert_every_declared_check_is_implemented()
_assert_the_finish_witness_is_still_a_finish_field()
