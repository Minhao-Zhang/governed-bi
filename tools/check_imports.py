"""Enforce import layering declared in ``governed_bi/__init__.py``.

AST-only (never imports the package). ``paths``/``ports``/``register`` are stdlib-only;
nothing imports upward. Exit 1 on violation.

``LAYERS`` must name every package under ``src/governed_bi`` and nothing else, and
:func:`undeclared` fails the run when it does not. A file's constraints come from its
package's position, so a package the list omits has none at all — the run still exits 0
while checking nothing, which is how ``verify/`` came to sit outside the layering.
"""


from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Layer order, innermost first. A module may import its own layer and any layer **before** it;
#: anything else is an upward import. One list, so adding a package forces a decision here.
LAYERS: tuple[tuple[str, ...], ...] = (
    # A top-level module, not a package: `paths.py` says where the repository is and every
    # layer may ask, so it is innermost.
    ("paths",),
    # Also a top-level module. Innermost-but-one because it depends on `paths` and nothing
    # else, but its real constraint is not expressible here: layering answers "what may this
    # import", and the rule that matters is "who may import *this*" -- entry points only.
    # `tests/conformance/test_only_entry_points_read_the_environment.py` holds that half.
    ("credentials",),
    ("ports",),
    ("register",),
    ("measure",),
    ("corpus",),
    ("retrieve",),
    ("govern",),
    ("datasource",),
    ("model",),
    ("serve",),
    ("eval",),
    ("api",),
)

#: Top-level names exempt from :func:`undeclared`. ``__init__`` is the package's own
#: docstring and imports nothing, so a layer for it would order it against itself.
UNLAYERED: frozenset[str] = frozenset({"__init__"})

#: Packages required to import in a bare interpreter: stdlib only, no third party.
STDLIB_ONLY: frozenset[str] = frozenset({"paths", "credentials", "ports", "register"})

#: Third-party roots that must never appear in a ``STDLIB_ONLY`` module. A denylist of this
#: project's own dependencies, not an exhaustive one: the inverse — an allowlist of stdlib —
#: would drift with every Python release.
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


def _own_package(path: Path, pkg: Path = PKG) -> str:
    """Which declared package a file belongs to. ``""`` for the root modules."""
    rel = path.relative_to(pkg)
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


def check_file(path: Path, pkg: Path = PKG) -> list[str]:
    problems: list[str] = []
    rel = path.relative_to(pkg.parent.parent).as_posix()
    own = _own_package(path, pkg)
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


def undeclared(pkg: Path = PKG) -> list[str]:
    """``LAYERS`` against what is on disk, in both directions.

    Omission is the silent failure: an undeclared package is checked against nothing and the
    run still passes. A declared name with no package is the same rot from the other side —
    an ordering claim about something nobody can be in, which is what ``record`` was.
    """
    declared = {name for names in LAYERS for name in names}
    problems: list[str] = []

    # Keyed the way :func:`_own_package` keys a file: a package by its directory name, a
    # top-level module by its stem.
    on_disk = {
        path.stem
        for path in pkg.iterdir()
        if (path.is_dir() and path.name != "__pycache__") or path.suffix == ".py"
    }
    for name in sorted(on_disk - declared - UNLAYERED):
        problems.append(
            f"src/governed_bi/{name}: on disk and absent from LAYERS, so nothing constrains "
            "its imports. Give it a position, add it to UNLAYERED with a reason, or delete it."
        )
    for name in sorted(declared - on_disk):
        problems.append(
            f"LAYERS declares {name!r}, but src/governed_bi/{name} does not exist. A layer "
            "nobody can be in orders nothing; drop the row."
        )
    return problems


def main() -> int:
    # ``--root DIR`` checks a tree the caller owns, so a negative test never writes a probe
    # module into ``src/`` (see ``check_one_implementation.py``).
    argv = sys.argv[1:]
    pkg = PKG
    if "--root" in argv:
        pkg = Path(argv[argv.index("--root") + 1]).resolve() / "src" / "governed_bi"

    if not pkg.exists():
        print(f"no package at {pkg}", file=sys.stderr)
        return 1
    files = sorted(pkg.rglob("*.py"))
    problems: list[str] = undeclared(pkg)
    for path in files:
        problems.extend(check_file(path, pkg))

    if problems:
        print(f"{len(problems)} layering violation(s):\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    n_layers = sum(len(names) for names in LAYERS)
    print(f"import layering OK across {len(files)} file(s) in {n_layers} declared layer(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
