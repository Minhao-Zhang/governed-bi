#!/usr/bin/env python
"""Conformance findings may shrink and may not grow, and closing one must be declared.

    uv run --frozen python tools/check_ratchet.py --corpus-dir ../BIRD-corpus
    uv run --frozen python tools/check_ratchet.py --corpus-dir ... --write   # after a real fix

**Why a ratchet at all.** The corpus carries findings today — 125 on ``../BIRD-corpus``, measured
2026-08-23 — so a gate that demands zero rejects production. That gate gets waived, and a waiver is
how a real finding goes green. The ratchet is the version that could run on every commit: the set
may shrink freely, it may not grow, and *shrinking* fails too until the pin file is updated in the
same commit. Nothing runs it on a commit; see the next paragraph for why that is a decision.

**It is not in any CI, and only half of the old reason survives.** The pin file is now
``.conformance/bird-corpus-pins.txt`` **in this repository** (see the last paragraph), so "the
baseline is a sibling of this checkout" is no longer true of it. The corpus tree still is, and that
is what stops a CI step: ``.github/workflows/ci.yml``'s nightly job checks out a corpus and runs
``check_corpus_delta.py`` against it, and running *this* tool there as well would fail the build on
the first genuine fix -- a closure is exit 1 here until somebody rewrites the pin file, which is
the policy and not an accident. So the automated reader is the delta gate; this tool is the
instrument a person runs when they want the whole debt named rather than the change. It is declared
manual in ``tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py`` and its
behaviour is pinned on a synthetic corpus in
``tests/conformance/test_the_ratchet_only_turns_one_way.py``.

**By name and not by count.** 125 findings and 125 *different* findings are the same integer, so a
count-based ratchet passes a commit that fixes one metric and breaks another. A finding's identity
here is **(rule, asset)** — a rule id and the ``file:asset`` the finding is about — and not the
message, because a reworded message is not a new finding and a finding that moved to another asset
is not the same one.

**A pin carries a count, because the identity alone cannot see growth.** 125 findings live on 101
identities: 107 V17a findings on 85 metrics and 17 V17b findings on 15. So an asset that is already
pinned could take on any number of *further* violations of the same rule without the finding set
growing by one line — and "the set may not grow" is this tool's entire claim. The identity stays
``(rule, asset)`` and each pin records how many findings it stands for; a count that rises fails the
build exactly like a new identity does, and a count that falls fails like a closure.

Per-asset and not per-call, still. A per-call identity would need a stable index into an expression,
and an expression that is edited renumbers every call after the edit. A count is stable under both
rewording and renumbering, which is what the identity was chosen for.

**Pins written before counts existed carry none, and this tool says so rather than implying it
checked.** A two-field line is read as "count not recorded" and only its presence is enforced. Run
``--write`` once to record them.

**The pin file lives HERE, in ``.conformance/bird-corpus-pins.txt``, and it used to live beside the
corpus.** The old arrangement had a measured cost that this one does not: at the corpus root the pin
file entered ``corpus_content_hash``, moving the treatment identity every measured number is pinned
to from ``6e5c7b4be83d5682…`` to ``8bb37531cff9155a…`` — the gate changed the thing it was gating.
Putting it in a ``.conformance/`` directory the digest ignores fixed the symptom by an exclusion
list; putting it in this repository removes the class, because a file here cannot be in a digest of
a tree over there. Verified after the move: ``../BIRD-corpus`` at ``74ff80c4`` hashes
``6e5c7b4be83d56828bab66183eec03bbdcf486d7454d34acd066530010ebed85``, unchanged, with no
``.conformance/`` and no ``.github/`` in that tree at all.

What the old placement bought, and what it costs to give up: the findings are properties of *a*
corpus, so a pin file here is a pin file about one named tree, and two corpora cannot both be
described by :data:`DEFAULT_PINS`. That is why the default is named for the corpus it is about
rather than ``pins.txt``, and why ``--pins`` stays a flag: a second corpus gets a second file, not
a second meaning for this one. The other half of the old objection — that an engine commit could
not be reviewed without a corpus in the reviewer's checkout — was already false, because reviewing
this file needs the *pins*, and running the tool needs the corpus either way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Sibling script, path-added like `check_corpus_conformance.py` imports its rules.
from conformance_findings import CannotRun, compare, read  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "BIRD-corpus"
#: In **this** repository, and named for the corpus it describes. See the docstring's last
#: paragraph for what moving it here removed and what it gave up. ``.conformance/`` keeps the name
#: it had beside the corpus so the two are recognisably the same artifact; it is also still in
#: ``corpus/identity.py::_NON_CORPUS_DIRS``, defensively, for a corpus that re-acquires one.
DEFAULT_PINS = ROOT / ".conformance" / "bird-corpus-pins.txt"

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

    try:
        report = read(args.corpus_dir)
    except CannotRun as err:
        print(str(err), file=sys.stderr)
        return 2
    found = report.counts
    if report.not_evaluated:
        # Reported and not fatal here. A rule that could not run has zero findings, which
        # would read as "closed" against a pin -- so the operator has to see it, and
        # `closed` says which. `check_corpus_delta.py` makes the same condition fatal under
        # `--every-rule-must-run`, because in CI there is no reason for a manifest to be
        # missing and nobody is reading the note.
        print(
            "note: "
            + "; ".join(
                f"{rule} not evaluated ({why})"
                for rule, why in report.not_evaluated.items()
            )
        )

    if args.write:
        # `write_text` does not create parents, so the first `--write` against a path whose
        # `.conformance/` nobody has made raised `FileNotFoundError` from inside the tool. The
        # default path's parent is tracked here now, so the case this guards is a `--pins`
        # pointed somewhere new -- a second corpus getting its first pin file, which is the only
        # tree this flag is for.
        args.pins.parent.mkdir(parents=True, exist_ok=True)
        args.pins.write_text(
            HEADER
            + "".join(
                f"{rule}\t{where}\t{count}\n"
                for (rule, where), count in sorted(found.items())
            ),
            encoding="utf-8",
            newline="\n",
        )
        print(
            f"wrote {len(found)} pin(s) covering {sum(found.values())} finding(s) to {args.pins}"
        )
        return 0

    if not args.pins.exists():
        print(
            f"no pin file at {args.pins}. Run with --write once to record the {sum(found.values())} "
            "finding(s) this tree carries today, and commit it beside this tool.",
            file=sys.stderr,
        )
        return 2

    pinned = _pins(args.pins)
    # The arithmetic is `conformance_findings.compare`; the **policy** is this tool's.
    # A closure fails here and passes in `check_corpus_delta.py`, and that is a real
    # disagreement rather than a bug: this baseline is a file somebody has to keep in
    # step, and a fix that does not update it leaves the ratchet loose by that many
    # findings. Git needs no updating, so there the same event is simply progress.
    change = compare(pinned, found)
    new, closed, grew, shrank = change.added, change.closed, change.grew, change.shrank
    uncounted = sum(1 for count in pinned.values() if count is None)

    print(f"corpus {args.corpus_dir}")
    print(f"  pinned {len(pinned)}, found {len(found)}, findings {sum(found.values())}")
    if uncounted:
        print(
            f"  {uncounted} pin(s) carry no count, so only their presence is checked. "
            "Re-run with --write to record them."
        )

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

    for label, keys, why in (
        ("GREW", grew, "a pinned asset took on further findings of the same rule, and the set may "
                       "not grow -- not by an identity and not by a count"),
        ("SHRANK", shrank, "a pinned asset carries fewer findings than the pin records. Real "
                           "progress, and it must be declared: re-run with --write"),
    ):
        if not keys:
            continue
        print(f"\n{len(keys)} pin(s) {label}:", file=sys.stderr)
        for rule, where in keys[:20]:
            print(f"  [{rule}] {where}  {pinned[(rule, where)]} -> {found[(rule, where)]}",
                  file=sys.stderr)
        if len(keys) > 20:
            print(f"  … {len(keys) - 20} more", file=sys.stderr)
        print(f"  {why}", file=sys.stderr)

    if new or closed or grew or shrank:
        return 1
    print("  the ratchet holds: the finding set is exactly what is pinned")
    return 0


def _pins(path: Path) -> dict[tuple[str, str], int | None]:
    """The pin file as ``(rule, where) -> count``, where ``None`` means no count was recorded.

    Two fields is the form written before pins carried counts. It is read rather than rejected --
    rejecting it would mean this tool could not run against a pin file already committed in a corpus
    repository -- and ``main`` prints how many pins are in that state, because a gate that silently
    checks less than it claims is the defect this whole file exists to avoid.
    """
    out: dict[tuple[str, str], int | None] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("	")
        if len(fields) < 2 or not fields[1].strip():
            continue
        rule, where = fields[0].strip(), fields[1].strip()
        count: int | None = None
        if len(fields) >= 3 and fields[2].strip().isdigit():
            count = int(fields[2].strip())
        out[(rule, where)] = count
    return out


if __name__ == "__main__":
    raise SystemExit(main())
