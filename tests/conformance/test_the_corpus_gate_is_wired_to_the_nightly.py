"""The corpus conformance gate moved into this repository's CI. This is what can be checked here.

The gate itself is pinned elsewhere: ``test_a_commit_does_not_add_a_finding.py`` drives
``tools/check_corpus_delta.py`` on throwaway git repositories and proves what it does to a finding.
This file covers the **wiring** — the baseline it compares against and the workflow that runs it —
because that half has no runner. `.github/workflows/ci.yml`'s `corpus` job has never executed, and
neither did the job in `BIRD-corpus` it replaces (0 GitHub Actions runs in that repository,
measured 2026-08-24). So every property of the step is argued from the YAML, and the YAML is the
only artifact a test here can read.

Which makes the failure this file is about a specific one: **a workflow that does not do what its
comment says, with nothing to notice.** The previous arrangement had two instances of exactly that
shape at once — a `ref:` pin described as temporary that no gate could see, and a
"`check_train_only.py` is deliberately not here" comment that a substring search counted as a CI
step. Neither was a wrong claim about behaviour; both were claims nothing checked.

``yaml.safe_load`` and the ``on:`` key: YAML 1.1 reads a bare ``on`` as the boolean *true*, so the
trigger block comes back under the key ``True`` and ``document["on"]`` raises ``KeyError``. Both
spellings are read below, because which one a given PyYAML resolves is not this file's claim to
make.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
BASELINE = ROOT / "tools" / "corpus_baseline.py"
PINS = ROOT / ".conformance" / "bird-corpus-pins.txt"

#: The job under test, and the tool the job exists to run.
JOB = "corpus"
DELTA_TOOL = "tools/check_corpus_delta.py"


def _load(path: Path, name: str) -> Any:
    """A ``tools/`` script as a module. ``tools/`` is not a package, so this is by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _triggers(document: dict) -> dict:
    """The ``on:`` block, under whichever key this PyYAML put it. See the module docstring."""
    block = document.get("on", document.get(True))
    assert isinstance(block, dict), f"the on: block parsed as {type(block).__name__}"
    return block


def _job() -> dict:
    jobs = _workflow()["jobs"]
    assert JOB in jobs, f"no `{JOB}` job in ci.yml; jobs are {sorted(jobs)}"
    return jobs[JOB]


def _run_scripts(job: dict) -> str:
    return "\n".join(str(s["run"]) for s in job.get("steps", []) if "run" in s)


def _checkouts(job: dict) -> list[dict]:
    """Every ``actions/checkout`` step, in file order, as its ``with:`` mapping."""
    return [
        dict(step.get("with") or {})
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout")
    ]


# ── the recorded baseline ─────────────────────────────────────────────────────


def test_the_baseline_is_a_full_length_commit_sha() -> None:
    """40 hex digits, not an abbreviation.

    ``git rev-parse --verify`` accepts a short sha only while it stays unique in the repository it
    is resolved in, so an abbreviated baseline is a reference that can start failing as the corpus
    grows, with nobody having touched the line. The tool maps that to exit 2 — "could not run" —
    which is the honest code and is still a gate that checked nothing.
    """
    sha = _load(BASELINE, "_corpus_baseline").BASELINE_SHA
    assert len(sha) == 40, f"{sha!r} is {len(sha)} characters, not a full sha"
    assert all(c in "0123456789abcdef" for c in sha), sha


def test_the_baseline_prints_the_sha_and_nothing_else() -> None:
    """The workflow does ``base="$(uv run --frozen python tools/corpus_baseline.py)"``.

    So anything else this prints — a heading, the finding counts, a deprecation warning — becomes
    part of the git ref handed to ``--base``. The tool would then exit 2 rather than pass silently,
    but the job would be reporting a broken *print* as a corpus verdict.
    """
    done = subprocess.run(
        [sys.executable, str(BASELINE)], capture_output=True, text=True, cwd=str(ROOT)
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == _load(BASELINE, "_corpus_baseline").BASELINE_SHA
    assert done.stdout.count("\n") == 1, repr(done.stdout)


def test_the_baseline_names_the_day_somebody_looked() -> None:
    """A sha with no date says "accepted" without saying by anyone at any time.

    Bumping :data:`~tools.corpus_baseline.BASELINE_SHA` is the one human act this design keeps from
    the rejected pin-file baseline. An act with no date is indistinguishable from a revision
    somebody pointed at years later, which is the state the number is supposed to rule out.
    """
    acknowledged = _load(BASELINE, "_corpus_baseline").ACKNOWLEDGED
    parts = acknowledged.split("-")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), acknowledged
    assert len(parts[0]) == 4, acknowledged


def test_the_pin_file_lives_in_this_repository_and_not_beside_a_corpus() -> None:
    """The second of the three reasons the pin-file baseline was rejected, closed by construction.

    Beside the corpus the pin file entered ``corpus_content_hash`` and moved the treatment identity
    every measured number is pinned to (``6e5c7b4be83d5682…`` to ``8bb37531cff9155a…``) — the gate
    changed the thing it was gating. ``.conformance/`` in ``_NON_CORPUS_DIRS`` fixed the symptom by
    an exclusion list; a pin file in *this* tree cannot be in a digest of *that* tree at all.

    Asserted as "under this repository and not under the default corpus", not as a string equal to
    the constant: a test comparing a module against its own literal passes for any value.
    """
    ratchet = _load(ROOT / "tools" / "check_ratchet.py", "_check_ratchet")
    pins = Path(ratchet.DEFAULT_PINS).resolve()
    assert pins.is_relative_to(ROOT), f"{pins} is outside this repository"
    assert not pins.is_relative_to(Path(ratchet.DEFAULT_CORPUS).resolve()), pins
    assert pins.is_file(), f"{pins} is the ratchet's default and does not exist"


def test_the_pin_file_and_the_baseline_agree_on_what_was_accepted() -> None:
    """Two records of one fact, so they are compared rather than trusted.

    ``tools/corpus_baseline.py`` records 125 findings on 101 identities as integers;
    ``.conformance/bird-corpus-pins.txt`` records the same 101 identities by name with a count
    each. Nothing forces them to agree — they are edited in separate commits by separate acts —
    and a baseline claiming counts the pin file contradicts is a baseline nobody reconciled.

    Read with the ratchet's own parser, not a second one: a private copy of the pin-file format
    here would be a second answer to what a line means.
    """
    baseline = _load(BASELINE, "_corpus_baseline")
    ratchet = _load(ROOT / "tools" / "check_ratchet.py", "_check_ratchet")
    pinned = ratchet._pins(PINS)
    assert len(pinned) == baseline.IDENTITIES, (
        f"{len(pinned)} pins, baseline says {baseline.IDENTITIES} identities"
    )
    assert sum(count or 0 for count in pinned.values()) == baseline.FINDINGS, (
        f"pins cover {sum(c or 0 for c in pinned.values())} findings, baseline says "
        f"{baseline.FINDINGS}"
    )
    assert baseline.FINDINGS > baseline.IDENTITIES, (
        "findings and identities are different nouns and on this corpus the difference is 24. "
        "Equal counts would mean the count-per-identity carried no information."
    )


# ── the workflow that runs it ─────────────────────────────────────────────────


def test_the_workflow_parses() -> None:
    """A malformed workflow is not a red build, it is a workflow GitHub silently never runs.

    Every other test in this section would pass vacuously on a file that cannot be loaded, because
    they would error rather than assert — so this one exists to be the failure that names the cause.
    """
    document = _workflow()
    assert isinstance(document.get("jobs"), dict), document.keys()


def test_the_nightly_job_runs_the_delta_tool() -> None:
    """The job's whole reason for existing, asserted on the step and not on the comment."""
    assert DELTA_TOOL in _run_scripts(_job())


def test_the_nightly_job_demands_that_every_rule_ran() -> None:
    """``--every-rule-must-run``, and its absence is a **green** build that checked nothing.

    V11, V12 and V15 need the obfuscation manifests. If that checkout is missing or misnamed,
    conformance reports the three as ``not_evaluated`` on *both* sides — zero findings at base,
    zero at head, an empty delta, exit 0. V12 is the held-out-split leakage rule. Without the flag
    the failure is invisible; with it the same condition is exit 2.
    """
    assert "--every-rule-must-run" in _run_scripts(_job())


def test_the_nightly_job_is_not_on_a_push_or_a_pull_request() -> None:
    """Schedule and manual dispatch only, and the reason is not cost.

    A finding belongs to a *corpus* commit. Reddening an unrelated engine PR because the corpus
    grew one is how a red X becomes noise — the same argument that rejected a zero-findings gate
    with a waiver table. The corpus also has 9 commits in its whole history against this
    repository's 485 over the same window, so per-push would be roughly 54 runs per thing worth
    checking.

    Both halves are asserted: the trigger has to *exist* on the workflow (a ``workflow_dispatch``
    named only in an ``if`` gives a job nobody can start), and the ``if`` has to exclude the two
    push-shaped events the workflow does declare.
    """
    triggers = _triggers(_workflow())
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers

    guard = _job()["if"]
    assert "schedule" in guard and "workflow_dispatch" in guard, guard
    assert "push" not in guard and "pull_request" not in guard, guard


def test_the_nightly_job_passes_both_paths_explicitly() -> None:
    """Neither the corpus nor the dataset may be left to default resolution.

    Both tools resolve their defaults from ``Path(__file__).parent.parent.parent``. On a runner
    that is the directory *above* the workspace, which holds neither checkout — so a missing flag
    is exit 2. Lay the three checkouts out as real siblings instead and the defaults resolve, at
    which point a wrong or absent flag is indistinguishable from a working gate. That is why the
    data repositories are nested under the workspace and both paths are spelled out.
    """
    script = _run_scripts(_job())
    assert "--corpus-dir" in script
    assert "--dataset-dir" in script


def test_the_corpus_checkout_carries_its_history() -> None:
    """``fetch-depth: 0`` on the corpus, and it is load-bearing rather than defensive.

    ``check_corpus_delta.py`` reads the base tree with ``git worktree add --detach <sha>`` inside
    that checkout. A shallow clone does not have the commit, so the tool exits 2 naming the ref —
    honest, and not a gate. Asserted as ``== 0`` and not "truthy": ``fetch-depth: 1`` is also a
    number and is the default this exists to override.
    """
    corpus = [
        with_ for with_ in _checkouts(_job())
        if "BIRD-corpus" in str(with_.get("repository", ""))
    ]
    assert len(corpus) == 1, f"expected one BIRD-corpus checkout, found {len(corpus)}"
    assert corpus[0].get("fetch-depth") == 0, corpus[0]


def test_the_dataset_directory_is_named_what_the_tools_look_for() -> None:
    """The repository is ``BIRD-Obfuscation``; the directory has to read ``BIRD-Data-Obfuscation``.

    Not cosmetic. ``BIRD-Data-Obfuscation`` is the name in both tools' defaults, in the docs and in
    every local checkout, so a path spelled from the *repository* name would send ``--dataset-dir``
    somewhere with no manifests. That failure is silent on its own — three rules go
    ``not_evaluated`` on both sides — and is only fatal because of ``--every-rule-must-run``, which
    is asserted separately above. This test is the other half: the path is right in the first place.
    """
    dataset = [
        with_ for with_ in _checkouts(_job())
        if "Obfuscation" in str(with_.get("repository", ""))
    ]
    assert len(dataset) == 1, f"expected one obfuscation checkout, found {len(dataset)}"
    assert dataset[0].get("path") == "BIRD-Data-Obfuscation", dataset[0]
    assert "BIRD-Data-Obfuscation/eval_dataset" in _run_scripts(_job())


def test_the_engine_checkout_comes_before_the_data_checkouts() -> None:
    """Order is a correctness property here, not a style one.

    ``actions/checkout`` cleans its destination with ``git clean -ffdx``. At the workspace root
    that removes untracked directories — which is exactly what the two nested data checkouts are.
    Run the engine checkout last and it deletes both, and the step after it fails with "no corpus
    at ...": exit 2, so not a false green, but a failure whose cause is 40 lines away from its
    message.

    The engine checkout is the one with no ``repository:`` — the default, this repository.
    """
    checkouts = _checkouts(_job())
    assert checkouts, "the corpus job has no actions/checkout steps"
    assert "repository" not in checkouts[0], (
        f"the first checkout names a repository ({checkouts[0].get('repository')}); the default "
        "checkout of this repository must come first, before anything it would clean away"
    )
    assert len(checkouts) == 3, f"expected three checkouts, found {len(checkouts)}"


@pytest.mark.parametrize("job", ["test", "ui"])
def test_the_corpus_job_did_not_disturb_the_jobs_beside_it(job: str) -> None:
    """The jobs that run on every push must still have no ``if``.

    Adding ``workflow_dispatch`` to the ``on:`` block widens what every job in the file responds
    to. That is intended for these two and opted out of by ``corpus`` and ``mutate``. What would
    not be intended is an ``if`` arriving on one of these while nobody was looking, which is how a
    push-time suite quietly stops being one.
    """
    assert "if" not in _workflow()["jobs"][job]
