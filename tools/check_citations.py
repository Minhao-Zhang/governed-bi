"""Fail when a retired measurement reappears in live code or docs.

Patterns come from ``register.citations.RETIRED_CLAIMS``; exemptions from
``GREP_EXEMPT_PATHS`` in the same module. Reads that module with ``ast`` rather
than importing it, so the check runs in a bare environment.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CITATIONS = ROOT / "src" / "governed_bi" / "register" / "citations.py"

#: Roots where a hit is fatal: live code, tools, and live documentation.
STRICT_ROOTS: tuple[str, ...] = ("src", "tools", "docs")

#: Archive roots (scanned, counted, never fatal). Empty: historical markdown
#: was deleted from the working tree.
ARCHIVE_ROOTS: tuple[str, ...] = ()

SEARCH_SUFFIXES: frozenset[str] = frozenset({".py", ".md", ".toml", ".json"})

#: Inline marker that exempts one line (quoting a retired number on purpose).
LINE_MARKER = "[retired]"


def _literal(node: ast.expr) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def load_declarations() -> tuple[list[tuple[str, str]], set[str]]:
    """Extract ``(pattern, why)`` pairs and exempt paths without importing."""
    tree = ast.parse(CITATIONS.read_text(encoding="utf-8"), filename=str(CITATIONS))
    patterns: list[tuple[str, str]] = []
    exempt: set[str] = set()

    for node in ast.walk(tree):
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
    if ARCHIVE_ROOTS:
        print(
            f"archive: {len(archived)} retired claim(s) across {archive_files} file(s) in "
            f"{', '.join(ARCHIVE_ROOTS)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
