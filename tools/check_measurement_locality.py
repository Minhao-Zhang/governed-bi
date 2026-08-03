"""Number formatting in ``src/`` happens in one method, or not at all.

``register/quantity.py``'s :meth:`Measured.render` is the only place in ``src/``
permitted to turn a quantity into display text. The rule is not stylistic. v1's
rounding helpers turned an **unmeasured** quantity into ``0.0`` on the way to a
report: ``round(x or 0.0, n)`` — the value was honest right up to the last function
that touched it, and by then nothing downstream could tell a measured zero from a
missing measurement. The related incident from the same family:
``docs/lessons-from-v1.md`` — *a ``:.3f`` on a ``None`` rate raised after the whole
serve loop and before ``summary.json`` was written*, discarding hours of paid model
calls to print a progress line. Formatting is where a measurement stops being a
measurement, in both directions.

So there is exactly one formatting site, it takes a ``Measured``, and it renders
absence as words. Anything in ``src/`` that formats a number is either bypassing
that site or duplicating it.

**``tools/`` and ``tests/`` are not scanned, deliberately.** Both format their own
diagnostics — this file prints counts, and a test that asserts ``render()`` produces
``"0.50"`` must write ``"0.50"`` somewhere. Neither writes a number into a report a
reader would quote, which is the thing the rule protects, and extending the rule
there would mean a gate whose only effect is to make its own diagnostics
unwritable.

**Which mechanism catches what, and why.**

AST does the work wherever a construct has a shape:

* ``round(...)`` — an ``ast.Call``. This is the reason not to use a line regex:
  ``register/record.py`` and ``register/quantity.py`` both quote ``round(`` in
  prose, explaining the v1 defect and this rule. A grep fails on both, so the gate
  would either be permanently red or acquire per-line markers on the two files most
  entitled to discuss it. The AST sees calls and not prose.
* f-string precision specs — ``FormattedValue.format_spec``. The spec is
  reconstructed from its literal parts, with a nested placeholder such as
  ``{places}`` rendered as ``{}``, so a computed precision is still matched.
* ``"{:.2f}".format(x)`` — an ``ast.Call`` on an ``Attribute`` named ``format``
  whose receiver is a string literal. Checked on the literal, so it cannot
  false-positive on ``obj.format(...)`` for some unrelated ``format``.
* ``"%.2f" % x`` — a ``BinOp`` with ``Mod`` and a string literal on the left. Only
  fatal when the literal holds a float/exponent/general conversion: ``"%s of %d" %
  ...`` loses no precision and is not what the rule is about.
* ``format(x, ".2f")`` — the two-argument builtin, spec as a literal.

A **regex** is used only for the contents of a matched spec string, because
"is this a numeric precision spec" is a question about characters, not about shape.
No line-level regex is used at all.

**Stated gaps, rather than a noisy check.** A precision spec assembled at runtime
(``spec = "." + str(n) + "f"``), or held in a module constant and ``.format``ed
through a variable later, is not caught: detecting those needs the value of an
expression, and the version that guesses would fire on every docstring in
``quantity.py`` that documents the rule. Also out of scope: ``Decimal.quantize``,
``numpy.round`` on an array, and thousands separators, which are presentation
without precision loss.

Exit code 1 on any violation.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "governed_bi"

#: The one permitted formatting site, relative to ``src/governed_bi/``.
#:
#: **Declared as data the checker reads, not as a branch inside the checker**, for
#: the same reason ``check_citations.py`` keeps its exemptions in the module it
#: checks: an exemption in a conditional is invisible in review, and this one is the
#: single most load-bearing line in the file. Adding a second entry here should feel
#: like what it is — a second answer to "how does a number reach a reader".
EXEMPT: tuple[str, ...] = ("register/quantity.py",)

#: A numeric precision spec: a dot, a width (or a nested ``{}`` placeholder for a
#: computed one), then a presentation type. Inside a format spec a dot only ever
#: introduces precision, so this does not need to know the type letters.
NUMERIC_SPEC = re.compile(r"\.(?:\d+|\{\})[a-zA-Z%]")

#: ``%``-style conversions that format a *float*. ``%d``/``%s`` are excluded: they
#: are not a precision claim, and including them would flag ordinary message
#: building for no measurement-integrity reason.
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

    # ``ast.walk`` is breadth-first, so an f-string nested inside a call reports
    # after a later top-level statement. Sorted numerically, because a reader fixes
    # these top to bottom and a lexicographic sort puts line 10 before line 2.
    return [msg for _, msg in sorted(found)]


def main() -> int:
    if not PKG.exists():
        print(f"no package at {PKG}", file=sys.stderr)
        return 1

    files = [p for p in sorted(PKG.rglob("*.py")) if not SKIP_DIRS & set(p.parts)]
    problems: list[str] = []
    scanned = 0
    for path in files:
        rel = path.relative_to(PKG).as_posix()
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
