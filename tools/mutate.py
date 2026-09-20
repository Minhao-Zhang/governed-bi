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

**What a run proves.** For each mutation: everything the entry names still exists — the target
file, the anchor exactly once, and each named test — so the entry has not silently gone stale
against a refactor; the mutated tree makes **at least one named test fail**; and the tree is
restored. What it does *not* prove is that the tests are otherwise good — a mutation the suite
catches says nothing about the mutations nobody wrote down.

The staleness half is :func:`why_it_proves_nothing`, and it is a separate function rather than an
inline guard because a nightly is a bad place to learn that this morning's rename made an entry
vacuous. It reads text and writes nothing, so
``tests/conformance/test_the_mutation_catalogue_is_not_stale.py`` runs it over every entry on
every push. Three entries were already in that state when it was written; read that function for
which, and for why a broken reference fails *green* without it.

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

__all__ = ["MUTATIONS", "Mutation", "main", "why_it_proves_nothing"]


def why_it_proves_nothing(mutation: Mutation) -> str:
    """Why running ``mutation`` would prove nothing, or ``""`` if it would prove what it claims.

    A catalogue is append-only prose about *other* files, so every entry is a set of references a
    refactor elsewhere can quietly break. The ways that happens:

    * the target file is gone, or the anchor is not in it exactly once — the entry cannot be
      applied at all, and this is the case the runner has always refused;
    * the replacement is identical to the anchor, so the "mutated" tree is the original tree and
      the named tests pass for the reason they always did;
    * a named test does not exist. **This is the shape that fails green**, and it is why this
      function was extracted: pytest exits ``4`` both for a path that is not there and for a node
      id that is not in the file, and :func:`_run_tests` reads any non-zero exit as the suite
      noticing. Three entries were in that state when this check was written on 2026-08-25.
      ``a4-handler-not-registered`` named a test since renamed to
      ``test_both_handlers_are_actually_registered`` when a second action was added to it.
      ``d5-rival-mcnemar-returns`` and ``d11-singleton-scan-vacuous`` both named a node id in
      ``tests/conformance/test_register_closure.py`` — which still exists, so only resolving the
      node id finds them; ``77d5f9f`` ("Split the six files the WARN tier said the next edit would
      break") moved that test into
      ``tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py`` and left the two
      entries pointing at where it had been. Each of the three printed ``caught`` on every nightly
      while re-introducing its defect against nothing at all;
    * a node id given for a directory, which pytest cannot resolve either;
    * an entry naming no tests, which would hand pytest no selection and run the whole suite.

    A node id is resolved by looking for ``def <name>`` in the file, with any ``[param]`` suffix
    stripped. That is a floor rather than a proof — it cannot tell a live parametrisation id from
    a retired one, and it would credit a name defined inside a class it is not in — but it is the
    check that catches a rename, which is the whole observed failure mode.

    **Text only.** Nothing is written and no test is run, so
    ``tests/conformance/test_the_mutation_catalogue_is_not_stale.py`` asks this of every entry on
    every push. That matters because the nightly is the only other reader: it runs on a schedule,
    it cannot fire from a feature branch, and it costs a pytest selection per entry — so a
    reference broken by this morning's rename would otherwise be found a refactor late, if at all.
    """
    target = REPO / mutation.path
    if not target.is_file():
        return f"{mutation.path} is not a file, so there is nothing to mutate"

    # Universal newlines, matching how `_apply` normalises before it searches. The two
    # readers have to agree or this reports an anchor as present that the run cannot find,
    # which is exactly what a raw read here would have hidden: a mutation that "applied"
    # nothing and was scored `SURVIVED`.
    count = target.read_text(encoding="utf-8").count(mutation.anchor)
    if count != 1:
        return (
            f"anchor appears {count} times, expected exactly 1 — the entry is stale against "
            "the current file and this run proved nothing"
        )

    if mutation.replacement == mutation.anchor:
        return "the replacement is identical to the anchor, so the mutated tree is the original tree"

    if not mutation.tests:
        return "no tests are named, so the selection would be the whole suite rather than a property"

    for selection in mutation.tests:
        path, _, node = selection.partition("::")
        named = REPO / path
        if not named.exists():
            return f"named selection {selection!r} points at {path}, which does not exist"
        if not node:  # a whole file or a whole directory; existing is all there is to check
            continue
        if not named.is_file():
            return f"named selection {selection!r} gives a node id for {path}, which is a directory"
        if f"def {node.split('::')[-1].split('[')[0]}" not in named.read_text(encoding="utf-8"):
            return f"named selection {selection!r} is not defined in {path}"

    return ""


def _run_tests(selection: tuple[str, ...]) -> tuple[bool, str]:
    """``(any test failed, last line of output)``.

    A non-zero exit is the signal, and *any* non-zero counts: a collection error caused by the
    mutation is still the suite noticing. What must not happen is exit 0.

    That reading is only sound because :func:`why_it_proves_nothing` has already resolved the
    selection against the tree. Pytest exits ``4`` when a selection names nothing, which this
    function cannot tell apart from a mutation the suite caught — so without that guard a renamed
    test turns its entry into a permanent, silent pass.
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
    """Run one mutation. Returns ``(survived, detail)`` — ``survived`` meaning **bad**.

    An entry :func:`why_it_proves_nothing` rejects is reported as a survivor **without being
    applied**. That is the conservative direction: a reference the entry names has moved, so
    nobody knows whether the defect would still be caught, and the one answer that must not be
    printed is ``caught``.
    """
    vacuous = why_it_proves_nothing(mutation)
    if vacuous:
        return True, vacuous

    target = REPO / mutation.path
    # **Bytes to restore, universal-newline text to match**, and they have to be the two
    # different things they are.
    #
    # Restoring from text was lossy on Windows: `read_text` collapses CRLF to LF and
    # `write_text` expands LF back to `os.linesep`, so a file that was LF on disk came back
    # rewritten line by line. Worse, the guard could not see it — both sides of the old
    # comparison were universal-newline-decoded, so it compared LF to LF and passed while
    # `.github/workflows/ci.yml` said the restore was "verified byte-for-byte". Masked today
    # by `autocrlf=true` making `src/` CRLF already; a `.gitattributes`, `core.autocrlf=input`
    # or a WSL-origin checkout re-arms it.
    #
    # Matching has the opposite requirement. An anchor is committed data in
    # `mutation_catalogue_data_*.py`, written with `\n` because a catalogue that carried a
    # platform's line endings would match on one machine and not another. Reading raw to fix
    # the restore therefore broke every multi-line anchor on this checkout — found the same
    # hour, by two mutations that went from `caught` to `SURVIVED` with nothing else changed.
    original = target.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")

    try:
        # `newline=""` so the mutant is written exactly as `replace` produced it. Its line
        # endings do not matter — Python does not care and the file is transient — but a
        # translation here would be one more thing between the anchor and the test.
        target.write_text(
            text.replace(mutation.anchor, mutation.replacement, 1),
            encoding="utf-8",
            newline="",
        )
        caught, tail = _run_tests(mutation.tests)
    finally:
        target.write_bytes(original)
        if target.read_bytes() != original:  # pragma: no cover - paranoia
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
