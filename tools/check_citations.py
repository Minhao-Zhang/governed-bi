"""Fail when a retired measurement reappears, anywhere in the repo.

v1's characteristic defect: **a number describing the world, written as a literal,
pinned to nothing.** One falsified routing-recall figure reached five places in
``src/`` including an operator-facing warning, **and a test asserted it** — which is
how a figure wrong by 2.4x survived long enough to become the stated reason for a
design decision. A stale price tuple overstated a run nine-fold. A count of
unwinnable questions was overstated 17x.

And the reason it kept happening: the fix landed where it was found and never
reached the adjacent copies. So this is the cheap mechanical version of pushing it.

The patterns come from ``register.citations.RETIRED_CLAIMS``, and the exemptions
from ``GREP_EXEMPT_PATHS`` in the same module — that file necessarily quotes every
retired claim in order to retire it, and so does the lessons document. **The
exemption is data the checker reads, not a special case inside the checker**, so it
is reviewable.

This script reads that module's source with ``ast`` rather than importing it,
for the same reason as ``check_imports.py``: a structural check should run in a bare
environment and should not execute the code it is checking.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CITATIONS = ROOT / "src" / "governed_bi" / "register" / "citations.py"

#: Roots where a hit is **fatal**: live code, the tools that check it, and the
#: live documentation.
#:
#: ``docs`` graduated to fatal on 2026-08-03. Before that it held 16 hits across 12
#: files, which were not all the same kind of problem — and the fix was structural,
#: not 16 inline markers. 52 v1 documents moved to ``docs/v1/`` (see
#: :data:`ARCHIVE_ROOTS`); the 2 hits left in live docs are genuine *discussions* of
#: a retired figure and carry a per-line marker. A subtree is skipped before its
#: children are scanned, so ``docs/v1`` does not inherit this.
STRICT_ROOTS: tuple[str, ...] = ("src", "tools", "docs")

#: Roots that are **archives**: scanned and counted, never fatal.
#:
#: The distinction that earns this tier: a v1 experiment record stating "we measured
#: 0.35 on this date" is a **true statement**, and the whole point of keeping the
#: archive is that such records stay unedited. Requiring ~14 inline markers in files
#: nobody will edit again would be noise, and editing the records to agree with
#: later measurements would destroy the evidence that the earlier instrumentation
#: was wrong.
#:
#: This is deliberately **not** the same as exempting the paths outright. The count
#: prints on every run, so an archive that starts *growing* is visible — a new file
#: appearing under ``docs/v1/`` is somebody adding to history, which is worth
#: noticing. ``GREP_EXEMPT_PATHS`` remains for the files that must quote every
#: retired figure in order to retire it.
ARCHIVE_ROOTS: tuple[str, ...] = ("docs/v1",)

SEARCH_SUFFIXES: frozenset[str] = frozenset({".py", ".md", ".toml", ".json"})

#: An inline marker that exempts one line.
#:
#: A grep gate cannot tell *quoting* a retired number from *explaining* one, and
#: both are legitimate: a docstring explaining that a figure was published through
#: a rate-limited embedder, and what it re-measured at, is the reason the
#: replacement exists. Exempting whole files would make the gate useless in exactly the files
#: that discuss measurement most; a per-line marker keeps the default strict and
#: makes each exemption visible in review, one line at a time.
LINE_MARKER = "[retired]"


def _literal(node: ast.expr) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def load_declarations() -> tuple[list[tuple[str, str]], set[str]]:
    """Extract ``(pattern, why)`` pairs and the exempt paths, without importing.

    Returns patterns as written, so a broken regex surfaces here rather than being
    silently skipped — an unusable pattern is a gate that catches nothing, which is
    the failure mode this whole file exists to prevent.
    """
    tree = ast.parse(CITATIONS.read_text(encoding="utf-8"), filename=str(CITATIONS))
    patterns: list[tuple[str, str]] = []
    exempt: set[str] = set()

    for node in ast.walk(tree):
        # Both forms, because the declarations carry type annotations and an
        # annotated assignment is an AnnAssign, not an Assign. Handling only
        # Assign found nothing and the checker reported zero patterns — which it
        # then refused to treat as success. That refusal is the only reason this
        # was caught rather than shipping as a permanently green gate.
        if isinstance(node, ast.AnnAssign):
            target, value_node = node.target, node.value
        elif isinstance(node, ast.Assign) and node.targets:
            target, value_node = node.targets[0], node.value
        else:
            continue
        if value_node is None:
            continue
        name = target.id if isinstance(target, ast.Name) else None

        if name == "GREP_EXEMPT_PATHS":
            value = _literal(value_node)
            if isinstance(value, (tuple, list)):
                exempt = {str(v) for v in value}

        if name == "RETIRED_CLAIMS" and isinstance(value_node, ast.Tuple):
            for element in value_node.elts:
                if not isinstance(element, ast.Call):
                    continue
                kwargs = {kw.arg: _literal(kw.value) for kw in element.keywords if kw.arg}
                pattern, why = kwargs.get("pattern"), kwargs.get("why")
                if isinstance(pattern, str):
                    patterns.append((pattern, str(why or "")))

    return patterns, exempt


def main() -> int:
    if not CITATIONS.exists():
        print(f"no citations module at {CITATIONS}", file=sys.stderr)
        return 1

    patterns, exempt = load_declarations()
    if not patterns:
        print("no retired claims declared — refusing to pass vacuously", file=sys.stderr)
        return 1

    compiled: list[tuple[re.Pattern[str], str]] = []
    for pattern, why in patterns:
        try:
            compiled.append((re.compile(pattern), why))
        except re.error as err:
            print(f"unusable pattern {pattern!r}: {err}", file=sys.stderr)
            return 1

    def scan(root_name: str, skip_subtrees: tuple[str, ...] = ()) -> tuple[list[str], int]:
        """Hits under ``root_name``, and how many files were read.

        ``skip_subtrees`` is checked **before** a file is counted, so scanning
        ``docs`` does not silently include ``docs/v1``. Without that, an archive
        nested inside a strict root would be scanned twice and judged by the
        stricter of the two tiers — the archive tier would exist and do nothing.
        """
        found: list[str] = []
        n = 0
        root = ROOT / root_name
        if not root.exists():
            return found, n
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in SEARCH_SUFFIXES:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel in exempt or any(rel.startswith(s + "/") for s in skip_subtrees):
                continue
            n += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if LINE_MARKER in line:
                    continue
                for rx, why in compiled:
                    if rx.search(line):
                        found.append(f"{rel}:{lineno}: {why[:100]}")
        return found, n

    fatal: list[str] = []
    archived: list[str] = []
    scanned = 0
    archive_files = 0
    for name in STRICT_ROOTS:
        found, n = scan(name, skip_subtrees=ARCHIVE_ROOTS)
        fatal.extend(found)
        scanned += n
    for name in ARCHIVE_ROOTS:
        found, n = scan(name)
        archived.extend(found)
        archive_files += n

    if fatal:
        print(f"{len(fatal)} retired claim(s) in live code:\n", file=sys.stderr)
        for h in fatal:
            print(f"  {h}", file=sys.stderr)
        print(
            "\nThese numbers were measured wrong; replacements are in "
            f"register/citations.py. To *discuss* one, put {LINE_MARKER!r} on the "
            "same line — the marker must sit on the line the pattern matches, not "
            "the line after it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"no retired claims in live code across {scanned} file(s) scanned; "
        f"{len(compiled)} pattern(s), {len(exempt)} exempt path(s)"
    )
    print(
        f"archive: {len(archived)} retired claim(s) across {archive_files} file(s) in "
        f"{', '.join(ARCHIVE_ROOTS)} — expected, and left unedited on purpose"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
