#!/usr/bin/env python
"""Turn a drafted patch into a directory an engineer applies with ``git apply`` (ADR 0015 §4).

    uv run --frozen python tools/export_bundle.py --patch pat-... --dry-run
    uv run --frozen python tools/export_bundle.py --patch pat-... --out ./bundles

**There is no ``--apply``, and there will not be one.** The write to corpus content is a human's
``git commit`` in a repository this process cannot reach, and that is the provenance gate the whole
design is. What this produces is a change that is *mechanical* to apply and carries its own evidence.

**It is a diff and never a directory copy**, which is a correctness requirement rather than a
preference. Copying a staged file into the corpus is how you get two files declaring one asset id:
the served corpus keeps a table's columns inline and ``store.load`` splits them at load, so a
standalone column file duplicates the id its table already derives — accepted by the loader with
zero problems, then fatal in ``build_index``, *after* the commit.

**Two conformance rules are fatal here and nowhere else, and they are called rather than copied.**

* **V19 — an excluded column or asset named in model-visible text**, which is
  ``conformance_rules_metric_and_content.check_excluded_not_named``. ADR 0003 found a corpus asset
  naming a ``governance.excluded`` column in text that was then injected verbatim into the SQL
  prompt, and concluded a content-scanning validator was the structural answer. None shipped. This
  is it, at the one gate a change has to pass. Measured 2026-08-23: **zero** assets are excluded in
  either corpus, so today the check has no population and cannot refuse a legitimate bundle.
* **V12 — a held-out question quoted in an asset**, which is
  ``check_corpus_conformance.check_split_leak``. The importer's rows carry question text from the
  held-out split, and the loop's whole purpose is that a person reads it and writes corpus prose.
  A question travelling from the graded split into a ``body`` contaminates every EX number measured
  afterwards, invisibly. Running V12 here is the obligation ``eval/feedback_import.py`` records as
  owed.

**Both used to be re-implemented in this file**, as a regex over
``for_analyst(...).excluded_columns`` and a "five shared words" phrase matcher. Measured 2026-08-24:
on six inputs the copies and the rules **disagreed on four**, in both directions. The copy searched
for an excluded column called ``SSN`` as ``ssn`` with a case-sensitive pattern — ``for_analyst``
keys columns through ``slug(physical_name).lower()`` — and exported a body the rule refuses. It
compared new text only against the questions of this patch's own observations, so a body quoting
some other graded question passed. And it refused on a five-word run and on a name in a ``summary``,
neither of which V12 or V19 calls a violation anywhere else in the tree. Two implementations of one
gate is two answers, and the one a reviewer trusts is whichever fired.

Neither is a claim of safety. ``tools/check_train_only.py``'s own docstring says paraphrase leaks
are undetectable, so the last control is a person who knows what they are reading — which is why the
review surface labels the question as held-out rather than presenting it as neutral context.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

# `tools/` is a directory of scripts and not a package, so the two conformance modules are reached
# the way `verify_patch.py` reaches them: one path insert, then a plain import. Copied there from
# `tests/conformance/`, and it is the only convention in the tree for a tool calling a rule.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_corpus_conformance as cc  # noqa: E402 - after the path insert, by design

# The one asset-shaping helper this needs, imported rather than restated: it turns a parsed
# document into the `(kind, mapping, path)` triples the rules take, unpacking a table's inline
# columns exactly as `cc.load_assets` does. A second copy here would be a second loader, and the
# rules would then be answering about an asset neither tool builds.
from verify_patch import _assets_of  # noqa: E402

from governed_bi.corpus.hash import corpus_content_hash  # noqa: E402
from governed_bi.corpus.patch import (  # noqa: E402
    StaleValue,
    UnwritableValue,
    apply_edit,
    locate,
)
from governed_bi.feedback.events import Patch, PatchIntent, PatchState, Source  # noqa: E402
from governed_bi.feedback.store import FeedbackStore  # noqa: E402
from governed_bi.paths import REPO_ROOT  # noqa: E402

DEFAULT_DB = "runs/feedback.sqlite"
DEFAULT_OUT = "bundles"


@dataclass(frozen=True, slots=True)
class Refusal:
    """One fatal finding. ``rule`` names the conformance rule it stands in for."""

    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", required=True, help="patch id, as `pat-...`")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--corpus-dir", default=None, help="defaults to GOVERNED_BI_CORPUS_DIR")
    parser.add_argument(
        "--test-split",
        # The same default as `check_corpus_conformance`, read from it rather than restated: V12
        # asking a different question here than in the corpus report is the defect this file had.
        default=str(cc.DEFAULT_DATASET / "test_final.jsonl"),
        help="the held-out split V12 forbids quoting. Absent, V12 falls back to the questions this "
        "patch's own eval observations carry, and says so",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the diff and the checks; write nothing",
    )
    parser.add_argument(
        "--despite-a-red-ladder",
        action="store_true",
        help="export even though T0, T1 or T2 says this edit breaks something. Prints what it is "
        "overriding",
    )
    args = parser.parse_args(argv)

    store = FeedbackStore(_resolve(args.db))
    patch = store.get_patch(args.patch)
    if patch is None:
        print(f"no patch {args.patch!r} in {args.db}", file=sys.stderr)
        return 2
    if patch.intent is not PatchIntent.edit_asset:
        print(
            f"patch {args.patch} has intent {patch.intent.value}, and only `edit_asset` produces a "
            "bundle. An exclusion_request is prose a human transcribes by hand; engine_defect and "
            "no_change author nothing on purpose.",
            file=sys.stderr,
        )
        return 2

    corpus_root = _corpus_root(args.corpus_dir)
    target = _file_declaring(corpus_root, patch)
    if target is None:
        print(
            f"no file under {corpus_root} declares asset {patch.asset_id!r}", file=sys.stderr
        )
        return 2

    before = target.read_text(encoding="utf-8")
    try:
        after = apply_edit(
            target,
            asset_id=str(patch.asset_id),
            field_path=str(patch.field_path),
            was=str(patch.was),
            becomes=str(patch.becomes),
        )
    except UnwritableValue as err:
        # The value cannot be written into this field and read back unchanged -- a tab, a control
        # character, a trailing colon, or an interior newline in a plain scalar. Refused here rather
        # than shipped: before `apply_edit` checked its own output this exited **0** and wrote a
        # bundle that stops the corpus loading after the commit.
        print(f"REFUSED, and no bundle was written:\n  {err}", file=sys.stderr)
        return 1
    except StaleValue as err:
        # `verify_patch` wraps this and this did not, so a stale `was` was a traceback carrying a
        # message about the corpus having moved.
        print(f"REFUSED, and no bundle was written:\n  {err}", file=sys.stderr)
        return 1
    diff = _unified(target.relative_to(corpus_root), before, after)

    refusals, notes = _refuse(
        patch,
        corpus_root=corpus_root,
        store=store,
        target=target,
        after=after,
        test_split=_resolve(args.test_split),
    )
    print(diff or "(the edit produced no textual change)")
    print(f"\n{_summarise(diff)}")
    for note in notes:
        print(f"\nNOTE: {note}")

    if refusals:
        print("\nREFUSED, and no bundle was written:")
        for refusal in refusals:
            print(f"  {refusal}")
        print(
            "\nThese are the conformance rules themselves, so the corpus report will say the same "
            "thing. They are fatal here and reported there by decision rather than by convention: "
            "V19 is ADR 0003's unfixed finding, and V12 is the last mechanical control on corpus "
            "text a person wrote while reading a held-out question."
        )
        return 1

    ladder_refusals, ladder_warnings = _ladder_verdict(patch)
    for warning in ladder_warnings:
        print(f"\nNOTE: {warning}")
    if ladder_refusals and not args.despite_a_red_ladder:
        print("\nREFUSED, and no bundle was written:", file=sys.stderr)
        for refusal in ladder_refusals:
            print(f"  {refusal}", file=sys.stderr)
        print(
            "\nThese tiers say the edit breaks something, so handing it over is handing over a "
            "regression. Fix it and re-run tools/verify_patch.py, or pass "
            "--despite-a-red-ladder if you mean it.",
            file=sys.stderr,
        )
        return 1
    if ladder_refusals:
        print("\nOVERRIDDEN with --despite-a-red-ladder:")
        for refusal in ladder_refusals:
            print(f"  {refusal}")

    if args.dry_run:
        print("\n(dry run: nothing was written. Drop --dry-run to write the bundle.)")
        return 0

    bundle = _write_bundle(
        Path(_resolve(args.out)) / f"bnd-{patch.patch_id}",
        patch=patch,
        store=store,
        corpus_root=corpus_root,
        relative=target.relative_to(corpus_root),
        after=after,
        diff=diff,
    )
    print(f"\nwrote {bundle}")

    if patch.state is PatchState.draft:
        # The digest the corpus will carry once this bundle is applied and nothing else is.
        # `DerivedState.landed_verified` is the state that reads it, and until now **nothing set the
        # field**: this call site omitted it as "the digest of a tree nobody has written yet" and
        # named `tools/check_landed.py` as where it would be computed, and that file has never
        # contained the symbol. So the branch never fired and every real landing reported
        # `landed_matched` at best -- which is the weaker claim, true of a corpus where three other
        # bundles also landed.
        #
        # It is computable here: the digest is a walk of relative path plus content, so substituting
        # the one edited file's bytes gives the post-state exactly. UTF-8 bytes and not the string,
        # because `git apply` writes LF and a text-mode write on Windows would not.
        expected = corpus_content_hash(
            corpus_root, overrides={target: after.encode("utf-8")}
        )
        store.move_patch(
            patch.patch_id,
            to=PatchState.exported,
            detail=f"bundle at {bundle.name}",
            expected_corpus_content_hash=expected,
        )
        print("patch state: draft -> exported")
        print(f"expected corpus hash after this lands alone: {expected[:16]}…")
    print(_apply_instructions(bundle, corpus_root))
    return 0


# ── the two fatal checks ──────────────────────────────────────────────────────


def _ladder_verdict(patch: Patch) -> tuple[list[str], list[str]]:
    """``(refusals, warnings)`` from the ladder rows the patch carries.

    Split because the tiers answer different questions. **T0-T2 red is a refusal**: they say the
    edit breaks something, and a bundle for that is a regression somebody will apply. **T3 red is a
    warning**: it says this patch does not fix the complaint it is attached to, which sends it back
    to the steward without meaning the edit is wrong -- refusing would refuse every patch that
    improves an asset without closing one specific coverage miss.

    **An unrun ladder warns and does not refuse.** The free tiers cost nothing, so there is no
    argument for handing over a change nobody ran them on; but there is no finding to refuse on
    either, and manufacturing one is the "unrun reads as failed" defect the derived states exist to
    avoid.
    """
    ladder = dict(patch.ladder or {})
    refusals: list[str] = []
    warnings: list[str] = []

    unrun = [tier for tier in ("T0", "T1", "T2") if tier not in ladder]
    if unrun:
        warnings.append(
            f"{', '.join(unrun)} has not been run on this patch, and they cost nothing. "
            "tools/verify_patch.py --patch " + patch.patch_id
        )
    for tier in ("T0", "T1", "T2"):
        row = ladder.get(tier)
        if isinstance(row, dict) and row.get("passed") is False:
            refusals.append(f"{tier}: {row.get('detail') or 'failed'}")

    t3 = ladder.get("T3")
    if isinstance(t3, dict) and t3.get("passed") is False:
        warnings.append(
            f"T3 says this patch does not fix the complaint it answers -- {t3.get('detail') or ''} "
            "That is a reason to take it back to the steward and not a reason it cannot land: the "
            "edit may still be a correct improvement to the asset."
        )
    elif t3 is None:
        warnings.append(
            "T3 has not been run, so nothing says whether this patch fixes the complaint. "
            "tools/reproduce_observation.py --patch " + patch.patch_id + " --embed"
        )
    return refusals, warnings


def _refuse(
    patch: Patch,
    *,
    corpus_root: Path,
    store: FeedbackStore,
    target: Path,
    after: str,
    test_split: Path,
) -> tuple[list[Refusal], list[str]]:
    """``(refusals, notes)`` from the two conformance rules, run on the text this patch introduces.

    **The rules are called, not restated.** V19 is
    ``conformance_rules_metric_and_content.check_excluded_not_named`` and V12 is
    ``check_corpus_conformance.check_split_leak``. What a rule owns is what the rule *is*: which
    names count as excluded, what text the model sees, and what counts as quoting a question. This
    file carried its own answer to all three, and all three differed -- see the module docstring for
    the four measured disagreements.

    **Two properties here are the caller's and stay.** A finding is **fatal**: this is the last
    mechanical control before a person commits corpus text they wrote while reading a held-out
    question, and a finding somebody has to go looking for is not a gate. And the population is
    what **this edit introduces**, diffed against the same rules on the tree as it stands.
    ``../BIRD-corpus`` carries 125 findings on 101 pinned identities, so an absolute gate here
    would refuse production, get waived, and a waiver is how a real finding goes green.
    """
    out: list[Refusal] = []
    notes: list[str] = []
    # `after` is the post-edit file, and it parses: `apply_edit` re-reads its own output and raises
    # `UnwritableValue` otherwise, which `main` has already turned into a refusal by here.
    edited = _assets_of(yaml.safe_load(after), target)

    # V19 needs the whole tree. The rule derives "excluded" from the asset list it is handed, so a
    # narrower list is a smaller answer: the column this text names may be declared in any file.
    tree = cc.walk(corpus_root)
    patched = [(k, a, p) for k, a, p in tree if p != target] + edited
    for finding in _introduced(
        cc.check_excluded_not_named(tree), cc.check_excluded_not_named(patched)
    ):
        out.append(
            Refusal(
                "V19",
                f"{finding} ADR 0003 found exactly this and concluded a content-scanning validator "
                "was the structural answer; this is that gate, and here it refuses.",
            )
        )

    # V12 gets the edited file alone, which is a complete population for it: the rule reads one
    # asset's own `summary` and `body` against a file on disk and looks at no second asset, which
    # is why `check_corpus_conformance` runs it in `--file` mode too. Scoped deliberately -- the
    # cost is assets times questions, and the split carries 1,351 of the latter.
    with tempfile.TemporaryDirectory() as scratch:
        split, supplied = _held_out(Path(scratch), store=store, patch=patch, test_split=test_split)
        if not supplied:
            notes.append(
                "V12 not evaluated: this patch carries no eval-sourced observation, and there is "
                f"no held-out split at {test_split}. So nothing here says whether the new text "
                "quotes a graded question -- pass --test-split to ask. Reported rather than "
                "refused: a corpus that is not a benchmark has no split, and refusing would refuse "
                "every export on one."
            )
        else:
            for finding in _introduced(
                cc.check_split_leak(cc.load_assets(target), split),
                cc.check_split_leak(edited, split),
            ):
                out.append(
                    Refusal(
                        "V12",
                        f"{finding} ({supplied} held-out question(s) were checked.) A question from "
                        "the graded split in an asset contaminates every EX number measured "
                        "afterwards, and the contamination is invisible.",
                    )
                )
    return out, notes


def _introduced(before: list, after: list) -> list[str]:
    """Findings the edit adds, by message.

    By message rather than by ``(rule, asset)`` as ``check_ratchet.py`` keys: the ratchet pins a
    corpus's standing debt, where a reworded message must not read as new, and this asks what one
    edit changed -- where the message *is* the change. Same argument as ``verify_patch._delta``,
    which cannot be reused directly because it runs every rule and these two run alone.
    """
    return sorted({str(f) for f in after} - {str(f) for f in before})


def _held_out(scratch: Path, *, store: FeedbackStore, patch: Patch, test_split: Path) -> tuple[Path, int]:
    """The held-out questions this bundle may not quote, as the JSONL ``check_split_leak`` reads.

    **Two sources, unioned.** The questions of this patch's own eval observations are always
    available in process, and they are the text a steward demonstrably read. The split file is every
    graded question there is, and it is what the corpus-wide V12 reads -- so a bundle quoting a
    question that is not attached to it is caught here as well. The inline copy this replaced had
    only the first source, and that is one of the two measured disagreements.

    Written to a file because ``check_split_leak`` takes a path, and this may not change its
    signature. One temporary file per export, deleted with the scratch directory.
    """
    lines: list[str] = []
    for observation in store.observations_of(patch.patch_id):
        # `question_is_held_out` is a *wire* field the route computes; the row carries the source it
        # was computed from, and reading that is one fewer thing to keep in step.
        if observation.source is Source.eval and observation.question:
            lines.append(
                json.dumps(
                    {
                        "question_id": observation.question_id or observation.observation_id,
                        "question": observation.question,
                    }
                )
            )
    if test_split.exists():
        lines += [
            line for line in test_split.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    path = scratch / "held_out.jsonl"
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8", newline="\n")
    # The count is questions *supplied*, not questions the rule used: it discards anything 25
    # characters or shorter, and this does not know that number without restating it.
    return path, len(lines)


# ── writing ──────────────────────────────────────────────────────────────────


def _write_bundle(
    bundle: Path,
    *,
    patch: Patch,
    store: FeedbackStore,
    corpus_root: Path,
    relative: Path,
    after: str,
    diff: str,
) -> Path:
    """The directory, in the layout ADR 0015 §4 declares."""
    (bundle / "after" / relative.parent).mkdir(parents=True, exist_ok=True)
    (bundle / "evidence").mkdir(parents=True, exist_ok=True)

    # Every write here goes through `_write`, which pins the line feed, and that is not a style
    # choice.
    #
    # `Path.write_text` defaults to `newline=None`, which on Windows translates each line feed into
    # a carriage-return pair. That made `changes.patch`'s separators CRLF, and `git apply` --
    # comparing against the index, where git stores line feeds -- read the stray carriage return as
    # part of the content and refused with "patch does not apply". On **every bundle this tool would
    # ever have produced**. Caught by driving the end-to-end path against the real corpus rather
    # than by a unit test, because the defect is in the bytes on disk and in no value the code holds.
    _write(bundle / "changes.patch", diff)
    _write(bundle / "after" / relative, after)

    observations = store.observations_of(patch.patch_id)
    _write(
        bundle / "MANIFEST.yaml",
        yaml.safe_dump(
            {
                "bundle_id": f"bnd-{patch.patch_id}",
                "patch_id": patch.patch_id,
                "intent": patch.intent.value,
                "asset_id": patch.asset_id,
                "field_path": patch.field_path,
                "file": str(relative).replace("\\", "/"),
                "base_corpus_content_hash": patch.base_corpus_content_hash,
                "observations": [o.observation_id for o in observations],
                "question_ids": [o.question_id for o in observations if o.question_id],
                "ladder": dict(patch.ladder),
                # Deliberately absent from the *manifest*, and recorded on the patch instead.
                # An engineer reading `MANIFEST.yaml` has nothing to compare it against before
                # applying, and a hash-shaped string nobody can check is worse than an absence.
                # `derived_state` is the reader that needs it, and it reads the store.
            },
            sort_keys=False,
            allow_unicode=True,
        ),
    )

    _write(bundle / "COMMIT_MSG.txt", _commit_message(patch, observations))

    # Reader prose lives here and never in the commit message: a sentence somebody typed should not
    # become a line of a commit log that some other tool later renders unescaped.
    _write(bundle / "evidence" / "observations.md", _observations_markdown(observations))
    _write(
        bundle / "evidence" / "ladder.json",
        json.dumps(dict(patch.ladder), indent=2, sort_keys=True) + "\n",
    )
    return bundle


def _write(path: Path, text: str) -> None:
    """UTF-8 with the line feed pinned. See the note in :func:`_write_bundle` for why."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _commit_message(patch: Patch, observations: tuple) -> str:
    """Generated from the typed fields. **Carries no reader prose** (see above)."""
    subject = f"Reword {patch.asset_id}.{patch.field_path}"
    if len(subject) > 72:
        subject = subject[:69] + "..."
    ids = ", ".join(o.observation_id for o in observations) or "(none)"
    questions = ", ".join(o.question_id for o in observations if o.question_id) or "(none)"
    return (
        f"{subject}\n"
        f"\n"
        f"Drafted from the return path (governed-bi ADR 0015).\n"
        f"\n"
        f"asset:        {patch.asset_id}\n"
        f"field:        {patch.field_path}\n"
        f"observations: {ids}\n"
        f"questions:    {questions}\n"
        f"authored against corpus {patch.base_corpus_content_hash[:16]}\n"
        f"\n"
        f"The reasoning and the evidence are in the bundle's evidence/ directory, not here.\n"
    )


def _observations_markdown(observations: tuple) -> str:
    lines = [
        "# What was observed",
        "",
        "Verbatim, inside fences. Fenced because this text is not the engine's and must not be",
        "read as prose to copy: an imported question comes from the **held-out** split, and a",
        "phrase carried from one into a corpus asset contaminates every measurement taken",
        "afterwards.",
        "",
    ]
    for observation in observations:
        lines += [
            f"## {observation.observation_id}",
            "",
            f"- category: `{observation.category.value if observation.category else 'none'}`",
            f"- outcome: `{observation.outcome or 'unknown'}`",
            f"- arm: `{observation.arm or 'n/a'}` question: `{observation.question_id or 'n/a'}`",
            f"- held-out question: **{'yes' if observation.source.value == 'eval' else 'no'}**",
            "",
            "```text",
            observation.question,
            "```",
            "",
        ]
        if observation.missing_tables:
            lines += [
                "Tables the reference answer needs that the turn was not allowed to read:",
                "",
                *[f"- `{table}`" for table in observation.missing_tables],
                "",
            ]
        if observation.note:
            lines += ["```text", observation.note, "```", ""]
    return "\n".join(lines)


def _apply_instructions(bundle: Path, corpus_root: Path) -> str:
    return (
        f"\nApply it, in the corpus repository:\n\n"
        f"  cd {corpus_root}\n"
        f"  git checkout -b return/{bundle.name}\n"
        f"  git apply -p1 {bundle / 'changes.patch'}\n"
        f"  git commit -F {bundle / 'COMMIT_MSG.txt'}\n"
    )


# ── plumbing ─────────────────────────────────────────────────────────────────


#: git's marker for a hunk line that is not newline-terminated. `difflib` does not emit it -- it is
#: a git convention rather than a diff one -- and without it `git apply` reads the next line as a
#: continuation of this one and refuses the whole patch as corrupt.
NO_NEWLINE = "\\ No newline at end of file\n"


def _unified(relative: Path, before: str, after: str) -> str:
    """A unified diff `git apply` accepts, including for a file with no trailing newline.

    **The marker is why this is not a one-liner.** Reproduced in a real repository: without
    ``\\ No newline at end of file`` the ``-old`` and ``+new`` lines concatenate and
    ``git apply --check`` answers *corrupt patch at line 7*. A corpus file saved without a final
    newline is ordinary, so every bundle touching one was unappliable -- with a message that blames
    the patch rather than the writer.

    The marker goes after any hunk line whose source text did not end in a newline, which `difflib`
    tells us only indirectly: a payload line it emits carries the original terminator or does not.
    """
    posix = str(relative).replace("\\", "/")
    out: list[str] = []
    for line in difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{posix}",
        tofile=f"b/{posix}",
    ):
        out.append(line if line.endswith("\n") else line + "\n")
        # A payload line (context, removal or addition) that did not end in a newline is the last
        # line of an unterminated file. The headers are excluded by shape, not by position: a
        # `+++`/`---`/`@@` line always ends in a newline from `difflib`, so only real content
        # reaches here without one.
        if not line.endswith("\n"):
            out.append(NO_NEWLINE)
    return "".join(out)


def _summarise(diff: str) -> str:
    """Lines added and removed, ignoring the headers and the no-newline marker.

    The marker begins with a backslash and used to be counted as neither -- but its *presence*
    shifted the arithmetic, so a one-line change on an unterminated file reported "0 line(s) added,
    1 removed". Excluded explicitly, because a reader checking the diff against this count is the
    one person who would notice the bundle was malformed.
    """
    payload = [
        line
        for line in diff.splitlines()
        if not line.startswith(("+++", "---", "@@", "\\"))
    ]
    added = sum(1 for line in payload if line.startswith("+"))
    removed = sum(1 for line in payload if line.startswith("-"))
    return f"{added} line(s) added, {removed} removed"


def _file_declaring(corpus_root: Path, patch: Patch) -> Path | None:
    """The file that declares this asset, found by asking ``locate`` rather than by guessing a path.

    Guessing is what ``store.write`` does and it is why it writes to the wrong place: a table's
    inline column lives in the table's file, whose name is not derivable from the column's id.
    """
    from governed_bi.corpus.patch import FieldNotLocatable

    for candidate in sorted(corpus_root.rglob("*.yaml")):
        if ".git" in candidate.parts:
            continue
        try:
            locate(candidate, asset_id=str(patch.asset_id), field_path=str(patch.field_path))
        except FieldNotLocatable:
            continue
        return candidate
    return None


def _corpus_root(explicit: str | None) -> Path:
    import os

    raw = explicit or os.environ.get("GOVERNED_BI_CORPUS_DIR")
    if not raw:
        raise SystemExit(
            "no corpus: pass --corpus-dir or set GOVERNED_BI_CORPUS_DIR. This tool reads the tree "
            "it is producing a diff against, so it cannot guess one."
        )
    return _resolve(raw)


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)


if __name__ == "__main__":
    sys.exit(main())
