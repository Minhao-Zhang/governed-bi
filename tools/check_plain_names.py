"""Fail when a coined name is back in a live doc or in code.

Reads ``docs/plain-names.toml``. Fails on a listed word in Markdown, and in
Python or TypeScript comments, docstrings, and identifiers. String literals
that are not docstrings are skipped: prompt text and snapshot row ids stay.
Guidance words are counted and printed, and do not fail the run.

Skips ``runs/``, accepted ADRs, review records, the glossary mapping note,
dependency and build trees, lockfiles, and generated output.

``paired test`` stays: that is the statistical name.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    "runs",
    "adr",
    "reviews",
    "node_modules",
    ".venv",
    "site-packages",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "coverage",
}
SKIP_NAMES = {"uv.lock", "package-lock.json", "pnpm-lock.yaml"}
SUFFIXES = {".py", ".pyi", ".md", ".ts", ".tsx"}
CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
URL = re.compile(r"\]\(([^)]*)\)")
THIRD_PARTY = ("site-packages", "node_modules", ".venv")


def load() -> tuple[set[str], list[str], set[str], list[tuple[re.Pattern[str] | None, str | None, str]]]:
    spec = tomllib.loads((REPO / "docs" / "plain-names.toml").read_text(encoding="utf-8"))
    fail = set(spec["plain"]) | set(spec["banned"])
    phrases = list(spec["phrases"])
    guidance = set(spec["guidance"])
    allow = [
        (re.compile(row["pattern"]) if row.get("pattern") else None, row.get("path"), row["reason"])
        for row in spec["allow"]
    ]
    return fail, phrases, guidance, allow


def skipped(path: Path) -> bool:
    if path.name in SKIP_NAMES or path.suffix == ".lock":
        return True
    return any(part in SKIP_PARTS or part.startswith(".next") for part in path.parts)


def components(raw: str) -> list[str]:
    if "_" in raw:
        return [part for part in raw.split("_") if part]
    found = CAMEL.findall(raw)
    return found or [raw]


def words_in(
    text: str,
    fail: set[str],
    phrases: list[str],
    allow: list[tuple[re.Pattern[str] | None, str | None, str]],
) -> list[str]:
    hits: list[str] = []
    lower = text.lower()
    for phrase in phrases:
        if phrase in lower:
            hits.append(phrase)
    for match in WORD.finditer(text):
        raw = match.group(0)
        if any(pattern.search(raw) for pattern, path, _reason in allow if pattern and not path):
            continue
        parts = components(raw)
        for index, part in enumerate(parts):
            low = part.lower()
            if low == "paired" and index + 1 < len(parts) and parts[index + 1].lower() == "test":
                continue
            if low == "paired" and re.match(r"\s+test\b", text[match.end() :]):
                continue
            if low in fail:
                hits.append(low)
    return hits


def count_guidance(text: str, guidance: set[str], counts: dict[str, int]) -> None:
    for match in WORD.finditer(text):
        for part in components(match.group(0)):
            low = part.lower()
            if low in guidance:
                counts[low] = counts.get(low, 0) + 1


def blank_mapping(text: str) -> str:
    lines: list[str] = []
    skip = False
    for line in text.splitlines():
        if "<!-- /plain-names-mapping -->" in line:
            skip = False
            lines.append("")
            continue
        if "<!-- plain-names-mapping -->" in line:
            skip = True
            lines.append("")
            continue
        lines.append("" if skip else line)
    return "\n".join(lines)


def doc_lines(tree: ast.AST) -> set[int]:
    found: set[int] = set()

    def take(node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                end = first.end_lineno or first.lineno
                found.update(range(first.lineno, end + 1))

    take(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            take(node)
    return found


def scan_python(
    text: str,
    fail: set[str],
    phrases: list[str],
    allow: list[tuple[re.Pattern[str], str]],
    guidance: set[str],
    counts: dict[str, int],
) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    docs = doc_lines(ast.parse(text))
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    for tok in tokens:
        piece = None
        if tok.type == tokenize.COMMENT:
            piece = tok.string
        elif tok.type == tokenize.NAME:
            piece = tok.string
        elif tok.type == tokenize.STRING and tok.start[0] in docs:
            piece = tok.string
        if piece is None:
            continue
        count_guidance(piece, guidance, counts)
        for word in words_in(piece, fail, phrases, allow):
            hits.append((tok.start[0], word))
    return hits


def scan_ts(
    text: str,
    fail: set[str],
    phrases: list[str],
    allow: list[tuple[re.Pattern[str], str]],
    guidance: set[str],
    counts: dict[str, int],
) -> list[tuple[int, str]]:
    """Comments and identifiers. Strings, including template text, are copied over."""
    hits: list[tuple[int, str]] = []
    i = 0
    n = len(text)
    line = 1

    def take(piece: str, at: int) -> None:
        count_guidance(piece, guidance, counts)
        for word in words_in(piece, fail, phrases, allow):
            hits.append((at, word))

    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            take(text[i:j], line)
            line += text[i:j].count("\n")
            i = j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            take(text[i:j], line)
            line += text[i:j].count("\n")
            i = j
            continue
        ch = text[i]
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            line += text[i:j].count("\n")
            i = j
            continue
        if ch == "`":
            j = i + 1
            while j < n and text[j] != "`":
                if text.startswith("${", j):
                    j += 2
                    depth = 1
                    while j < n and depth:
                        if text[j] in "\"'":
                            q = text[j]
                            j += 1
                            while j < n and text[j] != q:
                                j += 2 if text[j] == "\\" else 1
                            j = min(j + 1, n)
                            continue
                        if text[j] == "{":
                            depth += 1
                        elif text[j] == "}":
                            depth -= 1
                        j += 1
                    continue
                j += 1
            j = min(j + 1, n)
            line += text[i:j].count("\n")
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            take(text[i:j], line)
            i = j
            continue
        if ch == "\n":
            line += 1
        i += 1
    return hits


def scan_md(
    text: str,
    fail: set[str],
    phrases: list[str],
    allow: list[tuple[re.Pattern[str], str]],
    guidance: set[str],
    counts: dict[str, int],
) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(blank_mapping(text).splitlines(), start=1):
        masked = URL.sub(lambda m: "](" + (" " * len(m.group(1))) + ")", line)
        count_guidance(masked, guidance, counts)
        for word in words_in(masked, fail, phrases, allow):
            hits.append((lineno, word))
    return hits


def files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or skipped(path) or path.suffix not in SUFFIXES:
            continue
        found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO)
    root = parser.parse_args(argv).root
    fail, phrases, guidance, allow = load()
    counts: dict[str, int] = {}
    rows: list[tuple[Path, int, str]] = []
    third = 0
    for path in files(root):
        text = path.read_text(encoding="utf-8")
        if path.suffix in {".py", ".pyi"}:
            found = scan_python(text, fail, phrases, allow, guidance, counts)
        elif path.suffix == ".md":
            found = scan_md(text, fail, phrases, allow, guidance, counts)
        else:
            found = scan_ts(text, fail, phrases, allow, guidance, counts)
        rel = path.relative_to(root)
        if any(row_path == rel.as_posix() and pattern is None for pattern, row_path, _reason in allow):
            continue
        if any(part in rel.parts for part in THIRD_PARTY):
            third += len({line for line, _word in found})
        for line, word in found:
            rows.append((rel, line, word))
    if third > 20:
        print(f"plain-names: {third} lines of third-party or generated code, term list needs narrowing")
        return 2
    for rel, line, word in sorted(rows, key=lambda item: (str(item[0]), item[1], item[2])):
        print(f"{rel.as_posix()}:{line}: {word}")
    print(f"plain-names: {len(rows)}")
    print("guidance:")
    for word, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {word} {count}")
    print("allow:")
    for pattern, row_path, reason in allow:
        label = row_path or (pattern.pattern if pattern else "")
        print(f"  {label} — {reason}")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
