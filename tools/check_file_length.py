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

#: Reported **separately from the soft cap, and never fatal**: a file this close to
#: :data:`HARD_LIMIT` is one ordinary edit away from a build that fails, and the failure will not
#: say "start a new file" -- so whoever hits it fights the cap instead of splitting.
#:
#: It is its own tier because the soft cap cannot carry this signal. On 2026-08-19 the soft list
#: named **81** files; four of them were within 41-75 lines of fatal and were indistinguishable
#: from the other 77. Both of that week's rounds of near-cap files arrived the same way -- an
#: upstream merge grew files nobody was watching -- which is exactly the case a threshold you
#: only cross once, silently, cannot catch.
#:
#: Not fatal, deliberately. ADR 0005 §6 defines the tiers, so making 900 a build failure is an
#: ADR-level decision rather than a tooling one, and a gate that starts failing on work already
#: in flight teaches people to bypass it. This tier's job is to be *read*.
WARN_LIMIT = 900

#: Roots scanned. ``scripts`` was a fourth root until 2026-08-11, holding a one-shot
#: corpus rebuild kit — listed here because leaving it out would have made "move it to
#: scripts/" a way to leave the checks. That kit is gone; the escape is closed by there
#: being no second root to move to rather than by this tuple remembering one.
ROOTS: tuple[str, ...] = ("src", "tools", "tests")

#: Directory names skipped wherever they appear: generated or vendored trees are not code
#: anyone reads, and ``__pycache__`` holds no ``.py`` files but costs a walk. ``_build`` is
#: generated staging (Sphinx, extract dumps) — not a length anyone should act on.
SKIP_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".venv", "venv", "node_modules", "_build"}
)

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
    warn = sorted((c for c in counted if WARN_LIMIT < c[0] <= HARD_LIMIT), reverse=True)
    soft = sorted((c for c in counted if SOFT_LIMIT < c[0] <= WARN_LIMIT), reverse=True)

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
    if warn:
        print(f"\napproaching the hard cap of {HARD_LIMIT} — split these before adding to them:")
        for n, rel in warn:
            print(f"  {rel}: {n} lines, {HARD_LIMIT - n} left")
        print("  (not fatal. The next edit to any of these is.)")

    print(f"\nover the soft cap of {SOFT_LIMIT}: {len(soft)} file(s)"
          + (":" if soft else " — none"))
    for n, rel in soft:
        print(f"  {rel}: {n} lines (soft {SOFT_LIMIT}, not fatal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
