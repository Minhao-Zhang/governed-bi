"""Fail when a comment or docstring keeps more than the decision.

A run of ``#`` lines is at most three. A function or class docstring is at
most five lines, a module docstring at most ten. Module docstrings under
``tools/`` are the command help and are exempt. Comments and docstrings may
not carry history words or dates; other string literals may.

Prints ``path:line: rule: detail`` and ``comment-budget: N``, and exits 1
when N is not zero. ``--count`` prints ``rule package count`` instead and
exits 0.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PHRASES = (
    re.compile(r"\bused to\b", re.I),
    re.compile(r"\bno longer\b", re.I),
    re.compile(r"\bpreviously\b", re.I),
    re.compile(r"\bwas changed\b", re.I),
)
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
SCOPES = ("src/governed_bi/", "tests/", "tools/")
DEFINITIONS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
Row = tuple[str, int, str, str]


def candidates(root: Path) -> list[Path]:
    # Git-listed files only: ignored local files are not part of the tree.
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
    )
    if listed.returncode != 0:
        return list(root.rglob("*"))
    return [root / name for name in listed.stdout.decode("utf-8").split("\0") if name]


def history_hits(text: str) -> list[str]:
    found = [match.group(0).lower() for pattern in PHRASES for match in pattern.finditer(text)]
    found.extend(match.group(0) for match in DATE.finditer(text))
    return found


def leading_doc(node: ast.AST) -> ast.expr | None:
    body = getattr(node, "body", None)
    if not isinstance(body, list) or not body:
        return None
    first = body[0]
    if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
        return None
    if isinstance(first.value.value, str):
        return first.value
    return None


def comment_runs(lines: list[int]) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    length = 0
    for lineno in lines:
        if previous is not None and lineno == previous + 1:
            length += 1
        else:
            if length > 3 and start is not None:
                found.append((start, length))
            start = lineno
            length = 1
        previous = lineno
    if length > 3 and start is not None:
        found.append((start, length))
    return found


def scan(text: str, rel: str) -> list[tuple[int, str, str]]:
    try:
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, tokenize.TokenError) as err:
        line = getattr(err, "lineno", None) or 1
        return [(line, "parse", str(err).splitlines()[0])]
    lines = text.splitlines()
    docs: set[int] = set()
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, DEFINITIONS):
            continue
        doc = leading_doc(node)
        if doc is None or doc.lineno is None:
            continue
        start = doc.lineno
        end = doc.end_lineno or start
        docs.update(range(start, end + 1))
        if isinstance(node, ast.Module):
            kind = "module"
        elif isinstance(node, ast.ClassDef):
            kind = "class"
        else:
            kind = "function"
        # tools/ module docstrings are the command help, so the ten-line cap does not apply.
        if kind == "module" and rel.startswith("tools/"):
            continue
        length = end - start + 1
        limit = 10 if kind == "module" else 5
        if length > limit:
            found.append((start, "docstring", f"{kind} {length} lines"))
    comment_lines: list[int] = []
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            lineno = tok.start[0]
            source = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            if lineno == 1 and source.startswith("#!"):
                continue
            for hit in history_hits(tok.string):
                found.append((lineno, "history", hit))
            if source.lstrip().startswith("#") and (not comment_lines or comment_lines[-1] != lineno):
                comment_lines.append(lineno)
        elif tok.type == tokenize.STRING and tok.start[0] in docs:
            for offset, line in enumerate(tok.string.splitlines()):
                for hit in history_hits(line):
                    found.append((tok.start[0] + offset, "history", hit))
    for start, length in comment_runs(comment_lines):
        found.append((start, "comment-run", f"{length} lines"))
    return found


def package_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "tools":
        return "tools"
    if parts[0] == "src":
        return "/".join(parts[:3]) if len(parts) > 3 else "src/governed_bi"
    if parts[0] == "tests" and len(parts) > 2:
        return f"tests/{parts[1]}"
    return "tests"


def normalise(paths: list[str]) -> list[str]:
    keys: list[str] = []
    for raw in paths:
        key = raw.replace("\\", "/").strip("/")
        keys.append(key[2:] if key.startswith("./") else key)
    return keys


def selected(rel: str, limits: list[str], seen: dict[str, bool]) -> bool:
    if not limits:
        return True
    hit = False
    for key in limits:
        if rel == key or rel.startswith(key + "/"):
            seen[key] = True
            hit = True
    return hit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--count", action="store_true", help="print counts per rule and package")
    parser.add_argument("paths", nargs="*", help="limit the scan to these files or directories")
    args = parser.parse_args(argv)
    root = args.root
    limits = normalise(args.paths)
    seen = dict.fromkeys(limits, False)
    rows: list[Row] = []
    for path in candidates(root):
        if not path.is_file() or path.suffix != ".py":
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not rel.startswith(SCOPES) or not selected(rel, limits, seen):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as err:
            rows.append((rel, 1, "parse", str(err).splitlines()[0]))
            continue
        for line, rule, detail in scan(text, rel):
            rows.append((rel, line, rule, detail))
    missing = [key for key, hit in seen.items() if not hit]
    if missing:
        for key in missing:
            print(f"comment-budget: no files under {key}", file=sys.stderr)
        return 2
    if args.count:
        totals: dict[tuple[str, str], int] = {}
        for rel, _line, rule, _detail in rows:
            bucket = (rule, package_of(rel))
            totals[bucket] = totals.get(bucket, 0) + 1
        ordered = sorted(totals.items(), key=lambda item: (item[0][1], item[0][0]))
        for (rule, package), count in ordered:
            print(f"{rule} {package} {count}")
        return 0
    for rel, line, rule, detail in sorted(rows):
        print(f"{rel}:{line}: {rule}: {detail}")
    print(f"comment-budget: {len(rows)}")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
