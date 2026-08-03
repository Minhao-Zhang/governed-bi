"""Enforce ADR 0005 §6's file-length tiers: soft 400, hard 800.

v1's shape: **17 files over 1,000 lines, one at 5,085, and 30% of all code in
files over 1,000 lines** (``register.citations`` ``v1 was 86,746 lines``, measured
2026-08-02). ADR 0005 §6 declared the limit "CI-enforced" and nothing enforced it,
which is the same class of defect as the caller contract in ``check_imports.py``:
a number in a table that no process reads is a preference, not a limit.

Two tiers, and the soft one is the one that needs defending:

* **hard 800 — fatal.** Nothing at this size is reviewable, and every one of v1's
  worst files passed through 800 on the way to 1,000.
* **soft 400 — reported, never fatal, and the count prints on every run.** A soft
  cap that prints nothing when it is exceeded is indistinguishable from a soft cap
  nobody wired up — the same argument the archive tier in ``check_citations.py``
  earns its printing from. Publishing the count is what makes the *set* of
  overruns visible, so growth from one to five is something a reviewer sees rather
  than something they would have to go looking for.

There is one overrun today — ``register/record.py`` — and it is a recorded,
accepted decision, not an oversight. It is **not** special-cased here: the soft
tier reporting it is the correct behaviour, and an exemption would delete the only
signal that says how many overruns exist. A hard-cap exemption list is
deliberately absent too; if a file ever needs one, that is a design conversation,
not a constant.

Lines are counted physically, including blanks and comments, because that is what
a reader scrolls through. Docstrings are the bulk of this repo's line count by
design, and they are still lines a reader must hold in their head.

Exit code 1 only on a hard-cap violation.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Reported, never fatal. The tier exists so an overrun is a visible decision.
SOFT_LIMIT = 400

#: Fatal. ADR 0005 §6.
HARD_LIMIT = 800

#: Roots scanned. ``tools/`` and ``tests/`` are included on purpose — v1's largest
#: single file was a test module, and the review cost of an unreadable test is the
#: same as the review cost of an unreadable implementation.
ROOTS: tuple[str, ...] = ("src", "tools", "tests")

#: Directory names skipped wherever they appear. Generated or vendored trees are
#: not code anyone reads, and ``__pycache__`` holds no ``.py`` files but costs a
#: walk.
SKIP_DIRS: frozenset[str] = frozenset({"__pycache__", ".venv", "venv", "node_modules"})

ROOT = Path(__file__).resolve().parent.parent


def _files() -> list[Path]:
    out: list[Path] = []
    for name in ROOTS:
        root = ROOT / name
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
    files = _files()
    if not files:
        print(f"no Python files under {', '.join(ROOTS)} — refusing to pass vacuously",
              file=sys.stderr)
        return 1

    counted = [(measure(p), p.relative_to(ROOT).as_posix()) for p in files]
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
