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

**Two content checks are fatal here and nowhere else.**

* **An excluded column named in model-visible prose.** ADR 0003 found a corpus asset naming a
  ``governance.excluded`` column in text that was then injected verbatim into the SQL prompt, and
  concluded a content-scanning validator was the structural answer. None shipped. This is it, at the
  one gate a change has to pass. Measured 2026-08-23: **zero** assets are excluded in either corpus,
  so today the check has no population and cannot refuse a legitimate bundle — which is exactly why
  adding it now is free.
* **A held-out question quoted in an asset.** The importer's rows carry question text from the
  held-out split, and the loop's whole purpose is that a person reads it and writes corpus prose.
  A verbatim phrase travelling from a graded question into a ``summary`` contaminates every EX
  number measured afterwards, invisibly. Conformance rule V12 is the check; running it here is the
  obligation ``eval/feedback_import.py`` records as owed.

Neither is a claim of safety. ``tools/check_train_only.py``'s own docstring says paraphrase leaks
are undetectable, so the last control is a person who knows what they are reading — which is why the
review surface labels the question as held-out rather than presenting it as neutral context.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.patch import StaleValue, UnwritableValue, apply_edit, locate
from governed_bi.corpus.store import load
from governed_bi.feedback.events import Patch, PatchIntent, PatchState, Source
from governed_bi.feedback.store import FeedbackStore
from governed_bi.paths import REPO_ROOT

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

    refusals = _refuse(patch, corpus_root=corpus_root, store=store)
    print(diff or "(the edit produced no textual change)")
    print(f"\n{_summarise(diff)}")

    if refusals:
        print("\nREFUSED, and no bundle was written:")
        for refusal in refusals:
            print(f"  {refusal}")
        print(
            "\nBoth of these are fatal by decision rather than by convention: an excluded column "
            "named in model-visible prose is ADR 0003's unfixed finding, and a held-out question "
            "quoted in an asset contaminates every measurement taken afterwards."
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
        store.move_patch(
            patch.patch_id,
            to=PatchState.exported,
            detail=f"bundle at {bundle.name}",
        )
        print("patch state: draft -> exported")
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


def _refuse(patch: Patch, *, corpus_root: Path, store: FeedbackStore) -> list[Refusal]:
    """Every fatal finding on this patch's new text, in the order they were decided."""
    out: list[Refusal] = []
    becomes = str(patch.becomes or "")

    # V19 — an excluded column's name in model-visible prose. `for_analyst` is the same function
    # the serve path uses to decide what an analyst may see, so this asks the question the engine
    # asks rather than a second version of it.
    assets, _ = load(corpus_root)
    analyst = for_analyst(assets)
    excluded = {key.rsplit(".", 1)[-1] for key in getattr(analyst, "excluded_columns", ()) or ()}
    for name in sorted(excluded):
        if name and re.search(rf"\b{re.escape(name)}\b", becomes):
            out.append(
                Refusal(
                    "V19",
                    f"the new text names {name!r}, a governance-excluded column. `body` reaches the "
                    "model's prompt and `summary` reaches the retrieval index, so the name would "
                    "leak even though the column itself is correctly hidden -- ADR 0003's finding, "
                    "verbatim.",
                )
            )

    # V12 — a held-out question quoted in an asset. Compared against the questions of the
    # observations this patch answers, which is the population that can actually have leaked: the
    # steward read those and nothing else.
    for observation in store.observations_of(patch.patch_id):
        # `question_is_held_out` is a *wire* field the route computes; the row itself carries the
        # source it was computed from, and reading that is one fewer thing to keep in step.
        if observation.source is not Source.eval:
            continue
        overlap = _longest_shared_phrase(observation.question, becomes)
        if len(overlap.split()) >= 5:
            out.append(
                Refusal(
                    "V12",
                    f"the new text shares the phrase {overlap!r} with a held-out question "
                    f"({observation.question_id or observation.observation_id}). A phrase from a "
                    "graded question in an asset contaminates every EX number measured afterwards, "
                    "and the contamination is invisible.",
                )
            )
    return out


def _longest_shared_phrase(question: str, text: str) -> str:
    """The longest run of consecutive words the two share, case-insensitively.

    Word-level rather than character-level: a shared substring of characters is mostly noise
    ("the reference tab"), and a shared run of five words is the thing V12 is about.
    """
    left = re.findall(r"\w+", question.lower())
    right = re.findall(r"\w+", text.lower())
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    block = max(matcher.get_matching_blocks(), key=lambda b: b.size, default=None)
    if block is None or block.size == 0:
        return ""
    return " ".join(left[block.a : block.a + block.size])


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
    import yaml

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
                # Deliberately absent: `expected_corpus_content_hash`. It is the digest of a tree
                # nobody has written yet, and a hash-shaped string nobody can compare is worse
                # than an absence. `tools/check_landed.py` computes it after the commit.
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
