"""Quotability checks keyed on ``registry.record.CHECK_CONDITIONS``.

Three results: pass / fail / cannot_evaluate. Missing fields never pass.
Refuse the comparison; do not warn (ADR 0005 §4.1). Import assert closes the
declared↔implemented sets.
"""


from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Mapping

from governed_bi.registry.stored_names import OLD_KEY, alias_row, artifact_get

from ..registry.quantity import Measured
from ..registry.record import CHECK_CONDITIONS, RECORD_REGISTER, Absence
from ..registry.settings import setting_names
from ..registry.stages import Outcome, Stage
from .degradation import channel_anomalies
from .population import Population, TurnRow

__all__ = ["Result", "CheckResult", "CHECK_IMPLEMENTATIONS", "evaluate", "quotable"]


class Result(str, Enum):
    #: Not a pass. The check did not happen, so the comparison is not quotable.
    passed = "pass"
    failed = "fail"
    cannot_evaluate = "cannot_evaluate"


@dataclass(frozen=True)
class CheckResult:
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
    """Factored so each copy is not another chance to invert the comparison."""

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
    """A crash counted as a refusal moves each config's delta by a different amount.

    Judged from the classification, never from an error string.
    """
    return _zero_count_check("outcome", "crashed")(config)


def _with_stored_names(config: Population) -> Population:
    if not any(any(old in row for old in OLD_KEY.values()) for row in config.rows):
        return config

    return Population(
        config.label,
        config.unit_key,
        tuple(alias_row(row) for row in config.rows),
        config.filtered_by,
    )


def _channel_channels_check(config: Population) -> CheckResult:
    """An empty ``channel_scoring`` is not "no channel differed".

    The denominator is turns whose fan-out ran. Zero such turns is ``cannot_evaluate``, not a pass.
    """
    config = _with_stored_names(config)
    ran = config.restrict(lambda r: r.get("channel_scoring") not in (None, [], {}), "fan-out ran")
    if ran.n == 0:
        return _result(
            "channel_scoring",
            Result.cannot_evaluate,
            Measured.unmeasured(f"no turn in {config.describe()} ran the fan-out"),
            config,
            "a degradation rate of 0 over 0 turns is not a pass",
        )
    result = _zero_count_check("channel_scoring", "channel_degraded")(ran)
    if result.result is not Result.failed:
        return result
    return replace(result, detail=f"{result.detail}; {_drift(ran)}")


def _drift(config: Population) -> str:
    """Names drift only. ``extra_channel`` is included here and excluded from the written counter."""
    seen: dict[str, int] = {}
    unjudgeable = 0
    why = ""
    for row in config.rows:
        try:
            anomalies = channel_anomalies(row.get("channel_scoring"))
        except ValueError as err:
            unjudgeable += 1
            why = why or str(err)
            continue
        for key, anomaly in anomalies.items():
            seen[f"{key}={anomaly}"] = seen.get(f"{key}={anomaly}", 0) + 1
    parts = [f"{key} on {count} turn(s)" for key, count in sorted(seen.items())]
    if unjudgeable:
        parts.append(
            f"{unjudgeable} turn(s) whose channel_scoring could not be judged against the "
            f"register, so their drift is not named here: {why}"
        )
    return "; ".join(parts) if parts else "no channel state differs from its declaration"


def _context_hash_check(config: Population) -> CheckResult:
    """On ``context_hash``, not ``delivery_hash``: the latter moves when the model chooses different tools.

    This does not test that the setting under test differed. A turn with no hash assembled no context.
    """
    coverage = config.coverage("context_hash")
    # Never recorded is cannot_evaluate. Recorded on only some turns is failed.
    # Collapsing the two either excuses broken instrumentation or fails a config that has none.
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


# Evidence that finish ran, and not the field being checked: using that field as
# its own denominator would pass every row that already has it. Both are written
# by finish together, or neither is.
_FINISH_EVIDENCE_FIELD = "prompt_set_hash"


def _paused_before_finish(row: TurnRow) -> bool:
    """``clarification`` is both a pause and a written decline. The outcome alone cannot tell them apart.

    A row that reached ``finish`` has ``prompt_set_hash``. A written decline that recorded neither identity is dropped;
    ``reconcile`` is what sees that case.
    """
    return (
        str(row.get("outcome")) == Outcome.clarification.value
        and row.get(_FINISH_EVIDENCE_FIELD) is None
    )


def _corpus_content_hash_check(config: Population) -> CheckResult:
    """One corpus per config, and it is named. Two configs over different corpora cannot be judged from one config.

    Turns that paused before ``finish`` leave the denominator. ``clarification`` alone is the wrong test: a fail-closed
    decline reaches ``finish``. Zero finished turns is ``cannot_evaluate``, not a pass.
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


def _settings_resolved_check(config: Population) -> CheckResult:
    """Absent settings are unmeasured, not a pass. Two resolutions are not one config."""
    config = _with_stored_names(config)
    keys = setting_names()
    seen: dict[tuple[tuple[str, str], ...], int] = {}
    absent = 0
    for row in config.rows:
        settings = artifact_get(row, "settings_resolved")
        if not isinstance(settings, Mapping):
            absent += 1
            continue
        # `repr`, so two runs differing in a setting's *type* (3 vs "3") are two configurations;
        # a comparison that coerced them would report drift as agreement.
        signature = tuple(sorted((k, repr(settings.get(k))) for k in keys))
        seen[signature] = seen.get(signature, 0) + 1

    if absent:
        return _result(
            "settings_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured(f"{absent}/{config.n} row(s) carry no settings_resolved mapping"),
            config,
            detail=(
                f"{absent} of {config.n} rows have no settings_resolved, so whether the config ran under "
                "one configuration is unknown. Absence.never already reports these as missing."
            ),
        )
    if not seen:
        return _result(
            "settings_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured(f"{config.label!r} is empty"),
            config,
        )
    distinct = len(seen)
    observed = Measured.of(float(distinct))
    if distinct == 1:
        return _result("settings_resolved", Result.passed, observed, config)
    return _result(
        "settings_resolved",
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
    per_key: dict[str, set[str]] = {}
    for signature in seen:
        for name, value in signature:
            per_key.setdefault(name, set()).add(value)
    return {name for name, values in per_key.items() if len(values) > 1}


CHECK_IMPLEMENTATIONS: Mapping[str, CheckFn] = {
    "outcome": _outcome_check,
    "channel_scoring": _channel_channels_check,
    "context_hash": _context_hash_check,
    "corpus_content_hash": _corpus_content_hash_check,
    "settings_resolved": _settings_resolved_check,
    "guardrail_errors": _zero_count_check("guardrail_errors", "guardrail_error"),
}


def evaluate(config: Population) -> tuple[CheckResult, ...]:
    return tuple(CHECK_IMPLEMENTATIONS[field](config) for field in sorted(CHECK_CONDITIONS))


def quotable(config: Population) -> tuple[bool, tuple[CheckResult, ...]]:
    """``cannot_evaluate`` blocks quotation: a check that did not happen is not a pass."""
    results = evaluate(config)
    return all(r.result is Result.passed for r in results), results


def _assert_every_declared_check_is_implemented() -> None:
    declared, implemented = set(CHECK_CONDITIONS), set(CHECK_IMPLEMENTATIONS)
    if declared != implemented:  # pragma: no cover - import-time guard
        raise AssertionError(
            "quotability checks out of closure: declared-not-implemented "
            f"{sorted(declared - implemented)}, implemented-not-declared "
            f"{sorted(implemented - declared)}. v1 shipped eight of the former and "
            "every one of them passed."
        )


def _assert_the_finish_evidence_is_still_a_finish_field() -> None:
    """Both fields must stay ``Absence.never`` and owned by ``Stage.finish``, or a null hash stops meaning the turn
    never finished."""
    declared = {f.name: f for f in RECORD_REGISTER}
    for name in (_FINISH_EVIDENCE_FIELD, "corpus_content_hash"):
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
_assert_the_finish_evidence_is_still_a_finish_field()
