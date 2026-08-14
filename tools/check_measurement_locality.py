"""Number formatting in ``src/`` only via ``Measured.render``.

AST catches ``round``, precision format specs, ``.format``, ``%``, and
``format(x, spec)``. ``tools/`` and ``tests/`` are not scanned. Exit 1 on
violation.
"""


from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "governed_bi"

#: The one permitted formatting site, relative to ``src/governed_bi/``.
EXEMPT: tuple[str, ...] = ("register/quantity.py",)

#: A numeric precision spec: a dot, a width (or a nested ``{}`` placeholder for a
#: computed one), then a presentation type. Inside a format spec a dot only ever
#: introduces precision, so this does not need to know the type letters.
NUMERIC_SPEC = re.compile(r"\.(?:\d+|\{\})[a-zA-Z%]")

#: ``%``-style conversions that format a *float*. ``%d``/``%s`` are excluded: they make no
#: precision claim, so flagging them would only catch ordinary message building.
PERCENT_FLOAT = re.compile(r"%[-+ 0#]*\d*(?:\.\d+)?[fFeEgG]")

SKIP_DIRS: frozenset[str] = frozenset({"__pycache__"})


def _spec_text(node: ast.expr | None) -> str | None:
    """The literal text of a format spec, with nested placeholders as ``{}``.

    ``f"{x:.{places}f}"`` reconstructs as ``.{}f`` — matched, because a computed
    precision is still a precision claim. Returns ``None`` when there is no spec.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            else:
                parts.append("{}")
        return "".join(parts)
    return None


def _str_literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def check_file(path: Path, rel: str) -> list[str]:
    found: list[tuple[int, str]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as err:
        return [f"src/governed_bi/{rel}:{err.lineno}: does not parse: {err.msg}"]

    def report(lineno: int, what: str) -> None:
        found.append((
            lineno,
            f"src/governed_bi/{rel}:{lineno}: {what}. Formatting a number in src/ "
            "is permitted only in register/quantity.py's Measured.render(). v1's "
            "rounding helpers turned an unmeasured quantity into 0.0 on the way to "
            "a report (ADR 0005 section 6).",
        ))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "round":
                report(node.lineno, "calls round()")
            elif name == "format" and isinstance(func, ast.Attribute):
                template = _str_literal(func.value)
                if template and NUMERIC_SPEC.search(template):
                    report(node.lineno, f"formats a number via {template!r}.format()")
            elif name == "format" and len(node.args) == 2:
                spec = _str_literal(node.args[1])
                if spec and NUMERIC_SPEC.search(spec):
                    report(node.lineno, f"calls format(x, {spec!r})")

        elif isinstance(node, ast.FormattedValue):
            spec = _spec_text(node.format_spec)
            if spec and NUMERIC_SPEC.search(spec):
                report(node.lineno, f"f-string precision spec {spec!r}")

        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            template = _str_literal(node.left)
            if template and PERCENT_FLOAT.search(template):
                report(node.lineno, f"%-formats a float via {template!r}")

    # ``ast.walk`` is breadth-first, so a nested f-string reports after a later top-level
    # statement. Sorted numerically, since a lexicographic sort puts line 10 before line 2.
    return [msg for _, msg in sorted(found)]


def main() -> int:
    # ``--root DIR`` scans a tree the caller owns, so a negative test never writes a probe
    # into ``src/`` (see ``check_one_implementation.py``).
    argv = sys.argv[1:]
    pkg = PKG
    if "--root" in argv:
        pkg = Path(argv[argv.index("--root") + 1]).resolve() / "src" / "governed_bi"

    if not pkg.exists():
        print(f"no package at {pkg}", file=sys.stderr)
        return 1

    files = [p for p in sorted(pkg.rglob("*.py")) if not SKIP_DIRS & set(p.parts)]
    problems: list[str] = []
    scanned = 0
    for path in files:
        rel = path.relative_to(pkg).as_posix()
        if rel in EXEMPT:
            continue
        scanned += 1
        problems.extend(check_file(path, rel))

    if problems:
        print(f"{len(problems)} formatting site(s) outside register/quantity.py:\n",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(
            "\nTake a Measured and call .render(), or .rounded() if the rounded "
            "quantity travels further. Both carry absence through instead of "
            "defaulting it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"number formatting is local to {', '.join(EXEMPT)} across {scanned} "
        f"file(s) scanned; {len(EXEMPT)} exempt path(s), tools/ and tests/ "
        "not scanned (own diagnostics)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
