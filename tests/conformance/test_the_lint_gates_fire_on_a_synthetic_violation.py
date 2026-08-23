"""Every conformance gate under tools/ must fire on the violation it claims to catch.

Split out of test_register_closure.py by the 1000-line cap (ADR 0005 §6), which was forcing the
timing rather than the seam: that file's own docstring names its reason for existing as
cross-table closure across the register (kept there, in its first section), and everything here
is the file's other, self-contained concern -- a synthetic-tree harness (``_gate``,
``_synthetic_tree``, ``_probe_tree``, ``_layer_packages``, ``PROBE_NAME``) shared by every test
below, each of which plants one violation and asserts the tool under test returns 1 and names it.
A gate that only leaves a trace when it fires cannot afterwards be told from a gate that was
never wired up, which is why every test here is a negative one.

Covers: the declared-but-unconsumed ratchet, the layering gate, the citation gate, the
file-length hard/soft tiers, the one-implementation-per-concept gate (including its singleton
table), and the measurement-locality gate. Every test in this file drives the real tool as a
subprocess; none re-implements a check's own logic.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

# ── the lint gates must run, and must fail on a violation ─────────────────────


@pytest.mark.parametrize(
    "tool",
    [
        "check_imports.py",
        "check_citations.py",
        "check_file_length.py",
        "check_one_implementation.py",
        "check_measurement_locality.py",
        "check_no_benchmark_discriminators.py",
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


def test_every_gate_in_tools_is_either_in_ci_or_declared_manual() -> None:
    """The list of gates and the list of gates that run must be the same list.

    They were not: the register named five and ``.github/workflows/ci.yml`` ran four, with
    ``check_citations`` — the one that fails when a retired number reappears — outside CI. And
    ``check_train_only.py`` was in neither list and referenced by nothing in ``tests/`` or
    ``.github/``, which is how a gate whose control was the corpus under test went unnoticed.

    ADR 0005 §6 calls these CI-enforced. A gate nobody runs is a preference.
    """
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gates = sorted(p.name for p in (ROOT / "tools").glob("check_*.py"))
    #: Gates that cannot run in CI, each with the precondition that stops them. Declared here
    #: so "not in CI" is a decision with a reason rather than an omission.
    manual = {
        "check_train_only.py": (
            "needs a corpus tree (untracked), the held-out question file (a separate "
            "repository) and a third corpus certified train-only"
        ),
        "check_corpus_conformance.py": (
            "needs the corpus repository and BIRD-Data-Obfuscation, both siblings of this "
            "tree; its own rules are exercised in "
            "tests/conformance/test_corpus_conformance_rules_fire.py, which does run in CI"
        ),
        "check_declared_is_consumed.py": (
            "reports 5 real violations today, down from 14 on 2026-08-11. **Tier 1 is now "
            "clear** -- all five items in that gate's tier-1 section (the note listing them, "
            "git-history:docs/analysis/declared-not-consumed.md, is deleted) are closed and "
            "each is asserted on its VALUE in "
            "tests/serve/test_the_record_follows_the_knob.py, so the condition this entry "
            "used to name is met. What still stops a CI step is the five remaining findings: "
            "the run would fail every commit, and waiving 5 genuine findings to make it "
            "green is the exact lie the gate was written to catch. Two of them "
            "(expand_hops, negative_tau) need a decision in retrieve/ rather than a wire; "
            "clarifications was the third and is now consumed by the trace route. Until then "
            "test_the_declared_but_unconsumed_set_does_not_grow below runs the gate on every "
            "commit and fails on a SIXTH -- which is the half of CI that was actually "
            "missing. Delete this entry, and that test, when the list reaches zero"
        ),
    }
    missing = [g for g in gates if g not in ci and g not in manual]
    assert not missing, (
        f"{missing} exist under tools/ and run in neither CI nor the manual list. Add a CI "
        "step, or add an entry to `manual` naming what stops it."
    )
    stale = sorted(set(manual) - set(gates))
    assert not stale, f"{stale} are declared manual and no longer exist"


#: What ``tools/check_declared_is_consumed.py`` finds on this tree, with whose decision each
#: one is waiting on. **Not a waiver list**: every entry is a real finding, and putting them in
#: the checker's ``WAIVED_*`` tuples would be the lie open-work.md §3.10 says the gate exists to
#: catch. This is a *ratchet* — the set may shrink freely and may not grow.
KNOWN_UNCONSUMED: frozenset[str] = frozenset({
    # comparability knobs with no reader: setting one moves the config hash and no behaviour
    "expand_hops",
    "negative_tau",
    # dead declarations, superseded by `llm_utility_model` (ADR 0011's two-model split)
    "facet_model",
    "rewrite_model",
    # deliberately open: the curator that would consume it is not in this repository, and
    # wiring it from the eval driver would launder it under K1's blind spot
    "build_workers",
    # `clarifications` was here until 2026-08-19, when `/audit/turns/{id}/trace` began
    # projecting it (`ThreadTurnLog.clarifications_of`). The ratchet is what made removing it
    # mandatory rather than optional.
})


def test_the_declared_but_unconsumed_set_does_not_grow() -> None:
    """The gate runs on every commit, and a **sixth** finding fails the build.

    ``check_declared_is_consumed.py`` is on the ``manual`` list above because it exits 1 on five
    real findings, and a CI step that fails every commit is a step everyone learns to ignore.
    That reasoning is sound and it left the gate running nowhere, so the thing it was written to
    prevent — a new declaration nothing consumes — could still land unnoticed. Three did between
    2026-08-11 and 2026-08-12; each happened to be wired, and nothing would have said so.

    A ratchet instead. The set is pinned by name, so the gate executes on every run, a new
    violation fails immediately with the offending name, and closing one fails too — loudly,
    with "delete it from KNOWN_UNCONSUMED", because a shrinking list nobody updates is how a
    stale count survives.

    Asserting the **names** and not the count: five findings and five different findings are the
    same integer, which is the class of defect this whole package exists for.
    """
    result = _gate("check_declared_is_consumed.py")
    found = _findings(result)

    assert not (found - KNOWN_UNCONSUMED), (
        f"new declared-but-unconsumed finding(s): {sorted(found - KNOWN_UNCONSUMED)}. "
        "Something was declared and nothing reads it — a knob outside every reader still "
        "moves the config hash, a record field nothing projects reaches no artifact, and a "
        "state channel with one end carries nothing. Wire it, delete it, or argue it into "
        "the checker's own waiver list with a reason that says why no consumer is correct."
    )
    assert not (KNOWN_UNCONSUMED - found), (
        f"{sorted(KNOWN_UNCONSUMED - found)} no longer violate the gate. Delete them from "
        "KNOWN_UNCONSUMED so the ratchet tightens; a list that outlives its findings is the "
        "stale count this test replaced."
    )
    assert result.returncode == 1, (
        "the gate now passes. Delete this test and the `manual` entry above, and add a CI "
        "step — the condition that entry names is finally met."
    )


#: One pattern per rule in ``check_declared_is_consumed.py``, because the four rules do not
#: phrase their findings the same way.
#:
#: **This is the defect the ratchet was built to catch, found in the ratchet.** The single
#: pattern here before matched ``knob 'x'`` / ``field 'x'`` / ``channel 'x'`` — K1, R1 and S1 —
#: and K2 says ``writes knobs['x']``, which it cannot match. So an undeclared key written into
#: a ``knobs`` mapping made the gate report a seventh violation and exit 1 while all three
#: assertions below still passed: ``found`` never contained the new name, so neither difference
#: was non-empty, and ``returncode == 1`` was already the expectation. K2 is the class of one of
#: the tier-1 blockers this ratchet exists to stop recurring — a key outside
#: ``comparability_keys()`` is outside the config hash, so two runs differing only in it compare
#: as one treatment.
#:
#: :func:`test_the_ratchet_can_see_every_rule_the_gate_has` holds the list to the gate's rules.
_FINDING_PATTERNS: tuple[str, ...] = (
    r"(?:knob|field|channel) '([^']+)'",  # K1, R1, S1
    r"writes knobs\['([^']+)'\]",  # K2
)


def _findings(result: subprocess.CompletedProcess[str]) -> frozenset[str]:
    """Every name the gate reported, over **both** streams.

    Both, because the gate prints its findings to stderr and its summary to stdout, and a parse
    of one of them would silently find nothing — which is how this test failed the first time it
    ran, reporting every known finding as fixed.
    """
    blob = result.stdout + result.stderr
    return frozenset(name for pattern in _FINDING_PATTERNS for name in re.findall(pattern, blob))


def test_the_ratchet_can_see_every_rule_the_gate_has(tmp_path: Path) -> None:
    """A finding this parser cannot read is a finding the ratchet does not ratchet.

    Two halves, and the second is what makes the first honest. **Rule coverage**: every
    ``rule_*`` function the gate defines must be represented, so adding a fifth rule fails here
    rather than quietly landing outside the parser. **A live probe**: an undeclared
    ``knobs[...]`` write is planted and the parser must come back with its name — pinning K2's
    exact message format, which is the half a list of function names cannot check.

    The probe goes into a **copy** of ``src/``, not a hand-built tree: the gate refuses to run
    against a root with no knob or record register ("refusing to pass vacuously"), so a
    synthetic tree would exit 1 for a reason that has nothing to do with the probe — and
    ``returncode == 1`` is what this test would otherwise be reading.
    """
    source = (ROOT / "tools" / "check_declared_is_consumed.py").read_text(encoding="utf-8")
    rules = set(re.findall(r"^def (rule_\w+)\(", source, re.MULTILINE))
    assert rules == {"rule_k1", "rule_k2", "rule_r1", "rule_s1"}, (
        f"{sorted(rules)} — the gate grew or lost a rule. Each one phrases its finding its own "
        "way, so _FINDING_PATTERNS needs an entry for the new one or the ratchet cannot see it."
    )

    shutil.copytree(
        ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    probe = tmp_path / "src" / "governed_bi" / "serve" / f"{PROBE_NAME}.py"
    probe.write_text(
        'def write(knobs: dict) -> None:\n    knobs["probe_undeclared_knob"] = 1\n',
        encoding="utf-8",
    )
    result = _gate("check_declared_is_consumed.py", "--root", str(tmp_path))
    assert result.returncode == 1, (result.stdout, result.stderr)
    found = _findings(result)
    assert "probe_undeclared_knob" in found, (
        "the gate reported an undeclared knobs[...] write and this parser did not see it. That "
        "is exactly the shape that let a K2 violation land while the ratchet stayed green:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert KNOWN_UNCONSUMED <= found, (
        "the copied tree lost findings the real one has, so this probe ran against something "
        f"other than this repository: {sorted(KNOWN_UNCONSUMED - found)}"
    )


def _gate(tool: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / tool), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


#: Probe filename, shared by every negative test below so a hit is recognisable in a gate's
#: output. It is planted in a ``tmp_path`` tree, never in this repository — see
#: :func:`_synthetic_tree`, and :func:`_probe_tree` for the gates that scan outside ``src/``.
PROBE_NAME = "_conformance_probe"


def _probe_tree(tmp: Path, rel: str, body: str) -> Path:
    """A throwaway repository root holding one file at ``rel``, for ``--root``.

    :func:`_synthetic_tree`'s argument, generalised past ``src/governed_bi/``: two of these
    gates scan ``docs/`` and ``tools/`` as well.
    """
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return tmp


def _layer_packages() -> dict[str, str]:
    """An empty package per layer ``check_imports.LAYERS`` declares.

    That gate fails on a declared layer with no package on disk, so a tree holding only the
    probe would fail for a reason the test is not about. Read from the tool, not restated.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    from check_imports import LAYERS

    return {f"{name}/__init__.py": "" for names in LAYERS for name in names}


def test_layering_gate_fires_on_a_third_party_import_in_register(tmp_path: Path) -> None:
    """Written as a negative test because a gate that only leaves a trace when it
    fires cannot afterwards be told from a gate that was never wired up."""
    modules = {**_layer_packages(), f"register/{PROBE_NAME}.py": "import pydantic\n"}
    result = _gate("check_imports.py", "--root", str(_synthetic_tree(tmp_path, modules)))
    assert result.returncode == 1
    assert "stdlib-only" in result.stderr


def test_layering_gate_fires_on_an_undeclared_package(tmp_path: Path) -> None:
    """A package ``LAYERS`` omits is checked against nothing, and the run still passes.

    ``verify/`` sat outside the layering that way, and ``record`` was declared with no package
    to be in — the same rot from the other side, so both directions are asserted.
    """
    layers = _layer_packages()
    undeclared = _synthetic_tree(tmp_path / "extra", {**layers, "smuggled/__init__.py": ""})
    result = _gate("check_imports.py", "--root", str(undeclared))
    assert result.returncode == 1
    assert "smuggled" in result.stderr and "nothing constrains" in result.stderr

    absent = dict(layers)
    del absent["register/__init__.py"]
    result = _gate("check_imports.py", "--root", str(_synthetic_tree(tmp_path / "gone", absent)))
    assert result.returncode == 1
    assert "LAYERS declares 'register'" in result.stderr


RETIRED_LITERAL = "# recall drops 0.70 -> 0.35\n"  # [retired] the gate's own fixture, not a claim


def test_citation_gate_fires_on_a_retired_literal_in_live_code(tmp_path: Path) -> None:
    root = _synthetic_tree(tmp_path, {f"register/{PROBE_NAME}.py": RETIRED_LITERAL})
    result = _gate("check_citations.py", "--root", str(root))
    assert result.returncode == 1
    assert PROBE_NAME in result.stderr


def test_citation_gate_fires_in_live_documentation(tmp_path: Path) -> None:
    """``docs`` is a strict root (same fatal tier as ``src/`` / ``tools/``)."""
    root = _probe_tree(tmp_path, f"docs/{PROBE_NAME}.md", RETIRED_LITERAL)
    result = _gate("check_citations.py", "--root", str(root))
    assert result.returncode == 1, "docs/ is a strict root and must fail the run"
    assert PROBE_NAME in result.stderr


def test_citation_gate_has_no_archive_tier() -> None:
    """Historical markdown was deleted from the working tree; no non-fatal archive root."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_check_citations", ROOT / "tools" / "check_citations.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ARCHIVE_ROOTS == ()


# ── file length: the hard cap fails, the soft cap is published ────────────────


def _declared_limits() -> tuple[int, int]:
    """The soft and hard tiers as ``tools/check_file_length.py`` defines them."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
    from check_file_length import HARD_LIMIT, SOFT_LIMIT

    return SOFT_LIMIT, HARD_LIMIT


def test_the_adr_and_the_gate_declare_the_same_file_length_tiers() -> None:
    """A limit in a table that no process reads is a preference, not a limit — the
    argument ``check_file_length.py``'s own docstring rests on, turned back on the ADR
    that declares the number.

    This is the assertion that was missing when the hard tier moved 800 -> 1000 on
    2026-08-03: the constant, the gate's prose, a conformance test's probe size, two plan
    documents and this ADR row all carried the number by hand, and the only thing that
    noticed the change was a test failing for an unrelated reason. Divergence between a
    declared limit and an enforced one is how v1's caller contract came to be documented
    and breached at the same time.
    """
    import re

    adrs = Path(__file__).resolve().parent.parent.parent / "docs" / "adr"
    adr = (adrs / "0005-v2-memory-layer-and-faceted-retrieval.md").read_text(encoding="utf-8")
    match = re.search(r"soft \*\*(\d+)\*\*, hard \*\*(\d+)\*\*", adr)
    assert match, "ADR 0005 §6 no longer states the file-length tiers in a parseable form"
    assert (int(match.group(1)), int(match.group(2))) == _declared_limits(), (
        f"ADR 0005 §6 declares soft/hard {match.group(1)}/{match.group(2)}; "
        f"tools/check_file_length.py enforces {_declared_limits()}. One of them is lying "
        "to a reader, and the enforced one wins silently."
    )


def test_file_length_gate_fires_over_the_hard_cap(tmp_path: Path) -> None:
    """ADR 0005 §6 declared soft 400 / hard 1000 "CI-enforced" and for a while
    nothing enforced it, which is the same defect as v1's caller contract that was
    documented and breached. v1 reached 17 files over 1,000 lines, one at 5,085, and
    30% of its code lived in them.

    The probe size is **derived** from the gate's own constant. It was hand-written as
    ``801`` until the hard tier moved to 1000, at which point this test failed while
    saying nothing about the actual change — a stale duplicate of a number, which is the
    defect class §6 forbids two rows below the one it was enforcing.
    """
    over = "x = 0\n" * (_declared_limits()[1] + 1)
    root = _synthetic_tree(tmp_path, {f"{PROBE_NAME}.py": over})
    result = _gate("check_file_length.py", "--root", str(root))
    assert result.returncode == 1
    assert PROBE_NAME in result.stderr


def test_file_length_gate_publishes_a_soft_overrun_without_failing(tmp_path: Path) -> None:
    """The soft tier is a *tier*, not a warning nobody prints.

    A soft cap that says nothing when it is exceeded cannot be told from a soft cap
    that was never wired up — the same argument the archive tier in
    ``check_citations.py`` rests on. So this asserts the printed output *moved*, not
    merely that the run passed: the count is what makes a new overrun visible.
    """
    soft, hard = _declared_limits()
    quiet = _synthetic_tree(tmp_path / "quiet", {"small.py": "x = 0\n"})
    baseline = _gate("check_file_length.py", "--root", str(quiet))
    assert baseline.returncode == 0

    over = "x = 0\n" * ((soft + hard) // 2)
    loud = _synthetic_tree(tmp_path / "loud", {"small.py": "x = 0\n", f"{PROBE_NAME}.py": over})
    result = _gate("check_file_length.py", "--root", str(loud))

    assert result.returncode == 0, "the soft cap must never fail the run"
    assert PROBE_NAME in result.stdout
    assert result.stdout != baseline.stdout, (
        "the soft-cap count did not change, so this tier is a silent allowance "
        "rather than a published one"
    )


# ── one implementation per concept ────────────────────────────────────────────


def test_duplicate_concept_gate_fires_on_a_duplicate_top_level_name(tmp_path: Path) -> None:
    """v1 had two McNemars, three temp-then-replace helpers (and **none** of the
    three was durable, which is how the run ledger lost 312 of 320 records), and
    two ``LOW_CONFIDENCE_JOIN`` constants with different comparison
    operators. With the layers parcelled to parallel agents, none of which can
    import a module its neighbour has not written yet, a second implementation is
    the default outcome rather than a slip — so the gate defaults to deny and this
    asserts the deny actually fires.
    """
    twice = "def gate_keys() -> None:\n    ...\n"
    root = _synthetic_tree(
        tmp_path, {"first.py": twice, f"{PROBE_NAME}.py": twice}
    )
    result = _gate("check_one_implementation.py", "--root", str(root))
    assert result.returncode == 1
    assert PROBE_NAME in result.stderr
    assert "gate_keys" in result.stderr


def _singleton_concepts() -> tuple[object, ...]:
    """The gate's own ``SINGLETON_CONCEPTS``, imported rather than restated.

    Importing the tool is safe: its module level is declarations only and ``main()`` is
    behind ``__name__``. Restating the table here would be the very defect the gate
    exists to catch — two tables that must agree — and it would show up as these tests
    passing against a set that no longer matches the tool's.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_check_one_impl", ROOT / "tools" / "check_one_implementation.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.SINGLETON_CONCEPTS)


#: A minimal tree in which every declared singleton resolves.
#:
#: One-line bodies, because the gate is AST-only and does not care what a definition
#: contains. Built from the tool's table so adding a concept cannot leave these tests
#: asserting against a stale set.
_SINGLETON_HOMES: dict[str, str] = {}
for _s in _singleton_concepts():
    _name, _module = _s.name, _s.module  # type: ignore[attr-defined]
    _body = (
        f"class {_name}: ..."
        if _name[:1].isupper()
        else f"def {_name}() -> None: ..."
    )
    # Append rather than assign: two concepts declared in one module must both appear,
    # or this fixture would silently drop one and the "absent from its home" test would
    # pass for the wrong reason.
    _SINGLETON_HOMES[_module] = (_SINGLETON_HOMES.get(_module, "") + _body + "\n").lstrip("\n")


def _synthetic_tree(tmp: Path, modules: dict[str, str]) -> Path:
    """A throwaway ``src/governed_bi/`` tree for pointing a gate at.

    **No test writes into the real ``src/`` to exercise a gate.** The two tests below
    used to, with ``corpus/hash.py`` as a scratch file — chosen because that path was
    expected to stay absent. Parcel D built it, and from then on the suite
    ``write_text``-ed over real source and then ``unlink``-ed it; the ``rmdir`` in the
    ``finally`` raised as well, because ``corpus/`` was no longer empty. Five
    downstream tests failed with ``ModuleNotFoundError``, and because
    ``pytest-randomly`` shuffles order, *which* five varied per run.

    The lesson is general enough to be worth stating: **a test that writes to a
    production path is a test that will eventually overwrite production code.** An
    assumption that a path stays absent is an assumption about the future, and this
    one expired. So the gate takes ``--root`` and the test owns the tree.
    """
    pkg = tmp / "src" / "governed_bi"
    for rel, body in modules.items():
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp


def test_duplicate_concept_gate_fires_when_a_singleton_is_absent_from_its_home(
    tmp_path: Path,
) -> None:
    """A declared concept whose module exists but does not define it is fatal.

    Distinct from the pending tier: pending means "not built yet", which is scheduled
    work. This means "built somewhere else", which is the drift the table exists to
    catch — and reporting it as pending would be a green tick over exactly that.
    """
    homes = dict(_SINGLETON_HOMES)
    homes["measure/stats.py"] = "def something_else() -> None: ..."  # mcnemar is missing
    root = _synthetic_tree(tmp_path, homes)
    result = _gate("check_one_implementation.py", "--root", str(root))
    assert result.returncode == 1
    assert "mcnemar" in result.stderr


def test_duplicate_concept_gate_reports_a_pending_singleton_without_failing(
    tmp_path: Path,
) -> None:
    """A gate whose targets are unbuilt must not read as passing.

    Asserts the count *moves*, not merely that the run is green: a silent skip and a
    clean pass produce the same exit code, and half this repo's retired numbers have
    that shape. Same argument as the archive tier in ``check_citations.py``.

    This used to run against the real tree, where ``corpus_content_hash`` was the last
    unbuilt singleton. Parcel D built it, so the real tree now reports ``0 pending``
    and the premise died — which is itself the argument for a synthetic tree: a test
    whose premise is a transient property of the repository expires without warning.
    """
    built = dict(_SINGLETON_HOMES)
    everything = _gate(
        "check_one_implementation.py", "--root", str(_synthetic_tree(tmp_path / "all", built))
    )
    assert everything.returncode == 0, everything.stdout + everything.stderr
    assert "0 pending" in everything.stdout

    missing = dict(built)
    del missing["corpus/hash.py"]
    partial = _gate(
        "check_one_implementation.py", "--root", str(_synthetic_tree(tmp_path / "partial", missing))
    )
    assert partial.returncode == 0, (
        "pending is not fatal; failing the build for scheduled work trains people to "
        "disable the gate"
    )
    assert "PENDING" in partial.stdout
    assert partial.stdout != everything.stdout, (
        "the pending count did not change, so the tier is a silent skip rather than a "
        "reported one"
    )


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
def test_measurement_locality_gate_fires_on_formatting_outside_quantity(
    source: str, tmp_path: Path
) -> None:
    """v1's rounding helpers turned an unmeasured quantity into ``0.0`` on the way
    to a report: the value was honest right up to the last function that touched it.
    The sibling incident from the same family is a ``:.3f`` on a ``None`` rate that
    raised after the whole serve loop and before ``summary.json`` was written,
    discarding hours of paid model calls to print a progress line.

    Parametrised over all four detected constructs because they are four different
    code paths in the checker, and a single case passing would leave three that
    might never have been wired up. ``round(`` is checked as an AST call, not by
    grep, precisely so that a docstring can go on quoting ``round(x or 0.0, n)``
    while explaining the rule.
    """
    root = _synthetic_tree(tmp_path, {f"{PROBE_NAME}.py": source})
    result = _gate("check_measurement_locality.py", "--root", str(root))
    assert result.returncode == 1
    assert PROBE_NAME in result.stderr


