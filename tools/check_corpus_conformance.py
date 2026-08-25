"""Does a corpus tree obey ADR 0005's field spec? Exit 1 if not.

A thin adapter over ``governed_bi.conform``, which is where the twenty-two rules live. This file
owns argv, the printed report, the ``--json`` inventory and the exit codes — **0** nothing to
report, **1** at least one finding, **2** could not run at all. Nothing here is a rule; a question
about what a rule *asks* is answered in ``src/governed_bi/conform/``.

Three modes. ``--file`` checks one asset file and is what the rebuild loop calls after each
write; the default walks a whole tree and prints a per-rule report. Rules that need a **second
asset** are reported as *not evaluated* in ``--file`` mode rather than passed, because a rule
that silently skips is worse than one that fails. Needing an external manifest is not that: V11
and V12 answer from one asset, so they run in ``--file`` mode too and report the missing manifest
when one is missing. See :data:`~governed_bi.conform.WHOLE_TREE_ONLY`.

Why the rules exist: the corpus this kit replaced (measured 2026-08-08) passed both rules the
Pydantic model enforces (``1 <= len(summary) <= 250``, identifier present) and violated most of
what the ADR says in prose -- 100% of one arm's schema/table/column summaries were identifier
lists, 0/928 joins carried a ``body``, 441 of 949 terms dropped an alias the retrieval bridge
depends on. Prose rules that nothing executes are not rules.

Why the rules live in the engine and this file is only their CLI: they are statements about what
the engine will do with an asset -- V16 measures a table with ``serve/context.py``'s own renderer,
V17a parses a metric expression at the dialect ``govern/`` parses generated SQL at, V21 runs
``govern/guard.py``'s own ``GUARD_RULES``. ADR 0016 §Context 1 is that argument. Until 2026-08-25
it was an argument about *this script*, which meant the only way for the engine to ask "does this
corpus satisfy my rules" was to spawn a subprocess and parse its JSON.

The rules read raw YAML rather than ``corpus.store.load``: they must give a useful answer on a
half-written tree, where the loader would raise. V14 is the one rule that asks the loader.

``identifier_fields`` comes from ``ASSET_REGISTER`` and is not restated. Two spellings of one
policy is how ``airline."Air Carriers"`` ended up with no table asset while 24 few-shots cited it.

``--json`` emits the findings and **exits 0**, because it is an inventory rather than a gate:
``tools/check_ratchet.py`` is what decides whether the inventory is allowed. A mode that both
reported and failed would make the ratchet unable to read a tree that has findings, which is every
tree it exists for.

**On reading the held-out split (V12).** This tool loads ``test_final.jsonl`` to *forbid* its
text. That is the opposite of the defect it guards: tuning a corpus against the split adapts to
it, while refusing content that appears in it cannot. Nothing here writes an asset.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from governed_bi.conform import (
    RULE_DESCRIPTIONS,
    Manifests,
    problems_with_asset_file,
    problems_with_corpus,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "BIRD-corpus"
DEFAULT_DATASET = ROOT.parent / "BIRD-Data-Obfuscation" / "eval_dataset"


def _where_of(line: str) -> str:
    """The ``file:asset`` a finding is about, or ``""`` if the line is not in that shape.

    Split off the front rather than parsed, because the message that follows contains colons of its
    own. ``""`` drops the line from the JSON: an identity the ratchet cannot key on is worse than a
    missing finding, since it would pin as one thing and re-appear as another.
    """
    parts = str(line).split(":", 2)
    return f"{parts[0]}:{parts[1]}" if len(parts) >= 3 else ""


def _keyed(lines: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """``(rule, line)`` pairs as JSON rows, raising on any line that yields no identity.

    This used to filter instead of raise, on the stated grounds that "an identity the ratchet cannot
    key on is worse than a missing finding". That is backwards. A missing finding is a **blind
    gate**: V23's lines were unkeyable on POSIX, so a duplicate asset id -- the one defect that
    raises in ``build_index`` after the commit -- never reached the ratchet at all, and nobody
    noticed because the rule reports zero on the corpus we measure. A rule that cannot be keyed is a
    bug in the rule, and the tool that consumes it has to say so out loud.
    """
    rows = [{"rule": rule, "where": _where_of(line), "message": str(line)} for rule, line in lines]
    unkeyable = [row for row in rows if not row["where"]]
    if unkeyable:
        raise ValueError(
            f"{len(unkeyable)} finding(s) cannot be keyed as `file:asset`, so the ratchet could "
            "not pin them. Fix the rule to emit `where_of(...)` as its prefix. First: "
            f"[{unkeyable[0]['rule']}] {unkeyable[0]['message']!r}"
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_corpus_conformance", description=__doc__)
    ap.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--file", type=Path, default=None, help="check one asset file (rebuild loop)")
    ap.add_argument("--trap-manifest", type=Path, default=DEFAULT_DATASET / "trap_manifest.json")
    ap.add_argument("--table-manifest",
                    type=Path, default=DEFAULT_DATASET / "trap_table_manifest.json")
    ap.add_argument("--rename-map",
                    type=Path, default=DEFAULT_DATASET / "schema_rename_map.json")
    ap.add_argument("--test-split", type=Path, default=DEFAULT_DATASET / "test_final.jsonl")
    ap.add_argument("--max-lines", type=int, default=15, help="findings printed per rule")
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit findings as JSON on stdout (for tools/check_ratchet.py); exit 0 either way",
    )
    args = ap.parse_args(argv)

    # Resolved here and not in the library: the paths are this repository's layout, and ADR 0016
    # records why a default that resolves is a hazard in CI, where the two data repositories are
    # nested rather than siblings.
    manifests = Manifests(
        trap=args.trap_manifest,
        table=args.table_manifest,
        rename=args.rename_map,
        test_split=args.test_split,
    )

    if args.file:
        report = problems_with_asset_file(args.file, manifests)
        scope = f"{args.file}"
    else:
        # The library raises for a path that is not a directory; asked here first so the answer is
        # this tool's exit 2 -- "could not run" -- rather than a traceback.
        if not args.corpus_dir.is_dir():
            print(f"no corpus at {args.corpus_dir}", file=sys.stderr)
            return 2
        report = problems_with_corpus(args.corpus_dir, manifests)
        scope = f"{args.corpus_dir} ({report.asset_count} assets)"

    findings, skipped, whole = report.findings, report.not_evaluated, report.whole_tree

    if args.json:
        # A finding's **identity** is (rule, asset), and that is all this emits alongside the
        # message. The ratchet pins identities: a reworded message must not read as a new finding,
        # and a finding moving to another asset must not read as the same one.
        print(
            json.dumps(
                {
                    "corpus": str(args.corpus_dir if whole else args.file),
                    "whole_tree": whole,
                    "not_evaluated": skipped,
                    "findings": _keyed(
                        (rule, line)
                        for rule in RULE_DESCRIPTIONS
                        for line in sorted(findings.get(rule, ()))
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"corpus conformance: {scope}")
    if whole:
        print(f"  {report.assets_by_type}")
    print(f"  {'rule':<5}{'violations':>12}  description")
    total = 0
    for rule, description in RULE_DESCRIPTIONS.items():
        if rule in skipped:
            print(f"  {rule:<5}{'not evaluated':>12}  {description}  [{skipped[rule]}]")
            continue
        n = len(findings.get(rule, ()))
        total += n
        print(f"  {rule:<5}{n:>12}  {description}")

    if total:
        print(f"\n{total} violation(s):", file=sys.stderr)
        for rule in RULE_DESCRIPTIONS:
            lines = findings.get(rule, ())
            if not lines:
                continue
            print(f"\n  [{rule}] {RULE_DESCRIPTIONS[rule]} — {len(lines)}", file=sys.stderr)
            for line in sorted(lines)[: args.max_lines]:
                print(f"    {line}", file=sys.stderr)
            if len(lines) > args.max_lines:
                print(f"    … {len(lines) - args.max_lines} more", file=sys.stderr)
        return 1

    unevaluated = f"; {len(skipped)} rule(s) not evaluated" if skipped else ""
    print(f"\nall evaluated rules pass{unevaluated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
