"""One implementation per concept, one import name (ADR 0005 §6).

v1 had **two McNemars, two EX definitions, two temp-then-replace helpers, and two
``LOW_CONFIDENCE_JOIN`` constants with different comparison operators**. The cost is
in ``docs/lessons-from-v1.md``: three copies of temp-then-replace existed and *none*
was durable, so the run ledger lost 16 of 17 records under concurrent writers; two
definitions of "excluded" drifted, and the one that did not filter shipped PII
column names into the routing index.

**Why this gate matters more than it looks.** The layers of v2 are parcelled to
agents working in parallel, and a parcel cannot import a module its neighbour has
not written yet. So each one writes its own McNemar, its own hash, its own
temp-then-replace — two implementations of one concept is the *default* outcome of
the process, not a slip in it. Review does not catch it either, because each half
is locally correct and the defect exists only in the pair.

Two rules, and the second is the one that had to be designed rather than written:

**(a) Default-deny on duplicate top-level names.** Any name defined at module level
in two or more modules under ``src/`` is fatal unless it is in
:data:`KNOWN_DUPLICATES` with a stated reason. Deny-by-default because the
alternative — a list of names that *must* be unique — is a list somebody has to
remember to extend, and the whole failure mode here is nobody noticing.

**(b) Declared singletons, with a pending tier.** :data:`SINGLETON_CONCEPTS` names
concepts that must have exactly one definition site and says which module that is.
Most of those modules do not exist yet. A gate whose targets are unbuilt **must not
read as passing**, so an absent module is reported as *pending* and the count is
printed on every run: the output distinguishes "0 violations, 3 pending" from
"0 violations, nothing pending". Same argument as the archive tier in
``check_citations.py`` — a silent skip and a pass produce the same green tick, and
half this repo's defects have that shape. Pending is **not** fatal, because failing
the build for work that is scheduled and not yet done trains people to disable the
gate.

A module that exists but does not define its declared name **is** fatal, and so is
the name turning up in a module other than its declared home — that second case is
"two McNemars" caught while there is still only one.

AST-only, like the other gates: this script never imports the code it checks, so it
runs in a bare environment and a broken import-time guard cannot masquerade as a
duplicate.

Exit code 1 on any violation.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "governed_bi"

#: Names allowed to appear in more than one module, each with the reason.
#:
#: Kept as short as the tree forces. Every entry is a hole in rule (a), so the bar
#: is "this name cannot mean one concept" rather than "fixing this is annoying".
KNOWN_DUPLICATES: dict[str, str] = {
    #: The module export protocol. Required to recur by construction — one per
    #: module is the correct number, and its contents are checked by nothing here.
    "__all__": "Python's per-module export list; recurrence is the language, not a concept",
}


class Singleton(NamedTuple):
    """A concept that must have exactly one definition site."""

    #: The top-level name, as it would be imported.
    name: str
    #: Where it must live, relative to ``src/governed_bi/``.
    module: str
    #: The v1 incident. A constraint whose reason is not written down gets deleted
    #: by whoever finds it inconvenient.
    why: str


#: Seeded from ADR 0005 §6 and the module table in ``docs/plans/v2-layer-handoffs.md``,
#: which is where these paths are declared. Only concepts with a *named* home go in
#: here — inventing a path would make the gate fail on a module nobody promised.
SINGLETON_CONCEPTS: tuple[Singleton, ...] = (
    Singleton(
        "mcnemar", "measure/stats.py",
        "v1 had two McNemars (ADR 0005 §6). Adjacent-arm discordance is 16-20%, so "
        "which one ran changes whether a ladder step is significant.",
    ),
    Singleton(
        "Measured", "register/quantity.py",
        "L-R1, 25 recurrences: a quantity whose absence had no representation, so 0 "
        "was used, and 0 is a measurement. A second Measured-shaped type is a second "
        "answer to what absence means.",
    ),
    Singleton(
        "corpus_content_hash", "corpus/hash.py",
        "v1's corpus_content_hash was the field labelled 'the corpus IS the "
        "treatment' and it compared \"unknown\" equal to itself, passing two runs "
        "with no recorded treatment through the comparability gate. Two hash "
        "implementations would reproduce that from the other direction: two runs "
        "with the same corpus and different hashes.",
    ),
    Singleton(
        "Population", "measure/population.py",
        "L-R3: v1 computed a headline rate over one row set and its significance "
        "test over another, invisibly, because each call site filtered "
        "independently. One object passed to both is the fix, and it only works if "
        "there is one object.",
    ),
)

#: Assignments whose value is one of these calls bind scaffolding, not a concept.
#: ``T = TypeVar("T")`` in two generic modules is not two implementations of
#: anything, and treating it as one would put ``T`` in :data:`KNOWN_DUPLICATES` —
#: a name-shaped hole that then excuses a real ``T``. Matching on the *call* keeps
#: the exemption tied to what the line does.
SCAFFOLD_CALLS: frozenset[str] = frozenset({"TypeVar", "ParamSpec", "TypeVarTuple"})

SKIP_DIRS: frozenset[str] = frozenset({"__pycache__"})


def _is_scaffold(value: ast.expr | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in SCAFFOLD_CALLS


def top_level_names(tree: ast.Module) -> list[tuple[str, int]]:
    """Every name this module binds at module level, with its line.

    **Plain assignments are included, and that is a deliberate widening** of the
    "def / class / annotated assignment" reading. v1's cited case —
    ``LOW_CONFIDENCE_JOIN``, two copies with different comparison operators — is a
    bare constant assignment, and a gate that scanned only annotated declarations
    would have watched it happen. Type aliases (``Row = tuple[Any, ...]``) are bare
    assignments too, and an alias is a concept.

    Only the module's own top level: a name inside a class or a function is scoped
    and cannot be imported, so it is not an import name.
    """
    out: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((node.name, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append((node.target.id, node.lineno))
        elif isinstance(node, ast.Assign):
            if _is_scaffold(node.value):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.append((target.id, node.lineno))
    return out


def main() -> int:
    # ``--root DIR`` scans a different tree, and it exists so this gate can be tested
    # without writing into ``src/``.
    #
    # Not a test hook bolted on: the two tests that previously covered the singleton
    # tiers used ``src/governed_bi/corpus/hash.py`` as a scratch file, chosen because
    # that path was expected to stay absent. Parcel D built it, and from then on the
    # suite **overwrote and then deleted real source code** — the ``rmdir`` in their
    # ``finally`` also raised, because ``corpus/`` was no longer empty. A test that
    # writes to a production path is a test that will eventually overwrite production
    # code; the only durable fix is for the tool to be pointable at a tree the test owns.
    argv = sys.argv[1:]
    pkg = PKG
    if "--root" in argv:
        pkg = Path(argv[argv.index("--root") + 1]).resolve() / "src" / "governed_bi"

    if not pkg.exists():
        print(f"no package at {pkg}", file=sys.stderr)
        return 1

    files = [p for p in sorted(pkg.rglob("*.py")) if not SKIP_DIRS & set(p.parts)]
    if not files:
        print(f"no modules under {pkg} — refusing to pass vacuously", file=sys.stderr)
        return 1

    #: name -> [(module relative to the package, line)]
    where: dict[str, list[tuple[str, int]]] = defaultdict(list)
    problems: list[str] = []

    for path in files:
        rel = path.relative_to(pkg).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as err:
            problems.append(f"src/governed_bi/{rel}:{err.lineno}: does not parse: {err.msg}")
            continue
        for name, lineno in top_level_names(tree):
            where[name].append((rel, lineno))

    # ── (a) duplicate top-level names ─────────────────────────────────────────
    waived_hit: set[str] = set()
    for name, sites in sorted(where.items()):
        modules = sorted({m for m, _ in sites})
        if len(modules) < 2:
            continue
        if name in KNOWN_DUPLICATES:
            waived_hit.add(name)
            continue
        for module, lineno in sites:
            problems.append(
                f"src/governed_bi/{module}:{lineno}: {name!r} is also defined in "
                f"{', '.join(m for m in modules if m != module)}. One implementation "
                "per concept, one import name (ADR 0005 §6). If this name cannot "
                "mean one concept, add it to KNOWN_DUPLICATES with the reason."
            )

    stale = sorted(set(KNOWN_DUPLICATES) - waived_hit)

    # ── (b) declared singletons ───────────────────────────────────────────────
    pending: list[Singleton] = []
    resolved: list[Singleton] = []
    for concept in SINGLETON_CONCEPTS:
        sites = where.get(concept.name, [])
        elsewhere = sorted({m for m, _ in sites if m != concept.module})
        if not (pkg / concept.module).exists():
            if elsewhere:
                problems.append(
                    f"src/governed_bi/{elsewhere[0]}: {concept.name!r} is declared to "
                    f"live in {concept.module}, which does not exist yet, and is "
                    f"already defined in {', '.join(elsewhere)}. {concept.why}"
                )
            else:
                pending.append(concept)
            continue
        if not any(m == concept.module for m, _ in sites):
            problems.append(
                f"src/governed_bi/{concept.module}: exists but does not define "
                f"{concept.name!r}, which is declared to live there. {concept.why}"
            )
            continue
        resolved.append(concept)
        # A second home is already reported by rule (a) unless the name is waived,
        # and a waiver must not launder a declared singleton.
        if elsewhere:
            problems.append(
                f"src/governed_bi/{concept.module}: {concept.name!r} is a declared "
                f"singleton and is also defined in {', '.join(elsewhere)}. "
                f"{concept.why}"
            )

    if problems:
        print(f"{len(problems)} duplicate-concept violation(s):\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    n_names = sum(len({m for m, _ in s}) for s in where.values())
    print(
        f"one implementation per concept OK across {len(files)} module(s), "
        f"{len(where)} name(s), {n_names} definition site(s); "
        f"{len(waived_hit)} waived duplicate(s)"
    )
    if pending:
        print(
            f"singletons: {len(resolved)} resolved, {len(pending)} PENDING — the "
            "declared module does not exist yet, so this concept is unenforced:"
        )
        for concept in pending:
            print(f"  {concept.name} -> {concept.module} (not built)")
    else:
        print(
            f"singletons: {len(resolved)} resolved, 0 pending — every declared "
            "concept has its home and this rule is fully enforced"
        )
    for name in stale:
        print(
            f"stale waiver: {name!r} is in KNOWN_DUPLICATES and is no longer "
            "duplicated — delete the entry"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
