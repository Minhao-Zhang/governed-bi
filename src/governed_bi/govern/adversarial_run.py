"""Run the adversarial governance suite and publish its rates.

The cases and the world are :mod:`.adversarial`; this is the instrument. It exists as its own
module because the two have different lifecycles — cases are added weekly, the metric
definitions almost never — and because a driver and a test both need the instrument while only
the loader needs the schema.

Two entry points are exercised for every case. ``check()`` is the layer stack; ``prepare()`` is
the pipeline around it, and it is the one that produces an executable string. They are not
interchangeable: ``tools/mutation_catalogue.py``'s ``m1-guard-bypass`` records that ``prepare()``
once handed back runnable SQL for a refused verdict while 133/133 tests passed. So an attack
that leaves ``Prepared.sql`` non-``None`` is a bypass whatever the verdict said.

Every rate is a :class:`~governed_bi.register.quantity.Measured`, so a rate with no denominator
renders as *not measured* rather than as ``1.000``. ``COST`` owns no attacks — the layer ships
disabled — and reporting perfect recall for it would be the shape that made v1's unmeasured
quantities read as clean.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from ..register.quantity import Measured
from .adversarial import (
    CASE_FAMILIES,
    AdversarialCase,
    AdversarialSuite,
    GuardCase,
    WorldFixture,
    build_world_fixture,
    load_adversarial_suite,
)
from .check import check
from .guard import GuardVerdict, guard
from .layers import GUARDRAIL_ERROR, CheckVerdict, Layer
from .pipeline import Prepared, prepare
from .policy import DEFAULT_DIALECT, GUARD_RULE_IDS, GovernancePolicy

__all__ = [
    "CaseResult",
    "SuiteReport",
    "run_adversarial_suite",
    "report_lines",
    "format_case_failures",
    "GuardCaseResult",
    "GuardReport",
    "run_guard_suite",
    "guard_report_lines",
]


@dataclass(frozen=True, slots=True)
class CaseResult:
    """What one case did, through both entry points."""

    case: AdversarialCase
    verdict: CheckVerdict
    prepared: Prepared
    #: One of ``caught`` / ``bypassed`` / ``misattributed`` for an attack, ``allowed`` /
    #: ``false_refusal`` for a benign control, or ``guardrail_error`` on either half.
    status: str
    detail: str

    @property
    def observed(self) -> CheckVerdict:
        """The verdict the expectation is read from (:attr:`AdversarialCase.enforced_by`)."""
        return self.prepared.verdict if self.case.enforced_by == "pipeline" else self.verdict


def _classify(case: AdversarialCase, result: CheckVerdict, prepared: Prepared) -> tuple[str, str]:
    observed = prepared.verdict if case.enforced_by == "pipeline" else result
    crashed = [
        name
        for name, verdict in (("check", result), ("prepare", prepared.verdict))
        if verdict["reason_code"] == GUARDRAIL_ERROR
    ]
    if crashed:
        return "guardrail_error", f"{'/'.join(crashed)} raised: {observed['detail']}"

    if not case.is_attack:
        if observed["passed"] and prepared.sql is not None:
            return "allowed", ""
        if observed["passed"]:
            return "false_refusal", "the verdict passed and prepare() still produced no string"
        layer = observed["failed_layer"]
        return "false_refusal", f"{layer.name if layer else '?'}/{observed['reason_code']}"

    # An attack that yields an executable string is a bypass whatever the verdict says: the
    # string is the thing the database sees.
    if prepared.sql is not None:
        return "bypassed", "prepare() produced an executable string"
    if observed["passed"]:
        return "bypassed", f"{case.enforced_by} returned a passing verdict"
    layer = observed["failed_layer"]
    if layer is not case.expect_layer or observed["reason_code"] != case.expect_rule:
        return "misattributed", (
            f"expected {case.expect_layer.name if case.expect_layer else '?'}/"
            f"{case.expect_rule}, got {layer.name if layer else '?'}/{observed['reason_code']}"
        )
    return "caught", ""


def _run_case(case: AdversarialCase, fixture: WorldFixture, policy: GovernancePolicy) -> CaseResult:
    verdict = check(
        case.sql,
        licensed=fixture.licensed,
        corpus=fixture.corpus,
        default_schema=fixture.default_schema,
        dialect=DEFAULT_DIALECT,
        policy=policy,
    )
    prepared = prepare(
        case.sql,
        licensed=fixture.licensed,
        corpus=fixture.corpus,
        spellings=fixture.spellings,
        ambiguous_folds=fixture.ambiguous,
        spellings_by_table=fixture.by_table,
        default_schema=fixture.default_schema,
        dialect=DEFAULT_DIALECT,
        policy=policy,
    )
    status, detail = _classify(case, verdict, prepared)
    return CaseResult(case=case, verdict=verdict, prepared=prepared, status=status, detail=detail)


@dataclass(frozen=True, slots=True)
class SuiteReport:
    """Every rate this suite publishes, each with the denominator it was taken over."""

    version: str
    results: tuple[CaseResult, ...]

    def of_kind(self, kind: str) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if r.case.kind == kind)

    def with_status(self, status: str, kind: str | None = None) -> tuple[CaseResult, ...]:
        pool = self.results if kind is None else self.of_kind(kind)
        return tuple(r for r in pool if r.status == status)

    def rate(self, status: str, kind: str) -> Measured[float]:
        return Measured.rate(
            len(self.with_status(status, kind)), len(self.of_kind(kind)), what=f"{kind} cases"
        )

    def guardrail_error_rate(self) -> Measured[float]:
        return Measured.rate(
            len(self.with_status("guardrail_error")), len(self.results), what="cases"
        )

    def owned_by(self, layer: Layer) -> tuple[CaseResult, ...]:
        """The attacks whose declared refusal belongs to ``layer``."""
        return tuple(r for r in self.of_kind("attack") if r.case.expect_layer is layer)

    def layer_recall(self, layer: Layer) -> Measured[float]:
        owned = self.owned_by(layer)
        caught = [r for r in owned if r.status == "caught"]
        return Measured.rate(len(caught), len(owned), what=f"attacks owned by {layer.name}")

    def by_family(self) -> Mapping[str, tuple[int, int]]:
        """``family -> (attacks, benign)``, so a thin family is visible rather than inferred."""
        out: dict[str, tuple[int, int]] = {}
        for family in sorted(CASE_FAMILIES):
            cases = [r.case for r in self.results if r.case.family == family]
            out[family] = (
                sum(1 for c in cases if c.is_attack),
                sum(1 for c in cases if not c.is_attack),
            )
        return out

    def failures(self) -> tuple[CaseResult, ...]:
        """Every result the gate fails on: a bypass, a misattribution, a crash, or a false
        refusal nobody declared."""
        return tuple(
            r
            for r in self.results
            if r.status in ("bypassed", "misattributed", "guardrail_error")
            or (r.status == "false_refusal" and not r.case.known_false_refusal)
        )


def run_adversarial_suite(
    suite: AdversarialSuite | None = None, *, policy: GovernancePolicy | None = None
) -> SuiteReport:
    """Run every case through ``check()`` and ``prepare()``. No model, no network, no I/O
    beyond reading the suite file."""
    suite = suite or load_adversarial_suite()
    fixture = build_world_fixture(suite.world)
    # The world's grant, not the caller's. `licensed`, `excluded` and `suspect` already come
    # from the world and a case's expectation is written against all of them together; an
    # authorization that a caller could swap out would make `r_table_not_authorized`'s
    # expectations true of some runs and not others, which is a suite that measures the
    # caller rather than the layer stack (ADR 0012 §9).
    resolved = replace(policy or GovernancePolicy(), access_grant=fixture.grant)
    return SuiteReport(
        version=suite.version,
        results=tuple(_run_case(case, fixture, resolved) for case in suite.cases),
    )


@dataclass(frozen=True, slots=True)
class GuardCaseResult:
    """One :class:`~governed_bi.govern.adversarial.GuardCase`, run."""

    case: GuardCase
    verdict: GuardVerdict
    status: str
    detail: str


def _classify_guard(case: GuardCase, verdict: GuardVerdict) -> tuple[str, str]:
    """The statuses the statement half uses, minus the two that need a statement.

    ``bypassed`` keeps its meaning — an attack that reached the model — but there is no
    ``prepare()`` string to check, so a passing verdict is the whole of it. ``error_failed_open``
    is this half's ``guardrail_error``: :func:`~governed_bi.govern.guard.guard` returns it when
    a predicate raises, and it is a **bypass in effect** (``serve/graph.py::_after_guard``
    routes anything that is not ``blocked`` onward) which is why it is counted apart rather
    than folded into ``caught``.
    """
    if verdict["outcome"] == "error_failed_open":
        return "guardrail_error", f"{verdict['rule_id']} raised: {verdict['detail']}"
    blocked = verdict["outcome"] == "blocked"
    if not case.is_attack:
        return ("false_refusal", str(verdict["rule_id"])) if blocked else ("allowed", "")
    if not blocked:
        return "bypassed", "guard returned a clear verdict"
    if verdict["rule_id"] != case.expect_rule:
        return "misattributed", f"expected {case.expect_rule}, got {verdict['rule_id']}"
    return "caught", ""


@dataclass(frozen=True, slots=True)
class GuardReport:
    """The prompt-injection half's rates, each with its denominator."""

    version: str
    results: tuple[GuardCaseResult, ...]

    def of_kind(self, kind: str) -> tuple[GuardCaseResult, ...]:
        return tuple(r for r in self.results if r.case.kind == kind)

    def with_status(self, status: str, kind: str | None = None) -> tuple[GuardCaseResult, ...]:
        pool = self.results if kind is None else self.of_kind(kind)
        return tuple(r for r in pool if r.status == status)

    def rate(self, status: str, kind: str) -> Measured[float]:
        return Measured.rate(
            len(self.with_status(status, kind)), len(self.of_kind(kind)), what=f"{kind} questions"
        )

    def rule_recall(self, rule_id: str) -> Measured[float]:
        """Per rule, over the attacks that rule owns. The statement half's ``layer_recall``.

        Per *rule* and not per family, because ADR 0006 OQ3's condition for shipping these
        enabled was a number for each one — a set-level recall of 1.000 is compatible with one
        rule catching everything and four catching nothing.
        """
        owned = [r for r in self.of_kind("attack") if r.case.expect_rule == rule_id]
        caught = [r for r in owned if r.status == "caught"]
        return Measured.rate(len(caught), len(owned), what=f"attacks owned by {rule_id}")

    def failures(self) -> tuple[GuardCaseResult, ...]:
        return tuple(
            r
            for r in self.results
            if r.status in ("bypassed", "misattributed", "guardrail_error")
            or (r.status == "false_refusal" and not r.case.known_false_refusal)
        )


def run_guard_suite(
    suite: AdversarialSuite | None = None, *, policy: GovernancePolicy | None = None
) -> GuardReport:
    """Run every ``[[guard_case]]`` through ``guard()``. No model, no network, no database.

    **Every rule on, whatever the caller's policy says.** The suite's job is what the rules
    are worth, not what a deployment enabled; running it under a policy that disabled one
    would report a bypass rate over a rule that never ran. That is the shape of the defect
    this half exists because of.
    """
    suite = suite or load_adversarial_suite()
    resolved = replace(
        policy or GovernancePolicy(),
        guard_rules_enabled={rule_id: True for rule_id in GUARD_RULE_IDS},
    )
    results = []
    for case in suite.guard_cases:
        verdict = guard(case.text, resolved)
        status, detail = _classify_guard(case, verdict)
        results.append(
            GuardCaseResult(case=case, verdict=verdict, status=status, detail=detail)
        )
    return GuardReport(version=suite.version, results=tuple(results))


def guard_report_lines(report: GuardReport) -> list[str]:
    """The printed report for the prompt-injection half, same shape as the statement half."""

    def counted(status: str, kind: str) -> str:
        return (
            f"{report.rate(status, kind).render(3)}  "
            f"({len(report.with_status(status, kind))}/{len(report.of_kind(kind))})"
        )

    lines = [
        "",
        f"prompt-injection half — {len(report.results)} questions "
        f"({len(report.of_kind('attack'))} attack, {len(report.of_kind('benign'))} benign)",
        "  attacks",
        f"    reached the model         {counted('bypassed', 'attack')}",
        f"    blocked by another rule   {counted('misattributed', 'attack')}",
        f"    failed open (rule raised) {counted('guardrail_error', 'attack')}",
        f"    caught                    {counted('caught', 'attack')}",
        "  benign",
        f"    false refusal             {counted('false_refusal', 'benign')}",
        f"    allowed                   {counted('allowed', 'benign')}",
        "  per-rule recall (denominator: the attacks each rule owns)",
    ]
    for rule_id in sorted(GUARD_RULE_IDS):
        lines.append(f"    {rule_id:24} {report.rule_recall(rule_id).render(3)}")
    lines.append(f"  failures: {len(report.failures())}")
    for result in report.failures():
        lines.append(f"    {result.case.id}: {result.status} — {result.detail}")
    return lines


def _counted(report: SuiteReport, status: str, kind: str) -> str:
    return (
        f"{report.rate(status, kind).render(3)}  "
        f"({len(report.with_status(status, kind))}/{len(report.of_kind(kind))})"
    )


def report_lines(report: SuiteReport) -> list[str]:
    """The printed report. Every rate carries its denominator on the same line, because a rate
    whose denominator a reader has to go and find is a rate nobody checks."""
    attacks, benign = report.of_kind("attack"), report.of_kind("benign")
    lines = [
        f"adversarial governance suite v{report.version} — {len(report.results)} cases "
        f"({len(attacks)} attack, {len(benign)} benign)",
        "",
        f"attacks (n={len(attacks)})",
        f"  bypass rate          {_counted(report, 'bypassed', 'attack')}"
        "   passed, or prepare() produced an executable string",
        f"  misattribution rate  {_counted(report, 'misattributed', 'attack')}"
        "   refused by the wrong layer or the wrong rule",
        f"  guardrail errors     {_counted(report, 'guardrail_error', 'attack')}"
        "   check() crashed rather than deciding",
        f"  caught               {_counted(report, 'caught', 'attack')}",
        "",
        f"benign (n={len(benign)})",
        f"  false-refusal rate   {_counted(report, 'false_refusal', 'benign')}",
        f"  guardrail errors     {_counted(report, 'guardrail_error', 'benign')}",
        "",
        f"guardrail-error rate over all {len(report.results)} cases: "
        f"{report.guardrail_error_rate().render(3)}",
        "",
        "per-layer recall (denominator: the attacks that layer owns)",
    ]
    for layer in Layer:
        owned = report.owned_by(layer)
        caught = [r for r in owned if r.status == "caught"]
        lines.append(
            f"  {layer.name:<9} {report.layer_recall(layer).render(3)}  ({len(caught)}/{len(owned)})"
        )
    lines += ["", "cases by family (attack / benign)"]
    for family, (n_attack, n_benign) in report.by_family().items():
        lines.append(f"  {family:<10} {n_attack:>3} / {n_benign:>3}")

    declared = [r for r in report.results if r.case.known_false_refusal]
    if declared:
        lines += ["", "accepted false refusals (counted in the rate, not failed)"]
        lines += [f"  {r.case.id}: {r.case.known_false_refusal}" for r in declared]

    failures = report.failures()
    lines += ["", f"failures: {len(failures)}"]
    lines += [f"  {r.status:<15} {r.case.id}: {r.detail}" for r in failures]
    return lines


def format_case_failures(results: Iterable[CaseResult]) -> str:
    """One line per failing case, for a test's assertion message."""
    return "\n".join(
        f"{r.status}: {r.case.id} [{r.case.family}] {r.detail}\n"
        f"    sql   : {r.case.sql}\n"
        f"    why   : {r.case.why}\n"
        f"    origin: {r.case.origin}"
        for r in results
    )
