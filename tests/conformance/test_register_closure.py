"""Closure checks that need to import across the whole stack.

Why this package exists at all: **neither end of a declaration can prove closure
without an upward import.** The register declares fields that stages produce, and
naming a producer as a ``Stage`` member is what keeps the dependency pointing
downward — but "every declared field is actually written by that stage" cannot be
checked from the bottom, and "every emitted field is declared" cannot be checked
from the top without the top importing the bottom's checker. So closure is proven
where an upward import is legal: here.

**Every test in this file drives the real function.** None re-implements a check's
arithmetic. v1's gold-gate tests re-derived ``share > THRESHOLD`` themselves, so
deleting the gate, flipping the comparison, and reversing the denominator all
passed — three ways to break a security-relevant gate with a green suite. The
authoring rules that came out of that are in ``docs/lessons-from-v1.md`` §7 and are
applied here:

* Assert on the **effect** (does the guard raise?), not on the presence of a
  constant.
* **Never assert a module against its own constant** — that passes for an empty
  tuple.
* A guard that leaves a trace only when it fires cannot be told from a guard that
  was never wired up, so the negative case is tested too.

Still pending, and marked ``xfail(strict=True)`` rather than omitted so it cannot
be forgotten: the assertion that a **real turn on every terminal path** writes
every required field. That needs the graph, which does not exist yet. Strict xfail
means it fails the suite the moment it starts passing, which is the point at which
someone must come back and turn it into a real test — a non-strict xfail would
XPASS in silence and nobody would learn the thing started working.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from governed_bi.register import assets, citations, facets, knobs, record, stages

ROOT = Path(__file__).resolve().parent.parent.parent


# ── cross-table closure: the reason this package exists ────────────────────────


def test_every_facet_target_is_a_real_asset_type() -> None:
    """``FACET_TARGETS`` is keyed on the enum, so this is a type error rather than a
    runtime one — but the *union* still has to cover the index, which no type says."""
    retrieved: set[assets.AssetType] = set()
    for targets in facets.FACET_TARGETS.values():
        retrieved |= targets
    reachable = retrieved | facets.GATE_CONSUMED_TYPES
    assert reachable == assets.INDEXED_TYPES, (
        "an indexed asset type that no facet retrieves and no gate consumes is "
        "unreachable — which is exactly how v1's negative examples were embedded, "
        "budgeted at zero by a dict default, and never retrievable"
    )


def test_every_gate_reads_a_declared_field() -> None:
    assert record.gate_keys() <= record.record_keys()


def test_every_health_field_is_read_by_a_gate() -> None:
    """The ``health`` tier's definition is "every one of these is a quotability
    input". A health field no gate reads is the v1 incident where a degradation
    counter reached ``summary.json`` and ``quotable()`` read neither it nor its
    rate."""
    health = {f.name for f in record.RECORD_REGISTER if f.tier is record.Tier.health}
    assert health <= record.gate_keys()


def test_every_record_owner_is_a_real_stage() -> None:
    for field in record.RECORD_REGISTER:
        assert isinstance(field.owner, stages.Stage), field.name


def test_every_asset_type_has_a_policy_row() -> None:
    """``budgets.get(cls, 0)`` is the shape to keep unrepresentable."""
    assert set(assets.ASSET_REGISTER) == set(assets.AssetType)


def test_resume_drift_is_a_strict_superset_of_comparability() -> None:
    """Two runs at different commits are the normal comparison; the same difference
    inside one run directory is corrupting. The sets therefore differ, and the
    difference is where v1 lost 1025 rows and 326 rows into one arm score."""
    comp, drift = knobs.comparability_keys(), knobs.resume_drift_keys()
    assert comp < drift
    assert "git_sha" in drift and "git_sha" not in comp


# ── the guards must actually fire ──────────────────────────────────────────────


def test_presence_test_rejects_a_record_of_nulls() -> None:
    """The check that makes ``missing_required`` more than a rubber stamp.

    ``project`` writes every declared key, so key-presence alone always passes. This
    is the same defect as v1's ``corpus_content_hash == "unknown"`` comparing equal
    to itself and letting two runs with no recorded treatment pass comparability.
    """
    all_null = {f.name: None for f in record.RECORD_REGISTER}
    assert record.missing_required(all_null) == record.required_keys()


def test_a_refusal_path_record_passes() -> None:
    """The complement, and the reason eight fields are stage-conditional.

    A guard-blocked turn reaches ``stamp`` without running the facets, ``connect``
    or the agent loop. Declaring those fields ``never`` would either fail every
    refusal or force an empty-collection encoding — and an empty ``facet_channels``
    reads as *clean* to a gate looking for degradation.
    """
    rec = {f.name: None for f in record.RECORD_REGISTER}
    for f in record.RECORD_REGISTER:
        if f.absence is record.Absence.never:
            rec[f.name] = [] if f.name == "usage" else "stub"
    assert not record.missing_required(rec)


def test_unset_knobs_refuse_truth_testing() -> None:
    """``if not permitted_functions`` must not silently read as "empty allowlist"."""
    with pytest.raises(TypeError):
        bool(knobs.UNSET)
    unset = [k.name for k in knobs.KNOB_REGISTER if k.default is knobs.UNSET]
    assert "permitted_functions" in unset
    assert "negative_tau" in unset


def test_expected_channel_state_refuses_a_non_facet() -> None:
    with pytest.raises(KeyError):
        facets.expected_channel_state(stages.Stage.route, facets.Channel.lexical)


def test_unconfigured_where_configured_is_degradation() -> None:
    """A channel that silently stops being wired up must not pass a gate that only
    looks for ``failed``."""
    assert facets.is_degraded(
        stages.Stage.facet_entity, facets.Channel.lexical, facets.ChannelState.not_configured
    )
    assert not facets.is_degraded(
        stages.Stage.facet_example, facets.Channel.lexical, facets.ChannelState.not_configured
    )


def test_extra_channel_is_drift_not_degradation() -> None:
    """More retrieval than declared is worth reporting and must not refuse a run."""
    anomaly = facets.channel_anomaly(
        stages.Stage.facet_example, facets.Channel.lexical, facets.ChannelState.ran
    )
    assert anomaly is facets.Anomaly.extra_channel
    assert not facets.is_degraded(
        stages.Stage.facet_example, facets.Channel.lexical, facets.ChannelState.ran
    )


def test_cap_classifies_as_capped_not_refused() -> None:
    """A governance-terminated turn counted as a refusal — or as a crash — is the
    inversion that retired a set of numbers."""
    outcome = stages.classify_outcome(
        error=None, refused_by=stages.ATTEMPT_CAP_REFUSED_BY, has_sql=False
    )
    assert outcome is stages.Outcome.capped
    assert stages.REFUSED_BY_TO_STAGE[stages.ATTEMPT_CAP_REFUSED_BY] is stages.Stage.cap


def test_model_error_classifies_as_crashed_even_with_sql() -> None:
    """A crash wearing a refusal's clothes. v1 pooled these and every arm-to-arm
    delta was contaminated by a different amount, because arms do not crash at the
    same rate."""
    assert (
        stages.classify_outcome(error=None, refused_by="model_error", has_sql=True)
        is stages.Outcome.crashed
    )


def test_exec_error_is_an_answer_not_a_crash() -> None:
    """SQLite wraps "no such column" in ``OperationalError``, so classifying that
    family as infrastructure hides wrong answers as crashes."""
    assert (
        stages.classify_outcome(
            error="exec_error: no such column", refused_by=None, has_sql=True
        )
        is stages.Outcome.answered
    )


def test_no_sql_and_no_refusal_is_a_crash() -> None:
    """A turn that decided nothing did not refuse. Calling it a refusal is the
    original defect."""
    assert (
        stages.classify_outcome(error=None, refused_by=None, has_sql=False)
        is stages.Outcome.crashed
    )


def test_every_retired_pattern_matches_its_observed_spelling() -> None:
    """A pattern that matches nothing real is a gate that catches nothing — which
    one of v1's retired-literal entries actually was."""
    import re

    for claim in citations.RETIRED_CLAIMS:
        assert re.search(claim.pattern, claim.observed), claim.pattern


def test_every_citation_has_an_artifact_and_a_date() -> None:
    for c in citations.CITATIONS:
        assert c.artifact and c.measured, c.claim[:60]


# ── the lint gates must run, and must fail on a violation ─────────────────────


@pytest.mark.parametrize(
    "tool",
    [
        "check_imports.py",
        "check_citations.py",
        "check_file_length.py",
        "check_one_implementation.py",
        "check_measurement_locality.py",
    ],
)
def test_lint_gate_passes_on_a_clean_tree(tool: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / tool)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _gate(tool: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / tool)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


#: Every negative test writes here. One path, so a crashed test leaves at most one
#: file behind and every gate's probe is findable by the same name.
PROBE = ROOT / "src" / "governed_bi" / "register" / "_conformance_probe.py"


def test_layering_gate_fires_on_a_third_party_import_in_register(tmp_path: Path) -> None:
    """Written as a negative test because a gate that only leaves a trace when it
    fires cannot afterwards be told from a gate that was never wired up."""
    probe = ROOT / "src" / "governed_bi" / "register" / "_conformance_probe.py"
    probe.write_text("import pydantic\n", encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_imports.py")],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 1
        assert "stdlib-only" in result.stderr
    finally:
        probe.unlink()


RETIRED_LITERAL = "# recall drops 0.70 -> 0.35\n"


def _citation_gate() -> subprocess.CompletedProcess[str]:
    return _gate("check_citations.py")


def test_citation_gate_fires_on_a_retired_literal_in_live_code() -> None:
    probe = ROOT / "src" / "governed_bi" / "register" / "_conformance_probe.py"
    probe.write_text(RETIRED_LITERAL, encoding="utf-8")
    try:
        result = _citation_gate()
        assert result.returncode == 1
        assert "_conformance_probe" in result.stderr
    finally:
        probe.unlink()


def test_citation_gate_fires_in_live_documentation() -> None:
    """``docs`` graduated from advisory to fatal once the v1 archive moved out.

    Tested separately from the ``src/`` case because it is a different code path —
    ``docs`` is scanned with a subtree exclusion — and because "advisory" and
    "fatal" produce the same output when there happens to be nothing to report.
    """
    probe = ROOT / "docs" / "_conformance_probe.md"
    probe.write_text(RETIRED_LITERAL, encoding="utf-8")
    try:
        result = _citation_gate()
        assert result.returncode == 1, "docs/ is a strict root and must fail the run"
        assert "_conformance_probe" in result.stderr
    finally:
        probe.unlink()


def test_citation_gate_tolerates_the_archive_but_counts_it() -> None:
    """The complement, and the reason the archive is a *tier* and not an exemption.

    A v1 experiment record stating what was measured on a date is a true statement
    and must stay unedited, so a hit there cannot be fatal. But an exemption that
    printed nothing would make a growing archive invisible, so the count is
    published — and this asserts the count moved, not merely that the run passed.
    """
    before = _citation_gate()
    assert before.returncode == 0
    baseline = before.stdout

    probe = ROOT / "docs" / "v1" / "_conformance_probe.md"
    probe.write_text(RETIRED_LITERAL, encoding="utf-8")
    try:
        result = _citation_gate()
        assert result.returncode == 0, "an archived record must not fail the run"
        assert result.stdout != baseline, (
            "the archive count did not change, so this tier is a silent exemption "
            "rather than a reported one"
        )
    finally:
        probe.unlink()


# ── file length: the hard cap fails, the soft cap is published ────────────────


def test_file_length_gate_fires_over_the_hard_cap() -> None:
    """ADR 0005 §6 declared soft 400 / hard 800 "CI-enforced" and for a while
    nothing enforced it, which is the same defect as v1's caller contract that was
    documented and breached. v1 reached 17 files over 1,000 lines, one at 5,085, and
    30% of its code lived in them — every one of those passed through 800 first.
    """
    PROBE.write_text("x = 0\n" * 801, encoding="utf-8")
    try:
        result = _gate("check_file_length.py")
        assert result.returncode == 1
        assert "_conformance_probe" in result.stderr
    finally:
        PROBE.unlink()


def test_file_length_gate_publishes_a_soft_overrun_without_failing() -> None:
    """The soft tier is a *tier*, not a warning nobody prints.

    A soft cap that says nothing when it is exceeded cannot be told from a soft cap
    that was never wired up — the same argument the archive tier in
    ``check_citations.py`` rests on. So this asserts the printed output *moved*, not
    merely that the run passed: one accepted overrun exists today
    (``register/record.py``, a recorded decision), and the count is what makes a
    second one visible.
    """
    before = _gate("check_file_length.py")
    assert before.returncode == 0
    baseline = before.stdout

    PROBE.write_text("x = 0\n" * 500, encoding="utf-8")
    try:
        result = _gate("check_file_length.py")
        assert result.returncode == 0, "the soft cap must never fail the run"
        assert "_conformance_probe" in result.stdout
        assert result.stdout != baseline, (
            "the soft-cap count did not change, so this tier is a silent allowance "
            "rather than a published one"
        )
    finally:
        PROBE.unlink()


# ── one implementation per concept ────────────────────────────────────────────


def test_duplicate_concept_gate_fires_on_a_duplicate_top_level_name() -> None:
    """v1 had two McNemars, two EX definitions, two temp-then-replace helpers (and
    **none** of the three was durable, which is how the run ledger lost 16 of 17
    records), and two ``LOW_CONFIDENCE_JOIN`` constants with different comparison
    operators. With the layers parcelled to parallel agents, none of which can
    import a module its neighbour has not written yet, a second implementation is
    the default outcome rather than a slip — so the gate defaults to deny and this
    asserts the deny actually fires.
    """
    PROBE.write_text("def gate_keys() -> None:\n    ...\n", encoding="utf-8")
    try:
        result = _gate("check_one_implementation.py")
        assert result.returncode == 1
        assert "_conformance_probe" in result.stderr
        assert "gate_keys" in result.stderr
    finally:
        PROBE.unlink()


def test_duplicate_concept_gate_fires_when_a_singleton_is_absent_from_its_home() -> None:
    """The complement of the pending tier below, and the reason pending is safe.

    Pending means "the module does not exist yet". Once it exists, the declared name
    must be in it — otherwise a concept could be built somewhere else entirely while
    the gate went on reporting it as scheduled work, which is a green tick over the
    exact drift the table exists to prevent.
    """
    home = ROOT / "src" / "governed_bi" / "corpus" / "hash.py"
    home.parent.mkdir(parents=True, exist_ok=True)
    home.write_text("def something_else() -> None:\n    ...\n", encoding="utf-8")
    try:
        result = _gate("check_one_implementation.py")
        assert result.returncode == 1
        assert "corpus_content_hash" in result.stderr
    finally:
        home.unlink()
        home.parent.rmdir()


def test_duplicate_concept_gate_reports_a_pending_singleton_without_failing() -> None:
    """A gate whose targets are unbuilt must not read as passing.

    Most v2 layers do not exist yet, so most declared singletons are unenforceable
    today. Failing on them would train people to disable the gate; skipping them
    silently is worse, because **a pass and a silent skip print the same green
    tick** — the argument that earns ``check_citations.py`` its archive count. So
    this asserts both halves: the pending run passes *and* says how many concepts
    are unenforced, and resolving one changes what is printed.
    """
    before = _gate("check_one_implementation.py")
    assert before.returncode == 0, before.stdout + before.stderr
    assert "PENDING" in before.stdout, (
        "the run reports no pending singletons, so either every declared concept "
        "has a home now or the tier prints nothing and is a silent skip"
    )
    baseline = before.stdout

    home = ROOT / "src" / "governed_bi" / "corpus" / "hash.py"
    home.parent.mkdir(parents=True, exist_ok=True)
    home.write_text("def corpus_content_hash() -> None:\n    ...\n", encoding="utf-8")
    try:
        result = _gate("check_one_implementation.py")
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout != baseline, (
            "resolving a pending singleton did not change the output, so the "
            "pending count is not published and 'unenforced' is indistinguishable "
            "from 'enforced and clean'"
        )
    finally:
        home.unlink()
        home.parent.rmdir()


# ── measurement locality ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "rate = round(0.5, 2)\n",
        'text = f"{0.5:.2f}"\n',
        'text = "{:.1%}".format(0.5)\n',
        'text = "%.3f" % 0.5\n',
    ],
    ids=["round", "fstring_spec", "str_format", "percent_format"],
)
def test_measurement_locality_gate_fires_on_formatting_outside_quantity(source: str) -> None:
    """v1's rounding helpers turned an unmeasured quantity into ``0.0`` on the way
    to a report: the value was honest right up to the last function that touched it.
    The sibling incident from the same family is a ``:.3f`` on a ``None`` rate that
    raised after the whole serve loop and before ``summary.json`` was written,
    discarding hours of paid model calls to print a progress line.

    Parametrised over all four detected constructs because they are four different
    code paths in the checker, and a single case passing would leave three that
    might never have been wired up. ``round(`` is checked as an AST call, not by
    grep, precisely so that ``register/record.py`` and ``register/quantity.py`` can
    go on quoting ``round(x or 0.0, n)`` in prose while explaining the rule.
    """
    PROBE.write_text(source, encoding="utf-8")
    try:
        result = _gate("check_measurement_locality.py")
        assert result.returncode == 1
        assert "_conformance_probe" in result.stderr
    finally:
        PROBE.unlink()


# ── pending: needs the graph ───────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="needs the serve graph; ADR 0005 step 11")
def test_a_real_turn_writes_every_required_field_on_every_terminal_path() -> None:
    """The assertion this package exists for, and the half v1 skipped.

    Its presence test ran against fixtures, so it never met the case that matters:
    a refusal path, where the stage-conditional fields are null and the required
    ones must still be written. Strict xfail so that the moment the graph exists and
    this starts passing, the suite fails until someone replaces it with the real
    thing.
    """
    raise NotImplementedError("no graph yet")
