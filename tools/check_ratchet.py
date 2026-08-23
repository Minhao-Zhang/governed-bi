#!/usr/bin/env python
"""Conformance findings may shrink and may not grow, and closing one must be declared.

    uv run --frozen python tools/check_ratchet.py --pins ../BIRD-corpus/.conformance-pins.txt
    uv run --frozen python tools/check_ratchet.py --pins ... --write    # after fixing something

**Why a ratchet at all.** The corpus carries findings today — 125 on ``../BIRD-corpus``, measured
2026-08-23 — so a gate that demands zero rejects production. That gate gets waived, and a waiver is
how a real finding goes green. The ratchet is the version that can actually run on every commit:
the set may shrink freely, it may not grow, and *shrinking* fails the build too until the pin file
is updated in the same commit.

**By name and not by count.** 125 findings and 125 *different* findings are the same integer, so a
count-based ratchet passes a commit that fixes one metric and breaks another. A finding's identity
here is **(rule, asset)** — a rule id and the ``file:asset`` the finding is about — and not the
message, because a reworded message is not a new finding and a finding that moved to another asset
is not the same one.

**What that identity cannot see, stated rather than left to be discovered:** a metric with two
``divide`` calls is one pin, so fixing one of the two closes nothing. That is deliberate — a
per-call identity would need a stable index into an expression, and an expression that is edited
renumbers every call after the edit. 107 V17a findings live on 94 assets, so this collapses 13.

**The pin file lives in the corpus repository, not here.** The findings are properties of a corpus
tree, and this tree is the engine — pinning them here would mean an engine commit could not be
reviewed without a corpus in the reviewer's checkout, and two corpora could never both be clean.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "BIRD-corpus"
DEFAULT_PINS = DEFAULT_CORPUS / ".conformance-pins.txt"

HEADER = """# Conformance findings pinned for `tools/check_ratchet.py`.
#
# One line per (rule, asset). The set may SHRINK and may not GROW; closing a finding means
# deleting its line in the same commit that closes it, which is why a shrink fails the build
# until this file is updated. Regenerate with `--write` after a real fix, never to make a build
# green.
#
# Names, not a count: N findings and N different findings are the same integer.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--pins", type=Path, default=DEFAULT_PINS)
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite the pin file from the tree as it stands. For a real fix, not for a red build",
    )
    args = parser.parse_args(argv)

    if not args.corpus_dir.is_dir():
        print(f"no corpus at {args.corpus_dir}", file=sys.stderr)
        return 2

    found = _findings(args.corpus_dir)
    if found is None:
        return 2

    if args.write:
        args.pins.write_text(
            HEADER + "".join(f"{rule}\t{where}\n" for rule, where in sorted(found)),
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {len(found)} pin(s) to {args.pins}")
        return 0

    if not args.pins.exists():
        print(
            f"no pin file at {args.pins}. Run with --write once to record the {len(found)} "
            "finding(s) this tree carries today, and commit it to the corpus repository.",
            file=sys.stderr,
        )
        return 2

    pinned = _pins(args.pins)
    new = sorted(found - pinned)
    closed = sorted(pinned - found)

    print(f"corpus {args.corpus_dir}")
    print(f"  pinned {len(pinned)}, found {len(found)}")

    if new:
        print(f"\n{len(new)} NEW finding(s) -- the ratchet only turns one way:", file=sys.stderr)
        for rule, where in new[:20]:
            print(f"  [{rule}] {where}", file=sys.stderr)
        if len(new) > 20:
            print(f"  … {len(new) - 20} more", file=sys.stderr)

    if closed:
        print(
            f"\n{len(closed)} pinned finding(s) are GONE, and the pin file still lists them:",
            file=sys.stderr,
        )
        for rule, where in closed[:20]:
            print(f"  [{rule}] {where}", file=sys.stderr)
        if len(closed) > 20:
            print(f"  … {len(closed) - 20} more", file=sys.stderr)
        print(
            "\nThis fails deliberately. A fix that does not update the pin file leaves the "
            "ratchet loose by exactly that many findings, so the next commit could reintroduce "
            "one for free. Re-run with --write and commit the pin file with the fix.",
            file=sys.stderr,
        )

    if new or closed:
        return 1
    print("  the ratchet holds: the finding set is exactly what is pinned")
    return 0


def _findings(corpus: Path) -> set[tuple[str, str]] | None:
    """Every (rule, asset) the conformance tool reports, through its ``--json`` mode.

    A subprocess rather than an import, because the tool is a script with a ``main`` that parses
    ``sys.argv`` and owns its own exit codes -- and because running it the way CI runs it is the
    only way this gate is checking what CI checks.
    """
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "check_corpus_conformance.py"),
            "--corpus-dir",
            str(corpus),
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(
            f"conformance --json exited {result.returncode}:\n{result.stderr[:2000]}",
            file=sys.stderr,
        )
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as err:
        print(f"conformance --json did not emit JSON: {err}", file=sys.stderr)
        return None

    if payload.get("not_evaluated"):
        # Reported and not fatal. A rule that could not run has zero findings, which would read as
        # "closed" against a pin -- so the operator has to see it, and `closed` says which.
        print(
            "note: "
            + "; ".join(f"{rule} not evaluated ({why})" for rule, why in payload["not_evaluated"].items())
        )
    return {(f["rule"], f["where"]) for f in payload["findings"]}


def _pins(path: Path) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rule, _, where = line.partition("\t")
        if where:
            out.add((rule.strip(), where.strip()))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
