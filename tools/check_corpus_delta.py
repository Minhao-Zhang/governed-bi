#!/usr/bin/env python
"""Did the corpus add a conformance finding since somebody last looked? Git is the baseline.

    uv run --frozen python tools/check_corpus_delta.py --corpus-dir ../BIRD-corpus --base origin/main
    # CI, with the accepted baseline substituted in by the workflow step:
    uv run --frozen python tools/check_corpus_delta.py --every-rule-must-run
        --base "$(uv run --frozen python tools/corpus_baseline.py)"

    exit 0  no finding was added
    exit 1  a finding is present at head and not at base, or a pinned identity's count grew
    exit 2  could not run at all -- bad ref, not a git repository, conformance crashed or emitted
            no JSON, or --every-rule-must-run and a rule did not run

**This runs HERE, on a nightly, and never in the corpus repository.** ADR 0016 records why: a
conformance rule is a statement about what *this* engine requires of an asset -- V16 imports
``governed_bi.serve.context`` -- so the checker lives with the consumer, and therefore the consumer
runs it. A workflow in the corpus repository would be data asserting a fact about an engine it
cannot see, and executing that engine's default branch unpinned to do it. The baseline is a corpus
revision recorded on this side, in ``tools/corpus_baseline.py``; bumping it is a human saying "I
read the new findings and I accept them". The cost of the direction is honest and is in that ADR's
consequences: this is **not** a merge gate. A corpus commit that adds a finding lands, and is caught
up to a day later.

**This is ``check_ratchet.py``'s question with a different baseline, and the baseline is the whole
point.** The ratchet compares a corpus against ``.conformance/bird-corpus-pins.txt``, a file
committed in **this** repository. That file is the right instrument for a human declaring "this is
the debt we accept" and the wrong one for CI, for three measured reasons.

*A stricter rule reds a corpus that did not change.* Two rule changes landed on 2026-08-23 alone --
V21 went from running one guard rule to four, and V23 gained 45% of the tree. Either would have
turned a pin-based corpus build red with no corpus commit behind it, and the corpus author cannot
fix that in their own repository. Here the same rule set runs on both sides, so a rule change
cancels: it fires at base and at head, and the difference is empty.

*The pin file had to live in the corpus tree, and ``corpus_content_hash`` digests every file
there.* This is not a hazard, it happened: at the corpus root the pin file moved the treatment
identity from ``6e5c7b4be83d5682…`` to the now-superseded ``8bb37531cff9155a…``, so the gate changed
the thing it was gating. That is gone by construction rather than by an exclusion list: the pins
live in this repository now, and the corpus tree has no ``.conformance`` or ``.github`` in it at
all. ``corpus/identity.py::_NON_CORPUS_DIRS`` still excludes both, as defence against the next tool
that wants a corner of that tree, and the hash reads ``6e5c7b4be83d5682…`` with neither present --
the same value it read with both, which is what makes the exclusion measured rather than argued.

*Closing a finding fails a pin-based build* until someone rewrites the pin file in the same commit.
That ceremony exists only because there is a file to keep in sync. Here a fix is simply green --
``tests/conformance/test_a_commit_does_not_add_a_finding.py`` pins both halves side by side. This
reason is the one that only *shrank*: ``tools/corpus_baseline.py`` is still a line somebody keeps in
step, but it is one sha rather than 109 lines of findings, and editing it is the acknowledgement
rather than bookkeeping.

**The base tree comes from ``git worktree add --detach`` into a temp directory.** Never a checkout
in place, which would need a clean tree and would move the operator's HEAD, and never a directory
inside the corpus, which conformance would then walk. A worktree's ``.git`` is a *file* holding an
absolute path; ``corpus/identity.py::_is_tooling`` handles that already, and its docstring records
the measurement -- three checkouts of one commit, three content hashes -- that made it necessary.
The worktree is removed on every exit path, including the failing ones, because a leak registers a
directory in the corpus repository's ``.git/worktrees`` on every red build.

**When ``--base`` cannot be resolved this exits 2, and the condition is narrower than it looks.**
With ``fetch-depth: 0`` on the checkout, the parent of any non-root commit is present -- so the
cases that actually fire are a **root commit**, which has no parent, and a **base ref absent from
the clone**: a sha on an unfetched fork, or a baseline whose commit was rewritten away. "The first
push of a branch" was the reason given for this in an earlier draft of the CI, and it was wrong.
Either way nothing was compared, so nothing was checked, and the message names the ref
(``test_a_bad_base_ref_exits_two_and_says_which``).

**A finding's identity is ``(rule, where)`` and each identity carries a count**, which is the
ratchet's notion and deliberately not a new one: the identity alone cannot see growth. 125 findings
on ``../BIRD-corpus`` at ``main`` = ``74ff80c4`` live on 101 identities (measured 2026-08-24), so an
asset already carrying a V17a finding could take on any number more without the set growing by one
line. **Different nouns:** "carries 101 findings" is wrong, and so is "125 identities".

The comparison below is duplicated from ``check_ratchet.py`` rather than imported -- **two copies
of one rule, marked here so the next reader knows**. Extracting it is the right call and is left
for a commit that owns both files.

**``--every-rule-must-run`` is fatal, and it exits 2 rather than 1.** The conformance JSON reports
rules it could not evaluate: V11, V12 and V15 need the obfuscation dataset's manifests. On a laptop
that is normal and ``check_ratchet.py`` prints a note. In CI it is the failure this tool exists to
prevent: if the dataset checkout silently fails, the leakage rule runs on neither side, the delta is
empty and the build is green. A rule that could not run is indistinguishable from a rule that
passed. It is not "you made it worse", so it is not exit 1.

**Identities key on a file's basename, inherited from ``_where``.** Two assets with the same
basename in different directories share an identity, and moving a file between directories is
invisible here. Both are properties of the conformance tool's output and are not re-derived here;
a second notion of identity would disagree with the ratchet's.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Sibling script, path-added like `check_corpus_conformance.py` imports its rules.
from conformance_findings import (  # noqa: E402
    CannotRun,
    compare,
    read,
)

ROOT = Path(__file__).resolve().parent.parent
CONFORMANCE = ROOT / "tools" / "check_corpus_conformance.py"
DEFAULT_CORPUS = ROOT.parent / "BIRD-corpus"

#: The manifests ``--dataset-dir`` overrides, as ``conformance flag -> filename``. Named here so
#: both sides are handed the identical set; a dataset visible to one run and not the other would
#: manufacture a delta out of the environment.
DATASET_FILES: dict[str, str] = {
    "--trap-manifest": "trap_manifest.json",
    "--table-manifest": "trap_table_manifest.json",
    "--rename-map": "schema_rename_map.json",
    "--test-split": "test_final.jsonl",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--base",
        required=True,
        help="a git ref resolvable in the corpus repository, e.g. origin/main or HEAD",
    )
    parser.add_argument(
        "--every-rule-must-run",
        action="store_true",
        help="exit 2 if any rule could not be evaluated. Use this in CI: a rule that did not run "
             "reads as a rule that passed",
    )
    # Additive and optional, beyond the contract. Without it the conformance defaults resolve
    # against this repository's parent, so a CI job whose dataset lives elsewhere cannot run the
    # rules it most needs -- and neither can a test, which is how `--every-rule-must-run` is
    # pinned at all.
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="where the obfuscation dataset's manifests live. Defaults to the conformance tool's "
             "own default, ../BIRD-Data-Obfuscation/eval_dataset",
    )
    args = parser.parse_args(argv)

    try:
        return _compare(args)
    except CannotRun as err:
        print(str(err), file=sys.stderr)
        return 2


def _compare(args: argparse.Namespace) -> int:
    corpus = args.corpus_dir.resolve()
    if not corpus.is_dir():
        raise CannotRun(f"no corpus at {corpus}")

    top = _toplevel(corpus)
    commit = _resolve_ref(top, args.base)
    relative = corpus.relative_to(top)

    head = read(corpus, args.dataset_dir)
    with _base_tree(top, commit) as tree:
        base = read(tree / relative, args.dataset_dir)

    skipped = {**base.not_evaluated, **head.not_evaluated}
    print(f"corpus {corpus}")
    print(f"  base {args.base} ({commit[:12]}), head is the working tree")
    print(
        f"  base {sum(base.counts.values())} finding(s) on {len(base.counts)} identit(ies); "
        f"head {sum(head.counts.values())} on {len(head.counts)}"
    )
    if skipped:
        for rule, why in sorted(skipped.items()):
            print(f"  {rule} not evaluated: {why}")

    if skipped and args.every_rule_must_run:
        raise CannotRun(
            f"\n{len(skipped)} rule(s) did not run on one or both sides: "
            + ", ".join(sorted(skipped))
            + "\n"
            + "\n".join(f"  {rule}: {why}" for rule, why in sorted(skipped.items()))
            + "\n\nA rule that could not run is indistinguishable from a rule that passed: it "
            "reports zero findings at base and zero at head, the delta is empty and the build "
            "goes green having checked nothing. Fix the checkout, or drop "
            "--every-rule-must-run and accept that these rules were not gated."
        )

    # The arithmetic is `conformance_findings.compare`; the **policy** is here. This gate acts
    # on two of the four: a closure is the outcome it wants to be cheap, and git needs no
    # updating for one. The ratchet acts on all four because its baseline is a file somebody
    # has to keep in step. That is a real disagreement between two tools and it belongs in
    # each of them, not in the shared arithmetic.
    change = compare(base.counts, head.counts)
    added, grew = change.added, change.grew

    if added:
        print(
            f"\n{len(added)} finding(s) present at head and absent at base:", file=sys.stderr
        )
        for rule, where in added:
            for message in head.messages[(rule, where)]:
                print(f"  [{rule}] {where}  {message}", file=sys.stderr)

    if grew:
        print(f"\n{len(grew)} identit(ies) took on further findings of the same rule:",
              file=sys.stderr)
        for rule, where in grew:
            print(
                f"  [{rule}] {where}  {base.counts[(rule, where)]} -> {head.counts[(rule, where)]}",
                file=sys.stderr,
            )
            for message in head.messages[(rule, where)]:
                print(f"      {message}", file=sys.stderr)

    if added or grew:
        print(
            "\nThis commit added the findings above. It is a DELTA gate: it is not claiming the "
            "corpus is clean, and a rule that got stricter cannot land here -- it fires at base "
            "too. Fix the asset, or make the case for the rule.",
            file=sys.stderr,
        )
        return 1

    if change.closed or change.shrank:
        print(
            f"  {len(change.closed)} identit(ies) closed, {len(change.shrank)} shrank. "
            "Nothing to declare."
        )
    print("  no finding was added")
    return 0


# ── running conformance ───────────────────────────────────────────────────────


@contextmanager
def _base_tree(top: Path, commit: str) -> Iterator[Path]:
    """The corpus as of ``commit``, in a throwaway worktree, removed on every exit path.

    ``git worktree add --detach`` into a temp directory, and the two rejected alternatives are why:
    a checkout in place needs a clean tree and moves the operator's HEAD, and a worktree *inside*
    the corpus is a second copy of the tree that conformance would then walk.

    Removed in a ``finally`` because the failing paths are the ones that matter -- a leak registers
    a directory in the corpus repository's ``.git/worktrees`` on every red build, and the corpus
    repository is not this tool's to litter. ``remove --force`` because the worktree is detached and
    git treats that as dirty; ``prune`` after the directory is gone because ``remove`` can fail on
    Windows while the tree is still open, and a stale registration is what the next run trips over.
    """
    tree = Path(tempfile.mkdtemp(prefix="corpus-base-"))
    added = _git(top, "worktree", "add", "--detach", str(tree), commit)
    if added.returncode != 0:
        shutil.rmtree(tree, ignore_errors=True)
        raise CannotRun(
            f"could not check out {commit[:12]} into a worktree:\n{added.stderr[:2000]}"
        )
    try:
        yield tree
    finally:
        _git(top, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(tree, ignore_errors=True)
        _git(top, "worktree", "prune")


def _toplevel(corpus: Path) -> Path:
    done = _git(corpus, "rev-parse", "--show-toplevel")
    if done.returncode != 0:
        raise CannotRun(
            f"{corpus} is not inside a git repository, and git is this tool's baseline. "
            f"git said: {done.stderr.strip()}"
        )
    return Path(done.stdout.strip()).resolve()


def _resolve_ref(top: Path, ref: str) -> str:
    """``ref`` as a commit sha, or exit 2 naming the ref.

    ``^{commit}`` rather than a bare ``--verify``: a tag or a tree resolves to an object that
    ``worktree add`` cannot check out, and the failure would then arrive from git two steps later
    with no mention of what the operator typed.
    """
    done = _git(top, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if done.returncode != 0 or not done.stdout.strip():
        raise CannotRun(
            f"--base {ref!r} does not resolve to a commit in {top}. "
            "In CI this is usually a shallow clone: fetch the base ref before running this."
        )
    return done.stdout.strip()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
