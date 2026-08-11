"""One implementation per concept, one import name (ADR 0005 §6).

Default-deny on duplicate top-level names under ``src/`` (except
:data:`KNOWN_DUPLICATES`). Declared singletons in :data:`SINGLETON_CONCEPTS`
(pending if module absent). AST-only. Exit 1 on violation.
"""


from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "governed_bi"

#: Names allowed to appear in more than one module, each with the reason. Every entry is a hole
#: in rule (a), so the bar is "this name cannot mean one concept", not "fixing this is annoying".
KNOWN_DUPLICATES: dict[str, str] = {
    #: Required to recur by construction — one per module is the correct number.
    "__all__": "Python's per-module export list; recurrence is the language, not a concept",
    #: Exempts the name only. Both current sites are `__main__.py` modules sharing no body; a
    #: `main` in a module that is not an entry point is a different case this must not permit.
    "main": "the `python -m` entry-point protocol; one per program, name fixed by the runtime",
}


class Singleton(NamedTuple):
    """A concept that must have exactly one definition site."""

    #: The top-level name, as it would be imported.
    name: str
    #: Where it must live, relative to ``src/governed_bi/``.
    module: str
    #: Why this must stay unique (ADR pointer).
    why: str


#: Seeded from ADR 0005 §6. Only concepts with a *named* home go in
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
    Singleton(
        "on_digest", "corpus/identity.py",
        "ADR 0005 §1.2: join identity is the relationship, not the table pair. "
        "Two on_digest implementations means two relationships between one pair "
        "and the last write wins — 33 of 57 schemas lost an edge before the "
        "curator ran (decision #36).",
    ),
    Singleton(
        "join_id", "corpus/identity.py",
        "Paired with on_digest: the id format is join_{schema}_{left}_{right}_"
        "{digest[:8]}. A second home invents a second identity for the same edge.",
    ),
)

#: Assignments whose value is one of these calls bind scaffolding, not a concept. Matching on
#: the *call* rather than adding ``T`` to :data:`KNOWN_DUPLICATES` keeps the exemption tied to
#: what the line does, instead of opening a name-shaped hole that would excuse a real ``T``.
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

    Plain assignments count, deliberately: v1's cited case (``LOW_CONFIDENCE_JOIN``, two copies
    with different comparison operators) is a bare constant, as are type aliases, so a gate
    reading only annotated declarations would have watched it happen. Top level only — a name
    scoped to a class or function cannot be imported.
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
    # ``--root DIR`` scans a different tree, so this gate can be tested without writing into
    # ``src/``. The singleton-tier tests used to scratch-write ``src/governed_bi/corpus/hash.py``
    # on the assumption it would stay absent; once Parcel D built it, the suite overwrote and
    # then deleted real source. A tool pointable at a tree the test owns is the durable fix.
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

    #: The same names as they appear in ``tools/``, for **rule (b) only**.
    #:
    #: Rule (a) deliberately stays inside the package (audit D11). Scanned across ``tools/`` it
    #: reports 14 duplicate names, and 13 of them are each script's own boilerplate — ``ROOT``,
    #: ``REPO``, ``PKG``, ``SKIP_DIRS``, ``EXEMPT``, ``DEFAULT_CORPUS``, ``DEFAULT_DATASET``,
    #: ``check_file``. Those are not two implementations of one concept, and a gate that reports
    #: them is a gate that gets waived until it means nothing.
    #:
    #: The fourteenth was real: ``mcnemar``, in ``tools/query_summary_alignment.py``, beside the
    #: singleton this file declares — whose stated reason is that "v1 had two McNemars … which one
    #: ran changes whether a ladder step is significant". The copy silently intersected unit sets
    #: where the real one refuses, and returned no minimum detectable effect. So the *declared*
    #: singletons are exactly what must not have a second home anywhere, and ``tools/`` is where
    #: four of the five second implementations the audit found were living.
    # **This repository's** ``tools/``, via the module constant, and skipped entirely under
    # ``--root``. The scratch trees the singleton tests build have no ``tools/``, so scanning
    # relative to ``pkg`` made the gate fail on a tree the caller owns; and scanning the real
    # ``tools/`` against a scratch package would mix two trees into one verdict. ``--root`` exists
    # to exercise the package-level rules, so the outside scan is not part of that.
    #
    # A guard against the opposite mistake: written as ``pkg.parent.parent.parent`` first, which
    # pointed one level above the repo, left ``outside`` empty, and made this rule pass vacuously
    # while the gate printed "6 resolved, fully enforced". Caught by putting the rival ``mcnemar``
    # back and watching the gate stay green.
    outside: dict[str, list[str]] = defaultdict(list)
    if pkg == PKG:
        tools_dir = ROOT / "tools"
        for path in sorted(tools_dir.glob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue  # its own parse error is ruff's to report, not ours
            for name, lineno in top_level_names(tree):
                outside[name].append(f"tools/{path.name}:{lineno}")
        if not outside:
            print(
                f"no top-level names under {tools_dir} — the singleton rule would pass vacuously "
                "outside the package",
                file=sys.stderr,
            )
            return 1

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
        # And outside the package, where rule (a) does not look. `tools/` is not a lesser tree:
        # it holds the eval driver and every analysis script, so a second implementation there
        # decides published numbers just as much as one in `src/`.
        if concept.name in outside:
            problems.append(
                f"{', '.join(outside[concept.name])}: {concept.name!r} is a declared singleton "
                f"that lives in src/governed_bi/{concept.module}. {concept.why} Import it "
                "rather than restating it — a copy in tools/ still decides a number."
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
