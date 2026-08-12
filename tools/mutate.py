"""Mutation testing for the invariants this repository cannot afford to lose.

**Why this exists.** ``AGENTS.md`` requires that a test guarding a defect be *mutation-verified* —
break the behaviour, watch the test fail, restore — and until 2026-08-10 that was a habit rather
than a mechanism. The habit failed: ``tests/govern`` (1,705 lines then, 3,006 today; owner of the
layer stack, carrying ADR 0006's B1–B10 bypass contract) could not detect a **total governance
bypass**.
Setting ``pipeline.py``'s ``if not verdict["passed"]`` to ``if False:`` made ``prepare()`` hand back
``'SELECT token FROM secrets LIMIT 200001'`` for a refused verdict, and 133/133 tests passed.

A habit does not scale and does not survive the person who has it. This does: each entry below is
a mutation someone verified by hand once, written down so it is verified on every run.

**What a run proves.** For each mutation: the anchor still exists (so the entry has not silently
gone stale against a refactor), the mutated tree makes **at least one named test fail**, and the
tree is restored. What it does *not* prove is that the tests are otherwise good — a mutation the
suite catches says nothing about the mutations nobody wrote down.

**Not a ``check_*`` gate.** ``tests/conformance/test_register_closure.py`` requires every
``tools/check_*.py`` to be declared CI or declared manual; this is deliberately not named that
way, because it is slow (it runs a pytest selection per mutation) and belongs on a nightly or a
pre-release run rather than on every push.

The declarations themselves live in ``tools/mutation_catalogue.py``; this file is the runner.

Usage::

    uv run --frozen python tools/mutate.py            # every declared mutation
    uv run --frozen python tools/mutate.py --list     # names only, runs nothing
    uv run --frozen python tools/mutate.py --only c1  # one, by id substring

**Safety.** The target file is read into memory, written, and restored in a ``finally``, and the
restore is verified byte-for-byte before the next mutation runs. It does not use
``git checkout --``: ``AGENTS.md`` records that doing so has silently discarded uncommitted work
in the same file more than once, and this tool must be safe to run on a dirty tree.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
# The catalogue is a sibling script, not a package module: `python tools/mutate.py` puts
# `tools/` on the path, but importing this file by its path does not.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mutation_catalogue import MUTATIONS, Mutation  # noqa: E402  (needs the path insert above)

__all__ = ["MUTATIONS", "Mutation", "main"]


def _run_tests(selection: tuple[str, ...]) -> tuple[bool, str]:
    """``(any test failed, last line of output)``.

    A non-zero exit is the signal, and *any* non-zero counts: a collection error caused by the
    mutation is still the suite noticing. What must not happen is exit 0.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *selection, "-q", "-x", "--no-header", "-p", "no:randomly"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return proc.returncode != 0, (tail[-1] if tail else f"exit {proc.returncode}")


def _apply(mutation: Mutation) -> tuple[bool, str]:
    """Run one mutation. Returns ``(survived, detail)`` — ``survived`` meaning **bad**."""
    target = REPO / mutation.path
    original = target.read_text(encoding="utf-8")

    count = original.count(mutation.anchor)
    if count != 1:
        return True, (
            f"anchor appears {count} times, expected exactly 1 — the entry is stale against "
            "the current file and this run proved nothing"
        )

    try:
        target.write_text(original.replace(mutation.anchor, mutation.replacement, 1), encoding="utf-8")
        caught, tail = _run_tests(mutation.tests)
    finally:
        target.write_text(original, encoding="utf-8")
        if target.read_text(encoding="utf-8") != original:  # pragma: no cover - paranoia
            raise SystemExit(f"FATAL: could not restore {mutation.path}; fix before continuing")

    return (not caught), tail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run mutations whose id contains this substring")
    parser.add_argument("--list", action="store_true", help="print the declared mutations")
    args = parser.parse_args()

    chosen = [m for m in MUTATIONS if not args.only or args.only in m.id]
    if not chosen:
        print(f"no mutation id contains {args.only!r}", file=sys.stderr)
        return 2

    if args.list:
        for m in chosen:
            print(f"{m.id:34s} {m.what}\n{'':34s} {m.finding}")
        return 0

    survivors: list[tuple[Mutation, str]] = []
    for m in chosen:
        print(f"[{m.id}] {m.what} ... ", end="", flush=True)
        survived, detail = _apply(m)
        print("SURVIVED" if survived else "caught")
        if survived:
            survivors.append((m, detail))

    print()
    if survivors:
        print(f"{len(survivors)} of {len(chosen)} mutation(s) SURVIVED:\n", file=sys.stderr)
        for m, detail in survivors:
            print(f"  {m.id}: {m.what}", file=sys.stderr)
            print(f"    finding : {m.finding}", file=sys.stderr)
            print(f"    tests   : {' '.join(m.tests)}", file=sys.stderr)
            print(f"    observed: {detail}", file=sys.stderr)
        print(
            "\nA surviving mutation means the named tests pass against the reintroduced defect, "
            "so they report coverage they do not have.",
            file=sys.stderr,
        )
        return 1

    print(f"all {len(chosen)} declared mutation(s) were caught.")
    print(
        "This is coverage of the defects that are written down, and nothing else: a mutation "
        "nobody declared says nothing about the suite."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
