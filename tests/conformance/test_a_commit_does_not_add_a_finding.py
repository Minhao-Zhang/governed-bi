"""``tools/check_corpus_delta.py`` on a throwaway corpus repository: git is the baseline.

The ratchet's pin file answers "is this tree's debt what we declared". This answers a different
question — "did *this commit* add anything" — and the difference is the whole reason the tool
exists. So the cases below are chosen to separate them: closing a finding fails the ratchet
(``test_the_ratchet_only_turns_one_way.py``) and passes here, and a rule that got stricter reds a
pin-based build and produces no delta here.

Every repository in this file is created under pytest's ``tmp_path``. The tool runs
``git worktree add`` on whatever ``--corpus-dir`` names, which writes into that repository's
``.git`` — so it is never pointed at a real corpus from a test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
DELTA = ROOT / "tools" / "check_corpus_delta.py"
CONFORMANCE = ROOT / "tools" / "check_corpus_conformance.py"

#: A metric whose expression calls `DIVIDE`: it parses as SQL and names a function no dialect has,
#: so it is a V17a *finding* and not a parse error. Borrowed from the ratchet's fixtures, which
#: measured it producing exactly one finding.
_METRIC = """asset_type: metric
id: shop.conversion
name: conversion
base_table: shop.orders
expression: DIVIDE(COUNT(order_id), COUNT(*))
summary: conversion divides the counted orders by the total rows.
body: >-
  The share of shop.orders rows that carry an order id.
"""

#: Two `DIVIDE` calls, so V17a reports **two** findings on one identity. The identity does not
#: grow; the count does.
_METRIC_TWO_CALLS = """asset_type: metric
id: shop.conversion
name: conversion
base_table: shop.orders
expression: DIVIDE(DIVIDE(COUNT(order_id), COUNT(*)), COUNT(*))
summary: conversion divides the counted orders by the total rows.
body: >-
  The share of shop.orders rows that carry an order id.
"""

_CLEAN_METRIC = """asset_type: metric
id: shop.order_count
name: order_count
base_table: shop.orders
expression: COUNT(order_id)
summary: order_count counts the orders in the shop.orders table.
body: >-
  One per row of shop.orders.
"""

#: A second clean metric, so "a commit that changes nothing relevant" has something to change.
_SECOND_CLEAN_METRIC = """asset_type: metric
id: shop.distinct_orders
name: distinct_orders
base_table: shop.orders
expression: COUNT(DISTINCT order_id)
summary: distinct_orders counts the separate order ids in the shop.orders table.
body: >-
  One per distinct order id of shop.orders.
"""

_TABLE = """asset_type: table
id: shop.orders
schema: shop
physical_name: orders
summary: orders holds one row per placed order in the shop schema.
body: >-
  Grain is one order.
columns:
  - name: order_id
    summary: The identifier of this order row.
"""

#: The held-out question V12 forbids, and the table body that quotes it. Longer than the rule's
#: 25-character floor, which is what stops a common phrase reading as a leak.
_HELD_OUT_QUESTION = "how many orders were placed by a buyer whose rating is above four"

_TABLE_QUOTING_THE_SPLIT = f"""asset_type: table
id: shop.orders
schema: shop
physical_name: orders
summary: orders holds one row per placed order in the shop schema.
body: >-
  Grain is one order. A reader often asks {_HELD_OUT_QUESTION}.
columns:
  - name: order_id
    summary: The identifier of this order row.
"""


# ── fixtures ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, f"git {' '.join(args)}:\n{done.stdout}\n{done.stderr}"
    return done


def _repo(root: Path) -> Path:
    """A git repository holding a two-asset corpus, one commit deep and clean.

    ``core.autocrlf=false`` is set deliberately: with git's Windows default the base worktree
    checkout and the working tree would differ by line ending alone, and the delta would be
    measuring the checkout rather than the commit.
    """
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", str(root)],
        check=True,
        capture_output=True,
    )
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Delta Test")
    _git(root, "config", "core.autocrlf", "false")
    _write(root, "shop/tables/tbl_shop_orders.yaml", _TABLE)
    _write(root, "shop/metrics/metric_clean.yaml", _CLEAN_METRIC)
    _commit(root, "clean")
    return root


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _run(corpus: Path, base: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DELTA), "--corpus-dir", str(corpus), "--base", base, *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )


def _conformance(corpus: Path, *extra: str) -> dict:
    done = subprocess.run(
        [sys.executable, str(CONFORMANCE), "--corpus-dir", str(corpus), "--json", *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


def _empty_dataset(tmp_path: Path) -> Path:
    """A dataset directory with no manifests in it, so V11/V12/V15 cannot run.

    Passed explicitly rather than relying on the sibling checkout being absent: on the machine
    this was written on ``../BIRD-Data-Obfuscation/eval_dataset`` exists, so a test that assumed
    the manifests were missing would pass here and assert nothing.
    """
    path = tmp_path / "no-dataset"
    path.mkdir(exist_ok=True)
    return path


# ── the delta ─────────────────────────────────────────────────────────────────


def test_a_commit_that_introduces_a_finding_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "corpus")
    _write(repo, "shop/metrics/metric_conversion.yaml", _METRIC)

    done = _run(repo, "HEAD")
    assert done.returncode == 1, done.stdout + done.stderr
    out = done.stdout + done.stderr
    assert "V17a" in out, out
    assert "metric_conversion.yaml:shop.conversion" in out, out
    # The message and not just the identity. A red CI log has to be actionable without a local
    # re-run, and `[V17a] metric_conversion.yaml:shop.conversion` alone does not say what is wrong.
    assert "divide" in out.lower(), f"the message must be printed:\n{out}"


def test_a_commit_that_changes_nothing_relevant_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "corpus")
    _write(repo, "shop/metrics/metric_distinct_orders.yaml", _SECOND_CLEAN_METRIC)

    done = _run(repo, "HEAD")
    assert done.returncode == 0, done.stdout + done.stderr


def test_closing_a_finding_passes_unlike_the_ratchet(tmp_path: Path) -> None:
    """The case that motivates the whole tool.

    ``check_ratchet.py`` fails here until the pin file is rewritten in the same commit. There is no
    file to rewrite, so a fix is simply green.
    """
    repo = _repo(tmp_path / "corpus")
    dirty = _write(repo, "shop/metrics/metric_conversion.yaml", _METRIC)
    _commit(repo, "add a finding")
    dirty.unlink()

    done = _run(repo, "HEAD")
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_second_finding_on_the_same_identity_fails_with_the_count(tmp_path: Path) -> None:
    """One ``DIVIDE`` becomes two. The ``(rule, where)`` set does not grow; the count does.

    Without counts the set difference is empty and the build is green, which is exactly the hole
    the ratchet's pins record a count to close.
    """
    repo = _repo(tmp_path / "corpus")
    _write(repo, "shop/metrics/metric_conversion.yaml", _METRIC)
    _commit(repo, "add a finding")
    _write(repo, "shop/metrics/metric_conversion.yaml", _METRIC_TWO_CALLS)

    done = _run(repo, "HEAD")
    assert done.returncode == 1, done.stdout + done.stderr
    out = done.stdout + done.stderr
    assert "1 -> 2" in out, out
    assert "metric_conversion.yaml:shop.conversion" in out, out


def test_a_new_file_carrying_a_finding_fails(tmp_path: Path) -> None:
    """A file git has never seen is absent from the base tree, so all of its findings are added."""
    repo = _repo(tmp_path / "corpus")
    _write(repo, "shop/metrics/metric_conversion.yaml", _METRIC)

    done = _run(repo, "HEAD")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "shop.conversion" in done.stdout + done.stderr


def test_a_deleted_file_that_carried_a_finding_passes(tmp_path: Path) -> None:
    """The mirror of the case above, and it must fall out of the same set difference."""
    repo = _repo(tmp_path / "corpus")
    dirty = _write(repo, "shop/metrics/metric_conversion.yaml", _METRIC)
    base = _commit(repo, "add a finding")
    dirty.unlink()

    done = _run(repo, base)
    assert done.returncode == 0, done.stdout + done.stderr


# ── could not run ─────────────────────────────────────────────────────────────


def test_a_bad_base_ref_exits_two_and_says_which(tmp_path: Path) -> None:
    """Exit 2 and not 1: an unresolvable ref is "could not run", not "you made it worse"."""
    repo = _repo(tmp_path / "corpus")

    done = _run(repo, "no-such-ref")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "no-such-ref" in done.stderr, done.stderr


def test_a_corpus_that_is_not_a_git_repository_exits_two(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    _write(plain, "shop/tables/tbl_shop_orders.yaml", _TABLE)

    done = _run(plain, "HEAD")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "git" in done.stderr.lower(), done.stderr


def test_the_base_worktree_is_removed_even_when_the_run_fails(tmp_path: Path) -> None:
    """Cleanup on the failure path, checked against git's own bookkeeping.

    A leaked worktree writes a directory into the corpus repository's ``.git/worktrees`` and pins
    a checkout in the temp directory. On the next run the same commit is still registered, and
    a corpus repository accumulates one per red build.
    """
    repo = _repo(tmp_path / "corpus")
    _write(repo, "shop/metrics/metric_conversion.yaml", _METRIC)

    assert _run(repo, "HEAD").returncode == 1
    listed = _git(repo, "worktree", "list", "--porcelain").stdout
    assert listed.count("worktree ") == 1, f"a worktree leaked:\n{listed}"
    assert not (repo / ".git" / "worktrees").exists(), "git worktree bookkeeping was left behind"


def test_every_rule_must_run_is_fatal_when_a_manifest_is_absent(tmp_path: Path) -> None:
    """Exit 2, naming the rules, because a rule that could not run looks exactly like one that
    passed.

    If the dataset checkout silently fails in CI, V11/V12/V15 run on neither side, the delta is
    empty and the build is green with the leakage gate never having executed.
    """
    repo = _repo(tmp_path / "corpus")

    done = _run(repo, "HEAD", "--dataset-dir", str(_empty_dataset(tmp_path)),
                "--every-rule-must-run")
    assert done.returncode == 2, done.stdout + done.stderr
    for rule in ("V11", "V12", "V15"):
        assert rule in done.stderr, f"{rule} did not run and was not named:\n{done.stderr}"


def test_without_the_flag_an_unevaluated_rule_is_not_fatal(tmp_path: Path) -> None:
    """A laptop with no dataset checkout still gets a useful answer, as the ratchet does."""
    repo = _repo(tmp_path / "corpus")

    done = _run(repo, "HEAD", "--dataset-dir", str(_empty_dataset(tmp_path)))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "V12" in done.stdout, f"it must still be reported:\n{done.stdout}"


# ── the headline claim ────────────────────────────────────────────────────────


def test_a_rule_getting_stricter_produces_no_delta(tmp_path: Path) -> None:
    """Turn a rule on over an unchanged commit and the build stays green.

    V12 is the rule that can be switched on and off from outside the tool: it runs only when a
    held-out split is on disk. So this is a real stricter-rule event and not a mock — the same
    corpus, the same commit, one run where V12 evaluates nothing and one where it fires.

    A pin file cannot survive this. Its pins were written when V12 was not evaluated, so the
    corpus goes red on a commit that the corpus author did not make and cannot fix in their own
    repository. Git as the baseline applies the new rule to both sides, so it cancels.
    """
    repo = _repo(tmp_path / "corpus")
    _write(repo, "shop/tables/tbl_shop_orders.yaml", _TABLE_QUOTING_THE_SPLIT)
    _commit(repo, "a body that quotes what will later be held out")
    _write(repo, "shop/metrics/metric_distinct_orders.yaml", _SECOND_CLEAN_METRIC)

    lax = _empty_dataset(tmp_path)
    strict = tmp_path / "dataset"
    strict.mkdir()
    (strict / "test_final.jsonl").write_text(
        json.dumps({"question": _HELD_OUT_QUESTION}) + "\n", encoding="utf-8", newline="\n"
    )

    # The rule really did get stricter: zero V12 findings under `lax`, at least one under `strict`.
    before = _conformance(repo, "--trap-manifest", str(lax / "trap_manifest.json"),
                          "--test-split", str(lax / "test_final.jsonl"))
    after = _conformance(repo, "--trap-manifest", str(lax / "trap_manifest.json"),
                         "--test-split", str(strict / "test_final.jsonl"))
    assert "V12" in before["not_evaluated"], before["not_evaluated"]
    fired = [f for f in after["findings"] if f["rule"] == "V12"]
    assert fired, f"V12 did not fire, so this test would prove nothing: {after['not_evaluated']}"

    assert _run(repo, "HEAD", "--dataset-dir", str(lax)).returncode == 0
    strict_run = _run(repo, "HEAD", "--dataset-dir", str(strict))
    assert strict_run.returncode == 0, (
        "a rule that got stricter turned the build red on a commit that did not touch the asset "
        f"it fires on:\n{strict_run.stdout}\n{strict_run.stderr}"
    )


def test_the_two_sides_are_checked_by_the_same_command(tmp_path: Path) -> None:
    """The structural half of the claim above: one argv template, two corpus paths.

    The end-to-end test can only exercise rules that are switchable from the command line. This
    asserts the property for *any* rule change: base and head are handed to the same conformance
    invocation, differing in ``--corpus-dir`` alone, so no change to the rule set can land on one
    side and not the other.
    """
    import importlib.util

    # `conformance_argv` lives in `tools/conformance_findings.py`, shared with `check_ratchet.py`:
    # two tools that answer "what did this change do to the finding set" must not each carry their
    # own idea of how the finding set is obtained.
    spec = importlib.util.spec_from_file_location(
        "_findings", DELTA.parent / "conformance_findings.py"
    )
    findings = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered before exec: `@dataclass(slots=True)` rebuilds the class and looks its module up
    # in `sys.modules`, which is `None` for a spec loaded by path alone.
    sys.modules[spec.name] = findings
    spec.loader.exec_module(findings)

    one = findings.conformance_argv(Path("/a/base"), None)
    two = findings.conformance_argv(Path("/b/head"), None)
    assert len(one) == len(two)
    differ = [i for i, (x, y) in enumerate(zip(one, two)) if x != y]
    assert len(differ) == 1, f"the two sides are not the same command: {one} vs {two}"
    assert one[differ[0] - 1] == "--corpus-dir", one
