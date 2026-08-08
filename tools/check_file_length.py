"""Enforce ADR 0005 §6 file-length tiers: soft 400 (reported), hard 1000 (fatal).

Physical lines including blanks and comments. Scans ``src``, ``tools``, ``tests``.
Exit 1 only on hard-cap violation.
"""


from __future__ import annotations

import sys
from pathlib import Path

#: Reported, never fatal.
SOFT_LIMIT = 400

#: Fatal. ADR 0005 §6.
HARD_LIMIT = 1000

#: Roots scanned (src, tools, tests).
ROOTS: tuple[str, ...] = ("src", "tools", "tests")

#: Directory names skipped wherever they appear: generated or vendored trees are not code
#: anyone reads, and ``__pycache__`` holds no ``.py`` files but costs a walk.
SKIP_DIRS: frozenset[str] = frozenset({"__pycache__", ".venv", "venv", "node_modules"})

ROOT = Path(__file__).resolve().parent.parent


def _files(base: Path = ROOT) -> list[Path]:
    out: list[Path] = []
    for name in ROOTS:
        root = base / name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if SKIP_DIRS & set(path.parts):
                continue
            out.append(path)
    return out


def measure(path: Path) -> int:
    """Physical line count. ``splitlines`` so a missing trailing newline does not
    change the answer by one."""
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def main() -> int:
    # ``--root DIR`` measures a tree the caller owns, so a negative test never writes an
    # over-length probe into ``src/`` (see ``check_one_implementation.py``).
    argv = sys.argv[1:]
    base = ROOT
    if "--root" in argv:
        base = Path(argv[argv.index("--root") + 1]).resolve()

    files = _files(base)
    if not files:
        print(f"no Python files under {', '.join(ROOTS)} — refusing to pass vacuously",
              file=sys.stderr)
        return 1

    counted = [(measure(p), p.relative_to(base).as_posix()) for p in files]
    hard = sorted((c for c in counted if c[0] > HARD_LIMIT), reverse=True)
    soft = sorted((c for c in counted if SOFT_LIMIT < c[0] <= HARD_LIMIT), reverse=True)

    if hard:
        print(f"{len(hard)} file(s) over the hard cap of {HARD_LIMIT} lines:\n",
              file=sys.stderr)
        for n, rel in hard:
            print(f"  {rel}:{n}: {n} lines, hard cap {HARD_LIMIT} (ADR 0005 §6)",
                  file=sys.stderr)
        print(
            "\nSplit it. v1 reached 17 files over 1,000 lines and 30% of its code "
            "lived in them; every one of those passed through this cap first.",
            file=sys.stderr,
        )
        return 1

    total = sum(n for n, _ in counted)
    print(
        f"file length OK across {len(files)} file(s), {total} lines; "
        f"hard cap {HARD_LIMIT}, soft cap {SOFT_LIMIT}"
    )
    print(f"over the soft cap: {len(soft)} file(s)"
          + (":" if soft else " — none"))
    for n, rel in soft:
        print(f"  {rel}: {n} lines (soft {SOFT_LIMIT}, not fatal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
