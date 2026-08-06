"""Enforce import layering declared in ``governed_bi/__init__.py``.

AST-only (never imports the package). ``ports``/``register`` are stdlib-only;
nothing imports upward. Exit 1 on violation.
"""


from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Layer order, innermost first. A module may import its own layer and any layer
#: **before** it. Anything else is an upward import.
#:
#: Declared as one list so the order is stated once. Adding a package means adding
#: it here, which is the point at which someone has to think about where it sits.
LAYERS: tuple[tuple[str, ...], ...] = (
    ("ports",),
    ("register",),
    ("measure",),
    ("corpus",),
    ("retrieve",),
    ("govern",),
    ("datasource",),
    ("model",),
    ("serve",),
    ("record",),
    ("eval",),
    ("api",),
)

#: Packages required to import in a bare interpreter: stdlib only, no third party.
STDLIB_ONLY: frozenset[str] = frozenset({"ports", "register"})

#: Third-party roots that must never appear in a ``STDLIB_ONLY`` module. Not an
#: exhaustive list of third-party packages — an allowlist of *stdlib* would be a
#: maintenance burden that drifts with every Python release. These are the ones the
#: project actually depends on, so these are the ones that can leak.
FORBIDDEN_IN_STDLIB_ONLY: frozenset[str] = frozenset({
    "pydantic", "sqlglot", "networkx", "yaml", "numpy",
    "langchain", "langchain_core", "langchain_openai", "langgraph", "deepagents",
    "openai", "anthropic", "boto3", "botocore", "psycopg", "psycopg2",
    "fastapi", "starlette", "uvicorn", "httpx", "langsmith",
})

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "governed_bi"


def _layer_index(package: str) -> int | None:
    for i, names in enumerate(LAYERS):
        if package in names:
            return i
    return None


def _own_package(path: Path) -> str:
    """Which declared package a file belongs to. ``""`` for the root modules."""
    rel = path.relative_to(PKG)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Every imported module name, with its line. Relative imports resolved to a
    bare package name so ``from .assets import X`` reads as ``assets``."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module:
                out.append((node.lineno, node.module))
            elif node.level:
                out.append((node.lineno, ""))  # `from . import x`
            elif node.module:
                out.append((node.lineno, node.module))
    return out


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    rel = path.relative_to(ROOT).as_posix()
    own = _own_package(path)
    own_idx = _layer_index(own)

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as err:
        return [f"{rel}:{err.lineno}: does not parse: {err.msg}"]

    for lineno, name in _imports(tree):
        root = name.split(".")[0]

        if own in STDLIB_ONLY and root in FORBIDDEN_IN_STDLIB_ONLY:
            problems.append(
                f"{rel}:{lineno}: {own}/ is stdlib-only and imports {root!r}. "
                "Both the serve path and the eval harness import it, so a "
                "third-party dependency here makes it un-importable from one side."
            )

        # Only same-package (`from .x`) and governed_bi imports carry layer meaning.
        target: str | None = None
        if name.startswith("governed_bi."):
            parts = name.split(".")
            target = parts[1] if len(parts) > 1 else None
        elif own_idx is not None and _layer_index(root) is not None:
            target = root

        if target is None or target == own:
            continue
        target_idx = _layer_index(target)
        if target_idx is None or own_idx is None:
            continue
        if target_idx > own_idx:
            problems.append(
                f"{rel}:{lineno}: {own}/ imports {target}/, which is a later layer. "
                f"Declared order: {' -> '.join(n[0] for n in LAYERS)}"
            )
    return problems


def main() -> int:
    if not PKG.exists():
        print(f"no package at {PKG}", file=sys.stderr)
        return 1
    files = sorted(PKG.rglob("*.py"))
    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path))

    if problems:
        print(f"{len(problems)} layering violation(s):\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"import layering OK across {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
