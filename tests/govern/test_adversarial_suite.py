"""The gate over the adversarial governance suite (``govern/adversarial.toml``).

This is the build-failing half of the instrument open-work.md 3.11 asks for. It fails on a
**bypass** (an attack that reached a passing verdict, or that produced an executable string
whatever the verdict said), on a **misattribution** (an attack refused by the wrong layer or
the wrong rule — refusing for the wrong reason means the rule meant to catch it did not), on a
**guardrail error** (the checker crashed rather than deciding), and on a false refusal nobody
declared. It reports the false-refusal rate whether or not it is zero.

**Every test here drives the real entry points.** None re-implements a layer's arithmetic, and
the expectations live in the TOML rather than in these functions, so a disagreement between the
code and the criterion is a diff in a data file.

**This file has positive controls, for audit-2026-08-10 D13.** That finding is that six
conformance sweeps assert ``not offenders`` with no positive control, so a typo'd regex passes.
Two things answer it here. The loader tests below plant a case that must fail to load and check
that it does — a suite that silently accepted a malformed case would report a perfect score over
fewer cases than it appears to run. And ``tools/mutation_catalogue.py`` carries nine mutations
against the layer stack (``g1``–``g10``, with ``g6`` retired and its retirement argued in place),
each verified by hand to make this file fail with the
right diagnosis; a suite whose gate survives the layer being deleted is a green tick and nothing
else. Four of the nine are caught as a *misattribution* rather than as a bypass, which is why the
two are measured separately: the statement is still refused, one layer late, under a rule about
something else, and a gate asking only "was it refused" reports a deleted rule as working.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("B")

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def report():
    """The suite, run once. Deterministic and model-free, so one run is every run."""
    from governed_bi.govern.adversarial_run import run_adversarial_suite

    return run_adversarial_suite()


# ── the shape of the suite itself ─────────────────────────────────────────────


def test_the_suite_declares_both_halves_and_every_family(report) -> None:
    """A checker that refuses everything scores a perfect bypass rate, so the benign half is
    the other side of the only trade the layer stack makes — and a family with no cases is a
    part of the stack nothing is aimed at."""
    from governed_bi.govern.adversarial import CASE_FAMILIES

    attacks, benign = report.of_kind("attack"), report.of_kind("benign")
    assert attacks and benign
    assert len(benign) * 2 >= len(attacks), (
        f"{len(benign)} benign controls against {len(attacks)} attacks; the false-refusal rate "
        "is the companion metric ADR 0006 §2 requires and this denominator cannot carry it"
    )
    empty = sorted(f for f, (a, b) in report.by_family().items() if a + b == 0)
    assert not empty, f"families with no cases at all: {empty}"
    assert set(report.by_family()) == set(CASE_FAMILIES)


def test_a_suite_with_no_benign_controls_is_refused(report) -> None:
    """The positive control for the test above: the guard fires rather than being decorative.

    Reaches for the private guard on purpose. It runs at import time over the real suite, so
    there is no public call that can be handed a broken one, and the alternative to importing
    it is not testing the argument this whole file rests on.
    """
    from governed_bi.govern.adversarial import _assert_the_suite_has_both_halves

    attacks_only = tuple(r.case for r in report.of_kind("attack"))
    with pytest.raises(AssertionError):
        _assert_the_suite_has_both_halves(attacks_only)
    with pytest.raises(AssertionError):
        _assert_the_suite_has_both_halves(tuple(r.case for r in report.of_kind("benign")))


# ── the numbers ───────────────────────────────────────────────────────────────


def test_no_attack_reaches_a_passing_verdict(report) -> None:
    """Bypass rate, denominator: every attack case. This one must be zero and there is no
    tier below it — a governance layer that closes nine of ten bypasses is not 90% of a
    governance layer (ADR 0006's own framing)."""
    from governed_bi.govern.adversarial_run import format_case_failures

    bypassed = report.with_status("bypassed", "attack")
    assert not bypassed, (
        f"{len(bypassed)} of {len(report.of_kind('attack'))} attacks were not stopped:\n"
        + format_case_failures(bypassed)
    )


def test_no_attack_is_refused_by_the_wrong_layer_or_rule(report) -> None:
    """Misattribution rate, denominator: every attack case.

    "It was refused" is not the claim. If a whole-row aggregate refuses because its *name* is
    absent from the allowlist rather than because of the whole-row argument rule, then the rule
    written to catch that shape is untested and the next spelling of it walks through.
    """
    from governed_bi.govern.adversarial_run import format_case_failures

    wrong = report.with_status("misattributed", "attack")
    assert not wrong, (
        f"{len(wrong)} of {len(report.of_kind('attack'))} attacks were refused for the wrong "
        "reason:\n" + format_case_failures(wrong)
    )


def test_no_case_makes_the_checker_crash(report) -> None:
    """Guardrail-error rate, denominator: every case, both halves.

    ADR 0006 §1 makes any exception inside ``check()`` a block, and §12 requires it also be
    *counted*: a systematically broken checker otherwise presents as an arm that refuses
    everything, with ``crash_rate == 0`` and every register key present.
    """
    from governed_bi.govern.adversarial_run import format_case_failures

    crashed = report.with_status("guardrail_error")
    assert not crashed, format_case_failures(crashed)


def test_every_attack_leaves_prepare_with_nothing_to_execute(report) -> None:
    """``Prepared.sql is None`` for every attack, asserted separately from the verdict.

    ``tools/mutation_catalogue.py``'s ``m1-guard-bypass`` is the incident: setting ``pipeline.py``'s
    ``if not verdict["passed"]`` to ``if False:`` made ``prepare()`` hand back
    ``'SELECT token FROM secrets LIMIT 200001'`` for a refused verdict, and 133/133 tests
    passed. The verdict is not the artifact; the string is.
    """
    leaked = [
        r for r in report.of_kind("attack") if r.prepared.sql is not None
    ]
    assert not leaked, "\n".join(
        f"{r.case.id}: verdict={r.observed['reason_code']} sql={r.prepared.sql!r}" for r in leaked
    )


def test_the_false_refusal_rate_is_reported(report, capsys) -> None:
    """False-refusal rate, denominator: every benign case.

    A positive function allowlist and a fail-closed binding rule trade false refusals for
    closed holes, and the trade is only honest if the other side is measured (ADR 0006 §2).
    So the rate is printed on every run, and an undeclared false refusal fails: a benign case
    is a declaration that this statement must pass, and accepting one that does not should be
    a diff — the ``known_false_refusal`` field, empty today — rather than a drifting number.
    """
    from governed_bi.govern.adversarial_run import format_case_failures

    benign = report.of_kind("benign")
    refused = report.with_status("false_refusal", "benign")
    print(
        f"false-refusal rate: {report.rate('false_refusal', 'benign').render(3)} "
        f"({len(refused)}/{len(benign)} benign cases)"
    )
    assert "false-refusal rate" in capsys.readouterr().out
    undeclared = [r for r in refused if not r.case.known_false_refusal]
    assert not undeclared, format_case_failures(undeclared)


def test_every_layer_that_owns_an_attack_catches_all_of_them(report) -> None:
    """Per-layer recall, denominator: the attacks each layer owns.

    Reported per layer rather than pooled, because a pooled 100% over a suite weighted towards
    one cheap layer says nothing about the expensive ones. ``COST`` owns none and its rate is
    ``not measured`` rather than 1.0 — the layer ships disabled (``cost_budget`` is UNSET), and
    a rate of 1.0 over an empty denominator is the shape that made v1's unmeasured quantities
    read as clean.
    """
    from governed_bi.govern.layers import Layer

    for layer in Layer:
        owned = [r for r in report.of_kind("attack") if r.case.expect_layer is layer]
        recall = report.layer_recall(layer)
        if not owned:
            assert not recall.is_measured, f"{layer.name} owns no attacks but reports a rate"
            continue
        missed = [r for r in owned if r.status != "caught"]
        assert not missed, f"{layer.name}: {[r.case.id for r in missed]}"


# ── the two resolver defects from open-work.md 3.2a ───────────────────────────


@pytest.mark.parametrize(
    "case_id",
    [
        "a_spelling_derived_alias_shadows_a_table_handle",
        "a_spelling_self_colliding_table_keeps_its_bare_handle",
    ],
)
def test_the_two_confirmed_resolver_defects_refuse(report, case_id: str) -> None:
    """Both were reproduced in open-work.md 3.2a and neither was fixed until this suite.

    Named individually as well as being covered by the bypass gate, because the gate reports
    "an attack passed" and these two want their own diagnosis: the first is a derived-source
    alias borrowing a base table's spelling map, the second is a self-colliding table failing
    to poison its bare handle. Each ``prepare()`` emitted valid SQL reading a column the
    verdict never approved.
    """
    result = next(r for r in report.results if r.case.id == case_id)
    assert result.prepared.sql is None, result.prepared.sql
    assert result.observed["reason_code"] == "r_ambiguous_fold", result.observed
    assert result.status == "caught", result.detail


# ── the loader refuses a case that does not explain itself ────────────────────

#: The ten ``[bypass.*]`` rows every suite must declare, as text. Built rather than typed out:
#: the probe fixture is read on every failure below and ten hand-written stanzas would bury the
#: one line each test breaks. All ten claim **no** SQL surface, which is what makes
#: :data:`VALID` — whose two cases tag no bypass — consistent.
BYPASS_ROWS = "".join(
    f'[bypass.B{n}]\nsql_surface = false\nwhy = "a probe suite aims no statement at it."\n'
    f'pinned_by = "this docstring"\n\n'
    for n in range(1, 11)
)

#: A suite the loader accepts, as text. Every negative test below is this with one thing
#: broken, so a failure names the break rather than a fixture nobody re-reads.
VALID = f"""
version = "test"

{BYPASS_ROWS}
[world]
default_schema = "s"
licensed = ["s.t"]
[world.tables]
"s.t" = ["a"]
"s.u" = ["b"]

[[case]]
id = "attack_one"
kind = "attack"
family = "table"
sql = "SELECT u.b FROM s.u AS u"
expect_layer = "TABLES"
expect_rule = "r_table_not_licensed"
why = "s.u is not licensed."
origin = "this docstring"

[[case]]
id = "benign_one"
kind = "benign"
family = "table"
sql = "SELECT t.a FROM s.t AS t"
why = "s.t is licensed."
origin = "this docstring"
"""


def _load(tmp_path: Path, text: str):
    from governed_bi.govern.adversarial import load_adversarial_suite

    path = tmp_path / "probe.toml"
    path.write_text(text, encoding="utf-8")
    return load_adversarial_suite(path)


def test_the_probe_suite_loads(tmp_path: Path) -> None:
    """The control. Without it every test below passes for a loader that rejects everything."""
    suite = _load(tmp_path, VALID)
    assert [c.id for c in suite.cases] == ["attack_one", "benign_one"]


@pytest.mark.parametrize(
    "find, replace, expect",
    [
        pytest.param(
            'why = "s.u is not licensed."\n', "", "why", id="attack_with_no_why"
        ),
        pytest.param(
            'origin = "this docstring"\n\n[[case]]\nid = "benign_one"',
            '\n[[case]]\nid = "benign_one"',
            "origin",
            id="attack_with_no_origin",
        ),
        pytest.param(
            'expect_rule = "r_table_not_licensed"\n', "", "expect_layer", id="attack_with_no_rule"
        ),
        pytest.param(
            'expect_layer = "TABLES"', 'expect_layer = "COLUMNS"', "RULES says", id="layer_typo"
        ),
        pytest.param(
            'expect_rule = "r_table_not_licensed"',
            'expect_rule = "r_table_not_licenced"',
            "not a rule",
            id="rule_typo",
        ),
        pytest.param(
            'family = "table"\nsql = "SELECT u.b',
            'family = "tables"\nsql = "SELECT u.b',
            "family",
            id="family_typo",
        ),
        pytest.param(
            'id = "benign_one"', 'id = "attack_one"', "duplicate case id", id="duplicate_id"
        ),
        pytest.param(
            'licensed = ["s.t"]', 'licensed = ["s.t", "s.u"]', "licenses every table", id="all_licensed"
        ),
        pytest.param(
            'licensed = ["s.t"]', 'licensed = ["s.absent"]', "no [world.tables] entry", id="phantom_licence"
        ),
    ],
)
def test_a_case_that_does_not_explain_itself_fails_to_load(
    tmp_path: Path, find: str, replace: str, expect: str
) -> None:
    """One break per parameter, because these are nine separate branches in the loader and a
    single case passing would leave eight that might never have been wired up.

    The two that matter most are the last pair of typos. ``expect_layer`` and ``expect_rule``
    are declared twice on purpose — once by the case, once by ``govern.layers.RULES`` — so a
    typo in either half fails to load rather than pinning the wrong attribution and reporting
    100% recall for a layer that never fired.
    """
    broken = VALID.replace(find, replace, 1)
    assert broken != VALID, "the parametrised break did not match the fixture"
    with pytest.raises(ValueError) as err:
        _load(tmp_path, broken)
    assert expect in str(err.value), str(err.value)


# ── the bypass vocabulary, and the coverage claim as data ─────────────────────


def test_every_bypass_the_suite_claims_to_reach_has_a_statement_aimed_at_it(report) -> None:
    """The coverage claim, over the real suite, in both directions.

    It used to be a paragraph at the top of ``adversarial.toml``: *"B1, B2, B4, B5 and B6 are
    statement-shaped and are here; B3, B7, B8, B9 and B10 have no SQL surface."* Nothing held it
    to the cases, so a bypass could lose its last case and the paragraph would go on saying it
    was covered — which is the failure ADR 0006's own bypass list exists to stop, one level up.

    ``bypass`` was also the one case field with no closed vocabulary while ``kind``, ``family``,
    ``enforced_by``, ``expect_layer`` and ``expect_rule`` all fail to load on a typo, so ``B4``
    mistyped as ``B44`` was a case that loaded, ran, and counted towards nothing.

    Asserted here as well as at load because the loader's version runs over whatever file it is
    handed; this one runs over the shipped suite and prints the split, so the number of bypasses
    with a statement behind them is a thing a reader can see rather than infer.
    """
    from governed_bi.govern.adversarial import BYPASSES, load_adversarial_suite

    suite = load_adversarial_suite()
    assert set(suite.bypasses) == BYPASSES, sorted(set(suite.bypasses) ^ BYPASSES)

    aimed = {c.bypass for c in suite.cases if c.bypass and c.is_attack}
    claimed = {name for name, claim in suite.bypasses.items() if claim.sql_surface}
    assert aimed == claimed, (
        f"the suite claims a statement surface for {sorted(claimed)} and aims attacks at "
        f"{sorted(aimed)}"
    )
    # The half that cannot be satisfied by declaring every bypass surfaceless: some of ADR 0006's
    # ten really are statement-shaped, and a suite that claimed none would load cleanly.
    assert claimed, "no bypass has a statement aimed at it, so this file measures no bypass"
    for name, claim in suite.bypasses.items():
        assert claim.pinned_by, f"{name} says where it is covered nowhere"

    # Every case's tag is in the vocabulary — the property the loader enforces, asserted over the
    # shipped file so a suite loaded some other way cannot be the only thing that checks it.
    assert {c.bypass for c in suite.cases if c.bypass} <= BYPASSES


@pytest.mark.parametrize(
    "find, replace, expect",
    [
        pytest.param(
            'family = "table"\nsql = "SELECT u.b',
            'bypass = "B44"\nfamily = "table"\nsql = "SELECT u.b',
            "is not one of",
            id="bypass_typo",
        ),
        pytest.param(
            'family = "table"\nsql = "SELECT u.b',
            'bypass = "B1"\nfamily = "table"\nsql = "SELECT u.b',
            "sql_surface = false` and a case names them anyway",
            id="case_tags_a_bypass_declared_surfaceless",
        ),
        pytest.param(
            '[bypass.B1]\nsql_surface = false',
            '[bypass.B1]\nsql_surface = true',
            "no attack case names them",
            id="surface_claimed_with_no_attack",
        ),
        pytest.param(
            '[bypass.B10]\nsql_surface = false\nwhy = "a probe suite aims no statement at it."\n'
            'pinned_by = "this docstring"\n',
            "",
            "says nothing about",
            id="a_bypass_left_undeclared",
        ),
        pytest.param(
            "[bypass.B10]", "[bypass.B11]", "does not name", id="a_bypass_that_is_not_in_the_list"
        ),
        pytest.param(
            '[bypass.B1]\nsql_surface = false\nwhy = "a probe suite aims no statement at it."\n',
            '[bypass.B1]\nsql_surface = false\n',
            "why",
            id="a_bypass_with_no_rationale",
        ),
        pytest.param(
            '[bypass.B1]\nsql_surface = false', '[bypass.B1]\nsql_surface = "no"',
            "must be a boolean", id="sql_surface_is_not_a_boolean",
        ),
    ],
)
def test_the_bypass_coverage_claim_must_match_the_cases(
    tmp_path: Path, find: str, replace: str, expect: str
) -> None:
    """Seven ways the declaration and the cases can disagree, each its own load failure.

    The first two are the vocabulary: an id ADR 0006 does not name, and a case filed under a
    bypass the suite says it cannot express — which would make the ``sql_surface = false`` rows
    meaningless and turn ``pinned_by`` into a pointer away from a case that is right there.

    The third is the one the paragraph could never catch: a bypass claiming a statement surface
    with no statement aimed at it. That is exactly the state the prose version would have
    survived, because prose does not notice a deleted case.
    """
    broken = VALID.replace(find, replace, 1)
    assert broken != VALID, "the parametrised break did not match the fixture"
    with pytest.raises(ValueError) as err:
        _load(tmp_path, broken)
    assert expect in str(err.value), str(err.value)


@pytest.mark.parametrize(
    "find, replace, expect",
    [
        pytest.param(
            'licensed = ["s.t"]',
            'licensed = ["s.t"]\nauthorized = ["s.t"]',
            "authorizes every table it licenses",
            id="authorizes_everything_it_licenses",
        ),
        pytest.param(
            'licensed = ["s.t"]',
            'licensed = ["s.t"]\nauthorized = ["s.absent"]',
            "no [world.tables] entry",
            id="phantom_authorization",
        ),
        pytest.param(
            'licensed = ["s.t"]',
            'licensed = ["s.t"]\ndenied_columns = ["s.t.zzz"]',
            "no [world.tables] entry",
            id="phantom_denied_column",
        ),
        pytest.param(
            'licensed = ["s.t"]',
            'licensed = ["s.t"]\n[world.row_predicate]\n"s.absent" = { expression = "x" }',
            "no [world.tables] entry",
            id="predicate_on_an_undeclared_table",
        ),
        pytest.param(
            'licensed = ["s.t"]',
            'licensed = ["s.t"]\n[world.row_predicate]\n"s.t" = { enforcement = "refuse" }',
            "needs a string `expression`",
            id="predicate_with_no_expression",
        ),
    ],
)
def test_the_worlds_authorization_declarations_must_be_real(
    tmp_path: Path, find: str, replace: str, expect: str
) -> None:
    """ADR 0012's half of the world, held to the same standard as the licence's.

    The first parameter is the one with teeth and it is the licence check's argument one rule
    over: a world that authorizes everything it licenses gives ``r_table_not_authorized``
    nothing to fire on, so an access layer that authorized everything would score perfectly —
    which is exactly why ``[world].licensed`` may not cover every declared table either.
    """
    broken = VALID.replace(find, replace, 1)
    assert broken != VALID, "the parametrised break did not match the fixture"
    with pytest.raises(ValueError) as err:
        _load(tmp_path, broken)
    assert expect in str(err.value), str(err.value)


def test_a_benign_case_may_not_declare_a_refusal(tmp_path: Path) -> None:
    """Declaring a layer for a benign case would make a false refusal look intended, which is
    the one thing the benign half exists to make impossible to hide."""
    broken = VALID.replace(
        'why = "s.t is licensed."', 'expect_layer = "TABLES"\nwhy = "s.t is licensed."', 1
    )
    with pytest.raises(ValueError, match="must not declare an expected refusal"):
        _load(tmp_path, broken)


# ── the driver runs, offline, and its exit code means something ───────────────


def test_the_driver_runs_and_reports_its_denominators() -> None:
    """``tools/govern_bench.py`` is how the number gets quoted outside pytest, and a driver
    nobody runs is a preference. Exercised through a subprocess so that its ``sys.path``
    bootstrap and its exit code are both real."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "govern_bench.py"), "--json"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    import json

    payload = json.loads(result.stdout)
    assert payload["attack"]["bypassed"] == {"n": 0, "of": payload["attack"]["caught"]["of"]}
    assert payload["benign"]["false_refusal"]["n"] == 0
    assert payload["failures"] == []
    # Every rate carries its denominator. A consumer given "0.000" cannot tell 0/48 from 0/2.
    for half in ("attack", "benign"):
        for counts in payload[half].values():
            assert set(counts) == {"n", "of"} and counts["of"] > 0


# ── the disclosure half ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def probes():
    """``tools/govern_bench.py``'s probe runner, imported by path.

    **The runner is in a tool and not in ``govern/`` on purpose.** A probe has to ask what the
    principal was *shown*, which means calling ``serve/context.py::withheld_by_grant`` and
    ``serve/fetch.py`` — both above ``govern/`` in the layer order, so ``adversarial_run.py``
    may not import them and ``tools/check_imports.py`` would say so. A tool sits above every
    layer, which is what tools are for. The cases stay data in ``adversarial.toml``, so this
    gate and the driver still read one file rather than two lists that drift.
    """
    import importlib.util

    path = ROOT / "tools" / "govern_bench.py"
    spec = importlib.util.spec_from_file_location("_govern_bench_probes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered **before** exec: the tool declares a frozen dataclass and
    # ``from __future__ import annotations`` makes its field types strings, which
    # ``dataclasses`` resolves through ``sys.modules[cls.__module__]``. Omit this and the
    # import dies in the standard library with an ``AttributeError`` about ``NoneType``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.run_disclosure_probes()


def test_nothing_the_grant_withholds_is_disclosed_on_any_surface(probes) -> None:
    """The build-failing half of the disclosure instrument.

    A ``[[case]]`` asks whether a statement was refused. A ``[[probe]]`` asks what the principal
    was shown, and the three defects an independent review found on 2026-08-12 were all of the
    second kind: ``inspect_schema`` handing over every denied column of an authorized table, a
    bare-spelled join's ON clause naming a withheld table, and ``may_sample`` failing open on a
    mixed-case denial. None of the three is expressible as SQL, and the suite as it stood
    measured only SQL — so every one of them would have come back.
    """
    leaked = [r for r in probes if r.kind == "attack" and r.failed]
    assert not leaked, "\n".join(f"{r.id} [{r.surface}]: {r.detail}" for r in leaked)


def test_the_disclosure_filter_is_a_filter_and_not_an_outage(probes) -> None:
    """The benign half, and it is not decoration.

    ``withheld_by_grant`` returning every asset id would score a perfect disclosure rate on the
    test above, exactly as ``def check(...): return {"passed": False}`` scores a perfect bypass
    rate on attacks alone. The controls include the case that keeps the denial *specific*:
    ``sales.leads`` declares a column with the same bare name as the denied one and the grant
    does not deny it, so a filter keyed on bare names fails here.
    """
    hidden = [r for r in probes if r.kind == "benign" and r.failed]
    assert not hidden, "\n".join(f"{r.id} [{r.surface}]: {r.detail}" for r in hidden)


def test_every_disclosure_surface_carries_both_an_attack_and_a_control(probes) -> None:
    """Per surface, not in aggregate. Four surfaces sharing one pool of controls would let a
    surface with only attacks read as covered — the ``by_family`` argument from the case half,
    which exists so a thin family is visible rather than inferred."""
    by_surface: dict[str, set[str]] = {}
    for r in probes:
        by_surface.setdefault(r.surface, set()).add(r.kind)
    assert by_surface, "no probes ran at all"
    thin = sorted(s for s, kinds in by_surface.items() if kinds != {"attack", "benign"})
    assert not thin, (
        f"{thin} have probes of only one kind. An attack with no control measures whether the "
        "surface returns anything; a control with no attack measures nothing."
    )
