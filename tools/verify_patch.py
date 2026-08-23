#!/usr/bin/env python
"""The free half of the verification ladder: does this patch break anything? (ADR 0015 §11)

    uv run --frozen python tools/verify_patch.py --patch pat-...
    uv run --frozen python tools/verify_patch.py --patch pat-... --tier T1

**Three tiers, zero model calls, no database.** T0 is the edited asset alone, T1 is the whole tree
with the edit in it, T2 is the metric-expression resolver. Each result is written to the patch's
``ladder`` so the review surface can render what ran, and ``tools/export_bundle.py`` copies them
into the bundle's ``evidence/ladder.json`` for the engineer who applies it.

**Every tier is a delta gate, not an absolute one.** ``../BIRD-corpus`` carries 101 conformance
findings today (measured 2026-08-23), so a tier demanding zero rejects production, gets waived, and
a waiver is how a real finding goes green. What each tier asks is whether *this patch* made things
worse.

**Nothing is staged on disk.** The edit is applied in memory — ``corpus/patch.py::apply_edit``
returns the new text and writes nothing — and the whole-tree checks run over the parsed tree with
the one file's mapping substituted. So there is no copy of a 7,357-file tree per run (8.0 s
measured) and, more importantly, no destination directory for anything to delete: the ladder never
touches ``corpus/snapshot.py``, whose ``rmtree`` was measured deleting a scratch directory of
unrelated files.

**T2 needs no live catalog, which is a correction to the design.** ADR 0015 put the
metric-expression resolver behind a database on the grounds that resolving an identifier needs the
warehouse. It does not: the corpus declares its own tables, columns and joins, and *those* are what
an expression must be consistent with — the warehouse is checked at serve time by ``govern/``. So
T2 is conformance rule V17b over the patched tree, it costs nothing, and it runs offline. The
design's ``tools/check_closed_domains.py`` does not exist and nothing here pretends it does.

**T3 and above are not here.** T3 replays retrieval per question, which is step 6 of the build
order; T4 and T5 spend money. A tier that cannot run must not be reported as passing, so an unrun
tier is simply absent from the ladder rather than recorded as skipped-therefore-fine.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_corpus_conformance as cc  # noqa: E402 - after the path insert, by design

from governed_bi.corpus.patch import (  # noqa: E402
    FieldNotLocatable,
    StaleValue,
    UnwritableValue,
    apply_edit,
)
from governed_bi.feedback.events import PatchIntent  # noqa: E402
from governed_bi.feedback.store import FeedbackStore  # noqa: E402
from governed_bi.paths import REPO_ROOT  # noqa: E402

DEFAULT_DB = "runs/feedback.sqlite"
TIERS = ("T0", "T1", "T2")


@dataclass(frozen=True, slots=True)
class GateResult:
    """One tier's answer. ``passed`` is the gate; the rest is what a reviewer reads."""

    tier: str
    passed: bool
    detail: str
    new_findings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "passed": self.passed,
            "detail": self.detail,
            "new_findings": list(self.new_findings),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--corpus-dir", default=None, help="defaults to GOVERNED_BI_CORPUS_DIR")
    parser.add_argument(
        "--tier",
        default="T2",
        choices=TIERS,
        help="the highest tier to run. T0 is the fastest useful answer",
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="print the results and write nothing to the patch's ladder",
    )
    args = parser.parse_args(argv)

    store = FeedbackStore(_resolve(args.db))
    patch = store.get_patch(args.patch)
    if patch is None:
        print(f"no patch {args.patch!r} in {args.db}", file=sys.stderr)
        return 2
    if patch.intent is not PatchIntent.edit_asset:
        print(
            f"patch {args.patch} has intent {patch.intent.value}. The ladder verifies a corpus "
            "edit, and the other intents author no asset -- there is nothing to check.",
            file=sys.stderr,
        )
        return 2

    corpus_root = _corpus_root(args.corpus_dir)
    target = _file_declaring(corpus_root, str(patch.asset_id), str(patch.field_path))
    if target is None:
        print(f"no file under {corpus_root} declares {patch.asset_id!r}", file=sys.stderr)
        return 2

    try:
        edited = apply_edit(
            target,
            asset_id=str(patch.asset_id),
            field_path=str(patch.field_path),
            was=str(patch.was),
            becomes=str(patch.becomes),
        )
    except (StaleValue, FieldNotLocatable, UnwritableValue) as err:
        # Three reasons the ladder cannot start, and the caller wants the sentence rather than the
        # class: the corpus moved, the field is gone, or the value cannot be written faithfully.
        print(f"T0 fails before it starts: {err}", file=sys.stderr)
        return 1

    wanted = TIERS[: TIERS.index(args.tier) + 1]
    results: list[GateResult] = []

    results.append(_t0(edited, target))
    if "T1" in wanted and results[-1].passed:
        results.append(_t1(corpus_root, target, edited))
    if "T2" in wanted and all(r.passed for r in results):
        results.append(_t2(corpus_root, target, edited))

    for result in results:
        mark = "pass" if result.passed else "FAIL"
        print(f"{result.tier}  {mark}  {result.detail}")
        for finding in result.new_findings[:10]:
            print(f"        {finding}")
        if len(result.new_findings) > 10:
            print(f"        … {len(result.new_findings) - 10} more")

    if not args.no_record:
        for result in results:
            store.record_ladder(patch.patch_id, result.tier, result.as_dict())
        print(f"\nrecorded {len(results)} tier(s) on {patch.patch_id}")

    if not all(r.passed for r in results):
        print(
            "\nThe patch is not ready to hand over. Every tier here is a DELTA gate: it is not "
            "saying the corpus is clean, it is saying this edit made something worse.",
            file=sys.stderr,
        )
        return 1
    unrun = [t for t in TIERS if t not in wanted]
    if unrun:
        print(f"note: {', '.join(unrun)} not run. An unrun tier is absent, never 'passed'.")
    return 0


# ── the tiers ─────────────────────────────────────────────────────────────────


def _t0(edited: str, target: Path) -> GateResult:
    """The edited asset alone: it parses, and no local rule fires on it that did not before.

    Delta and not absolute even here, because a single asset can carry a pinned finding of its
    own -- 94 of the corpus's metrics do. An edit to a `summary` must not be blocked by a
    pre-existing problem in the same file's `expression`.
    """
    before = cc.load_assets(target)
    try:
        document = yaml.safe_load(edited)
    except Exception as err:  # noqa: BLE001 - any parse failure is the answer
        return GateResult("T0", False, f"the edited file does not parse: {err}")
    if not isinstance(document, dict):
        return GateResult("T0", False, "the edited file's top level is not a mapping")

    after = _assets_of(document, target)
    new = _delta(before, after)
    if new:
        return GateResult(
            "T0", False, f"{len(new)} new local finding(s) on {target.name}", tuple(new)
        )
    return GateResult("T0", True, f"{target.name} parses and adds no local finding")


def _t1(corpus_root: Path, target: Path, edited: str) -> GateResult:
    """The whole tree with the edit in it: no new conformance finding, and the engine still builds.

    ``build_index`` is the one that matters and the one a file check cannot reach: a duplicate id
    passes every rule, loads with zero problems, and raises ``ValueError: duplicate index id``
    here -- **after** the commit, if nobody ran this.
    """
    document = yaml.safe_load(edited)
    before = cc.walk(corpus_root)
    after = [
        entry
        for kind, a, path in before
        if path != target
        for entry in ((kind, a, path),)
    ] + _assets_of(document, target)

    new = _delta(before, after, whole_tree=True)
    if new:
        return GateResult("T1", False, f"{len(new)} new whole-tree finding(s)", tuple(new))

    builds, detail = _builds(corpus_root, target, edited)
    if not builds:
        return GateResult("T1", False, detail)
    return GateResult("T1", True, f"no new finding across {len(after)} assets; {detail}")


def _t2(corpus_root: Path, target: Path, edited: str) -> GateResult:
    """The metric-expression resolver over the patched tree (V17b), and no live catalog.

    Reported separately from T1 rather than folded into it, because it answers a different
    question: T1 asks whether the tree still loads and indexes, T2 asks whether a metric's
    definition is *executable*. An expression naming a column on an unjoined table is a query the
    engine cannot write, and nothing about loading the tree notices.
    """
    document = yaml.safe_load(edited)
    before = cc.walk(corpus_root)
    after = [(k, a, p) for k, a, p in before if p != target] + _assets_of(document, target)

    was = {str(f) for f in cc.check_metric_bindings(before)}
    now = {str(f) for f in cc.check_metric_bindings(after)}
    new = sorted(now - was)
    if new:
        return GateResult("T2", False, f"{len(new)} new unresolvable identifier(s)", tuple(new))
    return GateResult("T2", True, f"every metric expression still resolves ({len(now)} pinned)")


# ── plumbing ──────────────────────────────────────────────────────────────────


def _assets_of(document: dict[str, Any], path: Path) -> list[tuple[str, dict[str, Any], Path]]:
    """One parsed document in ``load_assets``' shape, columns unpacked from their table.

    Rebuilt here rather than re-reading the file, because the point is to check text that is not
    on disk. The unpacking mirrors ``cc.load_assets`` -- if that ever grows a third case this has
    to follow, which is why the shape is asserted by
    ``tests/conformance/test_the_ladder_checks_the_edit_and_not_the_file.py``.
    """
    out: list[tuple[str, dict[str, Any], Path]] = [
        (str(document.get("asset_type") or "<missing>"), document, path)
    ]
    if document.get("asset_type") == "table":
        for column in document.get("columns") or ():
            if isinstance(column, dict):
                out.append(("column", {"schema": document.get("schema"), **column}, path))
    return out


def _delta(
    before: list[tuple[str, dict[str, Any], Path]],
    after: list[tuple[str, dict[str, Any], Path]],
    *,
    whole_tree: bool = False,
) -> list[str]:
    """Findings present after the edit and not before, as ``[rule] message``.

    Compared by **message**, not by (rule, asset) as the ratchet does. The two want different
    things: the ratchet pins a corpus's standing debt, where a reworded message must not read as
    new; this asks what one edit changed, and there the message is the change. An edit that swaps
    one V4 violation on an asset for a different V4 violation on the same asset is something a
    reviewer needs to see.
    """
    return sorted(_findings(after, whole_tree=whole_tree) - _findings(before, whole_tree=whole_tree))


def _findings(
    assets: list[tuple[str, dict[str, Any], Path]], *, whole_tree: bool
) -> set[str]:
    out: set[str] = set()
    for kind, a, path in assets:
        for rule, lines in cc.check_local(kind, a, cc._where(kind, a, path)).items():
            out |= {f"[{rule}] {line}" for line in lines}
    if whole_tree:
        for rule, lines in (
            ("V9", cc.check_references(assets)),
            ("V19", cc.check_excluded_not_named(assets)),
            ("V23", cc.check_unique_ids(assets)),
        ):
            out |= {f"[{rule}] {line}" for line in lines}
    return out


def _builds(corpus_root: Path, target: Path, edited: str) -> tuple[bool, str]:
    """``build_structure`` does not gain problems and ``build_index`` does not raise.

    The edited file is handed to the loader as text through ``load_file``, so the asset the engine
    would build is the asset that is checked -- rather than a dict this tool assembled, which is a
    second loader and would disagree with the first.
    """
    from governed_bi.corpus.store import load, load_file
    from governed_bi.retrieve.index import build_index
    from governed_bi.retrieve.structure import build_structure

    assets, _ = load(corpus_root)
    # The baseline is `build_structure`'s OWN problem count on the unpatched tree. The first
    # version of this compared it against `load`'s count -- two different populations, so a tree
    # with 0 load problems and 10 structure problems reported "rose from 0 to 10" on an edit that
    # changed nothing structural. A delta gate whose two sides measure different things is a gate
    # that fails every patch.
    _, baseline_problems = build_structure(assets)
    baseline = len(baseline_problems)

    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / target.name
        staged.write_text(edited, encoding="utf-8", newline="\n")
        replacements, edit_problems = load_file(staged, where=str(target))
    if edit_problems:
        return False, f"the loader refuses the edited file: {edit_problems[0]}"

    replaced_ids = {str(getattr(a, "id", "")) for a in replacements}
    patched = [a for a in assets if str(getattr(a, "id", "")) not in replaced_ids] + list(
        replacements
    )

    structure, structure_problems = build_structure(patched)
    if len(structure_problems) > baseline:
        return False, (
            f"build_structure problems rose from {baseline} to {len(structure_problems)}"
        )
    try:
        # `_index_entries` rather than a local projection: it is what `session.from_assets` calls,
        # and a second entry builder here would be checking an index the engine does not build.
        from governed_bi.serve.session import _index_entries

        build_index(_index_entries(patched, structure), embedder=None, vector_cache=None)
    except Exception as err:  # noqa: BLE001 - the raise IS the finding
        return False, f"build_index raises: {type(err).__name__}: {err}"
    return True, f"build_structure holds at {len(structure_problems)} problem(s); build_index built"


def _file_declaring(corpus_root: Path, asset_id: str, field_path: str) -> Path | None:
    from governed_bi.corpus.patch import read_field

    for candidate in sorted(corpus_root.rglob("*.yaml")):
        if ".git" in candidate.parts:
            continue
        try:
            read_field(candidate, asset_id=asset_id, field_path=field_path)
        except FieldNotLocatable:
            continue
        return candidate
    return None


def _corpus_root(explicit: str | None) -> Path:
    import os

    raw = explicit or os.environ.get("GOVERNED_BI_CORPUS_DIR")
    if not raw:
        raise SystemExit("no corpus: pass --corpus-dir or set GOVERNED_BI_CORPUS_DIR")
    return _resolve(raw)


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)


if __name__ == "__main__":
    sys.exit(main())
