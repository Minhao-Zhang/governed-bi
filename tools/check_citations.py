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

#: Roots where a hit is **fatal**: live code, and the tools that check it.
STRICT_ROOTS: tuple[str, ...] = ("src", "tools")

#: Roots where a hit is **reported but not fatal**, for now.
#:
#: ``docs/`` currently holds 16 hits across 12 files, and they are not all the same
#: kind of problem. An experiment record that says "we measured 0.35" is a true
#: statement about what was measured on that date — the record should stay and be
#: annotated, not edited. A *plan* document that reasons *from* 0.35 toward a design
#: decision is stale in a way annotation cannot fix, and most of those plans
#: describe code that no longer exists.
#:
#: Sorting one from the other is a documentation pass, not a lint fix, so it is a
#: tracked item in ``docs/plans/v2-implementation-decisions.md`` rather than
#: something to paper over with 16 inline markers. **Reported on every run so the
#: number cannot quietly grow**, and promoted to fatal by ``--strict-docs`` once the
#: pass is done.
ADVISORY_ROOTS: tuple[str, ...] = ("docs",)

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

    strict_docs = "--strict-docs" in sys.argv

    def scan(root_name: str) -> tuple[list[str], int]:
        found: list[str] = []
        n = 0
        root = ROOT / root_name
        if not root.exists():
            return found, n
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in SEARCH_SUFFIXES:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel in exempt:
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
    advisory: list[str] = []
    scanned = 0
    for name in STRICT_ROOTS:
        found, n = scan(name)
        fatal.extend(found)
        scanned += n
    for name in ADVISORY_ROOTS:
        found, n = scan(name)
        (fatal if strict_docs else advisory).extend(found)
        scanned += n

    if advisory:
        print(f"{len(advisory)} retired claim(s) in documentation (advisory):\n")
        for h in advisory:
            print(f"  {h}")
        print(
            "\nThese are v1 records and v1 plans. An experiment record stating what "
            "was measured on a date is true and should be annotated, not edited; a "
            "plan reasoning from a falsified figure is stale in a way annotation "
            "cannot fix. Sorting them is tracked in "
            "docs/plans/v2-implementation-decisions.md. Run with --strict-docs to "
            "make these fatal.\n"
        )

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
        f"{len(compiled)} pattern(s), {len(exempt)} exempt path(s), "
        f"{len(advisory)} advisory"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
