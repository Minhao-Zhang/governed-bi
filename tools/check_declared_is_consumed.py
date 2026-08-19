"""Declared machinery must have a consumer on the other end (open-work §3.10).

AST-only (never imports the package). Exit 1 on violation.

**The incident this prevents.** One shape has now recurred seven times in a week: something
is declared -- a knob, a record field, a state channel -- and *nothing on the other end reads
it*. Each instance is individually small and none of them fails a test, because declaring and
consuming live in different files and nothing forces them to meet. Together they are why
numbers here have twice been quotable and wrong:

* ``reflect_verdict`` was projected into the turn record by ``stamp`` from the day the node
  landed, and ``eval/harness.py::project_turn`` never carried it to the artifact -- so the arm
  the knob's own note demands ("stays off until tools/score_reflector.py shows the verdict
  beats the base rate") could not have been scored even if it had been run.
* ``git_sha`` is an operational knob on every row and ``None`` on every row of every arm.
* ``facet_degraded`` is a retrieval-health signal and is constant ``False`` on all 1,351 rows
  of all six arms.
* ``w_lexical`` / ``w_semantic`` / ``semantic_scale_ceiling`` are comparability knobs resolved
  per turn by ``serve.runtime.channel_scale``. They *were* bound into a module constant at
  import from ``knob_default``, which is why this file described them that way; audit I10
  records why that made the declaration a false claim about a run.

Four rules, one per direction the wire can be missing:

``K1``  every declared knob is named somewhere outside ``register/``.
``K2``  every key written into a ``knobs`` mapping is a declared knob.
``R1``  every declared record field is named in the artifact projector.
``S1``  every ``ServeState`` channel has both a writer and a reader outside ``state.py``.

**This gate is RED today, deliberately.** It was written against a tree with a known
population of these defects and it finds them; a conformance check that went green on first
run against this codebase would be the same mistake as the eight instrument tests that
asserted constants against themselves. ``docs/analysis/declared-not-consumed.md`` ranks every
finding by consequence. Turning a line green means wiring the declaration to a consumer or
deleting the declaration -- **not** adding it to a ``WAIVED`` table. A waiver is only honest
when its reason can say why a declaration with no consumer is *correct*, which is true of
about two of these.

**What K1 can and cannot see.** Evidence is any occurrence of the knob's name as a string
constant or a bare identifier outside ``register/``. That is a floor, not a proof: a
coincidental literal launders a knob (``arms`` is credited by an unrelated ``"arms"`` dict key
in ``eval/report.py``, and ``split`` the same way). The sharper rule -- "reachable from
``knob_default`` / ``float_knob`` / a ``knobs[...]`` write" -- was tried and rejected because
two live knobs (``context_budget_chars``, ``read_body_max_tokens``) are read through
hand-rolled ``for source in (state, knobs, cfg)`` loops rather than an accessor, and a gate
that has to be waived for correct code teaches people to waive it.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent

#: Where each register lives, relative to the repository root.
KNOBS_MODULE = "src/governed_bi/register/knobs.py"
RECORD_MODULE = "src/governed_bi/register/record.py"
STATE_MODULE = "src/governed_bi/serve/state.py"

#: The projector that turns a served turn into a measurement row. R1 is a claim about *these*
#: files: a field the register declares and neither one names is a field no artifact carries,
#: whatever ``stamp`` did with it.
#:
#: Two files, not one, since the 1000-line cap split ``eval/harness.py``: ``project_turn`` --
#: the pure row-shaper most fields are named in -- moved to ``eval/projection.py``, but
#: ``run_id``, ``turn_id``, ``thread_id`` and ``attempt_id`` are set by the orchestration that
#: stayed behind (``_run_one``'s ``row["run_id"] = run_id`` and ``_base_turn``'s turn dict), and
#: still reach every row this harness produces.
ARTIFACT_PROJECTOR: frozenset[str] = frozenset(
    {"src/governed_bi/eval/harness.py", "src/governed_bi/eval/projection.py"}
)

#: Prefix whose contents are the declaration side and therefore cannot be their own consumer.
REGISTER_PREFIX = "src/governed_bi/register/"

SKIP_DIRS: frozenset[str] = frozenset({"__pycache__"})

#: Conformance checkers are excluded from the evidence scan. They quote declared names *in
#: order to police them* -- this file names every knob it waives, and
#: ``check_one_implementation.py`` names ``corpus_content_hash`` and ``mcnemar`` -- so counting
#: them as consumers lets a gate launder the very declarations it exists to catch. Found the
#: hard way: the first run of this file credited its own ``WAIVED_KNOBS`` entries and reported
#: them as consumed.
def _is_checker(rel: str) -> bool:
    return rel.startswith("tools/check_")


class Waiver(NamedTuple):
    """One declared thing allowed to have no consumer, and why it is correct."""

    name: str
    why: str


#: K1 waivers. The bar is "this declaration is correct with nothing reading its name", not
#: "wiring it is annoying". Two entries clear it.
WAIVED_KNOBS: tuple[Waiver, ...] = (
    Waiver(
        "asset_budgets",
        "derived inside knobs.py from ASSET_REGISTER and hashed_by_content: the name labels a "
        "digest, and the budgets themselves are consumed through ASSET_REGISTER, which every "
        "retrieval site imports. A lookup by this string would be the second reader ADR 0005 "
        "§6 forbids.",
    ),
    Waiver(
        "cache_cost_reduction_target",
        "an acceptance criterion for a measurement that has not been run, recorded for the "
        "reader rather than read by code. NOTE: it is Role.comparability, so it enters the "
        "comparability set and a run that changed the target would read as a different "
        "treatment -- the role is wrong, not the absence of a reader.",
    ),
)

#: R1 waivers: register fields deliberately absent from the artifact row.
WAIVED_RECORD_FIELDS: tuple[Waiver, ...] = (
    Waiver(
        "rewrite",
        "null by construction on every eval row -- the node runs only on a follow-up and the "
        "harness serves one turn per question. Carrying it would add a column that is a "
        "measured constant.",
    ),
    Waiver(
        "cache_read_tokens",
        "carried inside the row's `usage` list, one entry per model call. UsageRecord declares "
        "the same two token fields; a turn-level roll-up beside them would be a second answer "
        "to what the turn cost.",
    ),
    Waiver(
        "cache_write_tokens",
        "as above -- inside `usage`, billed at 1.25x and therefore kept separate there.",
    ),
)

#: S1 waivers: state channels that legitimately have only one side in this tree.
WAIVED_CHANNELS: tuple[Waiver, ...] = (
    Waiver(
        "pinned_schemas",
        "written through the `PINNED_SCHEMAS_KEY` constant in eval/replay.py rather than as a "
        "string literal, so the writer is real and this rule's evidence cannot see it. Not "
        "worth resolving the indirection: one name for the channel is the point of that "
        "constant.",
    ),
)


# --------------------------------------------------------------------------- #
# Reading the declarations
# --------------------------------------------------------------------------- #


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None


def _str(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _enum_member(node: ast.expr | None) -> str:
    """``Role.comparability`` -> ``"comparability"``. ``""`` for anything else."""
    return node.attr if isinstance(node, ast.Attribute) else ""


def declared_knobs(root: Path) -> list[tuple[str, str]]:
    """``(name, role)`` for every ``_k(...)`` row in the knob register."""
    tree = _parse(root / KNOBS_MODULE)
    out: list[tuple[str, str]] = []
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_k":
            name = _str(node.args[0]) if node.args else None
            if name:
                role = _enum_member(node.args[2]) if len(node.args) > 2 else ""
                out.append((name, role))
    return out


def declared_record_fields(root: Path) -> list[tuple[str, str, str]]:
    """``(name, tier, owner_stage)`` for every ``_f(...)`` row in the record register."""
    tree = _parse(root / RECORD_MODULE)
    out: list[tuple[str, str, str]] = []
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_f":
            name = _str(node.args[0]) if node.args else None
            if name:
                tier = _enum_member(node.args[1]) if len(node.args) > 1 else ""
                owner = _enum_member(node.args[3]) if len(node.args) > 3 else ""
                out.append((name, tier, owner))
    return out


def declared_channels(root: Path) -> tuple[list[str], frozenset[str]]:
    """``(every annotated key of ServeState, the names state.py calls TEST_HOOKS)``.

    ``TEST_HOOKS`` is state.py's own declaration that a channel's writer is a test rather than
    a node, so S1 does not demand a production writer for one. Read from the source instead of
    hard-coded here: a channel promoted out of the hook set must fall under the rule the same
    day, not whenever someone remembers this file.
    """
    tree = _parse(root / STATE_MODULE)
    if tree is None:
        return [], frozenset()
    channels = [
        body.target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ServeState"
        for body in node.body
        if isinstance(body, ast.AnnAssign) and isinstance(body.target, ast.Name)
    ]
    hooks: set[str] = set()
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            target = first.id if isinstance(first, ast.Name) else None
        if target != "TEST_HOOKS" or node.value is None:
            continue
        for sub in ast.walk(node.value):
            name = _str(sub)
            if name:
                hooks.add(name)
    return channels, frozenset(hooks)


# --------------------------------------------------------------------------- #
# Reading the consumers
# --------------------------------------------------------------------------- #


class Evidence(NamedTuple):
    """Where each name is mentioned, written and read, keyed by repo-relative path."""

    #: Any string constant or bare identifier equal to the name.
    mentions: dict[str, set[str]]
    #: A dict-literal key, or the slice of a subscript assignment target.
    writes: dict[str, set[str]]
    #: ``.get("x")``, a load subscript ``["x"]``, or any string-literal call argument.
    reads: dict[str, set[str]]
    #: ``knobs["x"] = ...`` and dict-literal keys inside a ``*knob*``-named function,
    #: with the site, so K2 can name the line.
    knob_writes: set[tuple[str, str, int]]


def _sources(root: Path) -> list[Path]:
    pkg = root / "src" / "governed_bi"
    tools = root / "tools"
    files: list[Path] = []
    for base in (pkg, tools):
        if base.exists():
            files.extend(p for p in sorted(base.rglob("*.py")) if not SKIP_DIRS & set(p.parts))
    return [p for p in files if not _is_checker(p.relative_to(root).as_posix())]


def _knob_write_targets(tree: ast.Module) -> set[tuple[str, int]]:
    """``(key, lineno)`` for every place this module builds a ``knobs_resolved`` mapping.

    Two shapes, because both are in use: ``knobs["llm_model"] = ...`` in
    ``serve/session.py``, and the dict literal returned by ``model/embedder.embedding_knobs``.
    The second is matched by the enclosing function's name rather than by the variable's, so a
    helper that renames its local still counts.
    """
    out: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in ("knobs", "knobs_resolved")
                ):
                    key = _str(target.slice)
                    if key:
                        out.add((key, target.lineno))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "knob" in node.name:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    for k in inner.keys:
                        key = _str(k)
                        if key:
                            out.add((key, inner.lineno))
    return out


def gather(root: Path) -> Evidence:
    mentions: dict[str, set[str]] = defaultdict(set)
    writes: dict[str, set[str]] = defaultdict(set)
    reads: dict[str, set[str]] = defaultdict(set)
    knob_writes: set[tuple[str, str, int]] = set()

    for path in _sources(root):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()

        for key, lineno in _knob_write_targets(tree):
            knob_writes.add((key, rel, lineno))

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                mentions[node.value].add(rel)
            elif isinstance(node, ast.Name):
                mentions[node.id].add(rel)

            # `writes` / `reads` are the *consumer* side, so the declaration layer cannot
            # contribute to them. Without this, `record.py`'s own `_f("run_id", ...)` counted
            # as a read of the `run_id` channel and S1 passed on a channel nothing consumed —
            # caught by mutation-testing this file against a fixture tree.
            if rel.startswith(REGISTER_PREFIX):
                continue

            if isinstance(node, ast.Dict):
                for k in node.keys:
                    key = _str(k)
                    if key:
                        writes[key].add(rel)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript):
                        key = _str(target.slice)
                        if key:
                            writes[key].add(rel)

            if isinstance(node, ast.Call):
                # Any string-literal argument counts as a read: `float_knob(state, "x")`,
                # `state.get("x")` and `prompt_text("x")` are all lookups by name, and the
                # alternative -- enumerating the accessors -- goes stale silently.
                for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                    key = _str(arg)
                    if key:
                        reads[key].add(rel)
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Load)
            ):
                key = _str(node.slice)
                if key:
                    reads[key].add(rel)
            # Dispatch by name: `if name == "latency_sec"` and `if name in ("run_id", ...)`
            # in `stamp._extract_factory` are how most of the register is read, and neither is
            # a call or a subscript. Without this, `run_id` and `attempt_id` -- read on every
            # single turn -- reported as channels with no reader.
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    for sub in ast.walk(comparator):
                        key = _str(sub)
                        if key:
                            reads[key].add(rel)

    return Evidence(mentions, writes, reads, knob_writes)


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #


def _waived(table: tuple[Waiver, ...]) -> dict[str, str]:
    return {w.name: w.why for w in table}


def rule_k1(root: Path, ev: Evidence) -> tuple[list[str], set[str]]:
    """Every declared knob is named outside ``register/``."""
    waived = _waived(WAIVED_KNOBS)
    problems: list[str] = []
    hit: set[str] = set()
    for name, role in declared_knobs(root):
        sites = {
            p
            for p in (ev.mentions.get(name) or set())
            if not p.startswith(REGISTER_PREFIX)
        }
        if sites:
            continue
        if name in waived:
            hit.add(name)
            continue
        problems.append(
            f"{KNOBS_MODULE}: knob {name!r} (role={role or '?'}) is named nowhere outside "
            "register/. Nothing can resolve it and nothing can read it, so an arm that sets "
            "it records a different configuration and runs identical code. Wire it, delete "
            "it, or add it to WAIVED_KNOBS with a reason that says why no consumer is correct."
        )
    return problems, hit


def rule_k2(root: Path, ev: Evidence) -> list[str]:
    """Every key written into a ``knobs`` mapping is a declared knob."""
    declared = {name for name, _ in declared_knobs(root)}
    problems: list[str] = []
    for key, rel, lineno in sorted(ev.knob_writes):
        if key in declared:
            continue
        problems.append(
            f"{rel}:{lineno}: writes knobs[{key!r}], which KNOB_REGISTER does not declare. "
            "An undeclared key is outside comparability_keys() and therefore outside the "
            "config hash: two runs differing only in this value compare as one treatment. "
            "Declare it in register/knobs.py or stop writing it."
        )
    return problems


def rule_r1(root: Path, ev: Evidence) -> tuple[list[str], set[str]]:
    """Every declared record field is named in the artifact projector."""
    waived = _waived(WAIVED_RECORD_FIELDS)
    problems: list[str] = []
    hit: set[str] = set()
    for name, tier, owner in declared_record_fields(root):
        sites = ev.mentions.get(name) or set()
        if sites & ARTIFACT_PROJECTOR:
            continue
        if name in waived:
            hit.add(name)
            continue
        problems.append(
            f"{RECORD_MODULE}: record field {name!r} (tier={tier or '?'}, owner="
            f"{owner or '?'}) is never named in {' or '.join(sorted(ARTIFACT_PROJECTOR))}. "
            "`stamp` projects it "
            "into the turn record and the measurement row drops it, which is exactly how "
            "`reflect_verdict` came to be unscoreable. Carry it in `project_turn`, or add it "
            "to WAIVED_RECORD_FIELDS with a reason."
        )
    return problems, hit


def rule_s1(root: Path, ev: Evidence) -> tuple[list[str], set[str], int]:
    """Every ``ServeState`` channel has both a writer and a reader outside ``state.py``."""
    waived = _waived(WAIVED_CHANNELS)
    problems: list[str] = []
    hit: set[str] = set()
    channels, hooks = declared_channels(root)
    for name in channels:
        if name in hooks:
            continue
        writers = (ev.writes.get(name) or set()) - {STATE_MODULE}
        readers = (ev.reads.get(name) or set()) - {STATE_MODULE}
        if writers and readers:
            continue
        if name in waived:
            hit.add(name)
            continue
        half = "no writer" if not writers else "no reader"
        other = f"{len(readers)} reader(s)" if not writers else f"{len(writers)} writer(s)"
        problems.append(
            f"{STATE_MODULE}: channel {name!r} has {half} outside state.py ({other}). A "
            "channel with one end is a value that either never moves or never arrives; "
            "either way the behaviour it is supposed to carry is not there."
        )
    return problems, hit, len(channels) - len(hooks & set(channels))


# --------------------------------------------------------------------------- #


def main() -> int:
    # ``--root DIR`` checks a tree the caller owns, so a negative test never writes a probe
    # module into ``src/`` (see ``check_one_implementation.py``).
    argv = sys.argv[1:]
    root = ROOT
    if "--root" in argv:
        root = Path(argv[argv.index("--root") + 1]).resolve()

    knobs = declared_knobs(root)
    fields = declared_record_fields(root)
    if not knobs or not fields:
        print(
            f"no knob or record register under {root} — refusing to pass vacuously "
            f"({len(knobs)} knob(s), {len(fields)} field(s))",
            file=sys.stderr,
        )
        return 1

    ev = gather(root)

    k1, k1_waived = rule_k1(root, ev)
    k2 = rule_k2(root, ev)
    r1, r1_waived = rule_r1(root, ev)
    s1, s1_waived, n_channels = rule_s1(root, ev)

    groups = (
        ("K1  declared knob, no consumer", k1),
        ("K2  knob written but not declared", k2),
        ("R1  record field never reaches the artifact", r1),
        ("S1  state channel with only one end", s1),
    )
    total = sum(len(p) for _, p in groups)

    if total:
        print(
            f"{total} declared-but-unconsumed violation(s) across "
            f"{len(knobs)} knob(s), {len(fields)} record field(s), "
            f"{n_channels} state channel(s):\n",
            file=sys.stderr,
        )
        for title, problems in groups:
            if not problems:
                continue
            print(f"── {title} ({len(problems)}) " + "─" * 12, file=sys.stderr)
            for p in problems:
                print(f"  {p}", file=sys.stderr)
            print(file=sys.stderr)
        print(
            "Each line is one instance of open-work.md §3.10. Ranked by consequence in "
            "docs/analysis/declared-not-consumed.md.",
            file=sys.stderr,
        )
        return 1

    print(
        f"declared-is-consumed OK across {len(knobs)} knob(s), {len(fields)} record "
        f"field(s), {n_channels} state channel(s)"
    )

    # A waiver that no longer waives anything is a claim about code that has changed. Only
    # meaningful against this repository: under ``--root`` the tables describe a tree that is
    # not the one being checked, so every entry would read as stale.
    if root != ROOT:
        return 0
    for table, hit, label in (
        (WAIVED_KNOBS, k1_waived, "WAIVED_KNOBS"),
        (WAIVED_RECORD_FIELDS, r1_waived, "WAIVED_RECORD_FIELDS"),
        (WAIVED_CHANNELS, s1_waived, "WAIVED_CHANNELS"),
    ):
        for waiver in table:
            if waiver.name not in hit:
                print(
                    f"stale waiver: {label} names {waiver.name!r}, which now has a "
                    "consumer (or no longer exists) — delete the entry"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
