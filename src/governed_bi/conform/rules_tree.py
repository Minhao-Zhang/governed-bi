"""The rules that need more than one asset's own mapping: V9, V11, V12, V14, V15, V16.

Three different kinds of "more", and the distinction is the one ``WHOLE_TREE_ONLY`` turns on:

* **A second asset.** V9 resolves a reference against the ids in the tree.
* **A manifest on disk.** V11, V12 and V15 read one asset's own text against a file the
  obfuscation dataset ships. V11 and V12 answer from a single asset and therefore run in
  ``--file`` mode too; V15's "no fewer" half needs every table.
* **The engine.** V14 asks the loader, V16 asks the renderer. Both are about a *file*, so both
  key on :func:`~governed_bi.conform.findings.where_of_file`.

**The engine imports are inside the functions that need them, deliberately.**
``serve/__init__.py`` re-exports the graph, so a module-level ``governed_bi.serve.context`` here
would make ``import governed_bi.conform`` pull langgraph -- and ADR 0016 records a three-package
install dying at exactly that import. Every other rule in this package runs without it.
"""


from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from governed_bi.corpus.identity import derive_column_id

from .findings import Finding, _text, where_of, where_of_file

#: What one table actually costs the context block: its structural line, its body, and the roster
#: its pulled-in columns fold into (``serve/context.py``). **This is the half a per-asset cap
#: cannot see.** A roster entry runs ~53 chars, so a 1,500-column table would render 80,000 chars
#: and consume the entire budget while every individual asset passed its cap and nothing
#: complained. That is the deliverability question the file cap was crudely approximating, and
#: this is it asked directly.
#:
#: 20,000 is a quarter of ``context_budget_chars`` (80,000) -- one table may not take a quarter of
#: the whole rendered budget. Worst observed: 8,435 (``archibus_room_attributes``, 156 columns)
#: and 6,787 (``european_football_2.partido``, 118), so 2.4x headroom.
CLOSURE_CAP: int = 20_000


def check_references(assets: Iterable[tuple[str, dict[str, Any], Path]]) -> list[Finding]:
    """V9 -- every declared reference resolves. Whole-corpus only.

    An inline column's id comes from :func:`~governed_bi.corpus.identity.derive_column_id`, not
    from ``f"{table_id}.{physical_name}"``. The two agree only while every column name is a bare
    identifier: ``derive_column_id`` slugs the name, so a column physically called
    ``Air Carriers`` is ``...Air_Carriers_66c534`` to the loader and ``...Air Carriers`` to the
    hand-rolled spelling. A corpus that referenced such a column *correctly* therefore failed
    this rule, and V9 returns exit 1 — a gate blocking a valid tree. The module docstring names
    this exact class of bug as the reason this file imports its policy instead of restating it.
    """
    ids = {str(a.get("id")) for kind, a, _ in assets if a.get("id")}
    for kind, a, _ in assets:
        if kind == "table":
            tid = str(a.get("id") or "")
            for col in a.get("columns") or []:
                if isinstance(col, dict) and col.get("physical_name"):
                    ids.add(derive_column_id(tid, str(col["physical_name"])))
    bad: list[Finding] = []
    for kind, a, path in assets:
        where = where_of(kind, a, path)
        targets: list[tuple[str, Any]] = []
        if kind == "term" and isinstance(a.get("binding"), dict):
            targets.append(("binding.target_id", a["binding"].get("target_id")))
        if kind == "metric":
            targets.append(("base_table", a.get("base_table")))
        if kind == "join":
            targets += [("left_table", a.get("left_table")), ("right_table", a.get("right_table"))]
        for field, target in targets:
            if target and str(target) not in ids:
                bad.append(Finding(f"{where}: {field}={target!r} resolves to no asset"))
    return bad


def check_suspect_summaries(assets, trap_manifest: Path) -> list[Finding]:
    """V11 -- a suspect column's summary must not carry the vocabulary of what it resembles.

    A retrieval rule, not a disclosure one: ``summary`` is the index, so a caveat naming the
    real column makes the unreliable one rank for that column's questions and compete for the
    same 30-column budget.
    """
    traps = json.loads(trap_manifest.read_text(encoding="utf-8"))
    resembles: dict[tuple[str, str], str] = {
        (t["db"], t["names"]["rename"]): t["source_column"] for t in traps if t.get("names")
    }
    bad: list[Finding] = []
    for kind, a, path in assets:
        if kind != "column":
            continue
        rel = a.get("reliability")
        status = (rel or {}).get("status") if isinstance(rel, dict) else None
        if str(getattr(status, "value", status)) != "suspect":
            continue
        db, name = _text(a.get("schema")), _text(a.get("physical_name"))
        source = resembles.get((db, name))
        summary = _text(a.get("summary"))
        if source and source != name and re.search(rf"\b{re.escape(source)}\b", summary):
            bad.append(
                Finding(f"{where_of(kind, a, path)}: summary of a suspect column names {source!r}, "
                        "which makes it rank for that column's questions")
            )
    return bad


def check_suspect_set(assets, trap_manifest: Path, table_manifest: Path,
                      rename_map: Path) -> list[Finding]:
    """V15 -- exactly the manifest's columns are marked suspect, no more and no fewer.

    V11 polices how a suspect column is *worded*; nothing until now policed *which* columns
    carry the mark, and the two failure directions cost different things. Marking a real column
    suspect is the expensive one: ``reliability.note`` is never dropped from the context, so the
    model is told every turn not to use a column it needs.

    The packet hands writers a flat, de-duplicated list of bare column names, so a name that is
    real on one table and planted on another arrives indistinguishable. That is how
    ``regional_sales.emplacements_magasin.code_zone`` -- a real telephone area code -- came to be
    suppressed alongside the planted ``code_zone_geo``, while a *different* real ``code_zone``
    on ``zones_geographiques`` holds state abbreviations.

    The manifest keys tables by their upstream BIRD name, so every lookup goes through
    ``schema_rename_map.json`` first. Comparing against the raw name silently reports every real
    column in a renamed schema as mis-marked, which is what a first pass at this check did.
    """
    rmap = json.loads(rename_map.read_text(encoding="utf-8"))
    traps = json.loads(trap_manifest.read_text(encoding="utf-8"))
    tables = json.loads(table_manifest.read_text(encoding="utf-8"))

    planted: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for t in traps:
        if t.get("names"):
            db = t["db"]
            renamed = (rmap.get(db) or {}).get(t["table"], t["table"])
            planted[db].add((renamed, t["names"]["rename"]))
    decoy_tables: dict[str, set[str]] = defaultdict(set)
    for t in tables:
        renamed = (t.get("names") or {}).get("rename") or {}
        if t.get("db") and renamed.get("table"):
            decoy_tables[t["db"]].add(renamed["table"])

    bad: list[Finding] = []
    for kind, a, path in assets:
        if kind != "table":
            continue
        db, tbl = _text(a.get("schema")), _text(a.get("physical_name"))
        if db not in planted and db not in decoy_tables:
            continue
        whole = tbl in decoy_tables.get(db, set())
        for col in a.get("columns") or []:
            if not isinstance(col, dict):
                continue
            name = _text(col.get("physical_name"))
            rel = col.get("reliability")
            status = (rel or {}).get("status") if isinstance(rel, dict) else None
            marked = str(getattr(status, "value", status)) == "suspect"
            should = whole or (tbl, name) in planted.get(db, set())
            if marked and not should:
                bad.append(Finding(
                    f"{path.name}:{tbl}.{name}: marked suspect but the manifest says it is real; "
                    "the caveat renders every turn and can never be dropped"))
            elif should and not marked:
                bad.append(Finding(f"{path.name}:{tbl}.{name}: the manifest plants this column, "
                                   "but it carries no reliability caveat"))
    return bad


def check_loadable(paths: Iterable[Path]) -> list[Finding]:
    """V14 -- the engine can actually load the file.

    The other rules read raw YAML so they still answer on a half-written tree, and that leaves
    a hole: a file with a valid ``asset_type`` and a well-formed summary can still be rejected
    by the model. The first scaffold wrote ``provenance.source: introspection``, which is not
    one of the four the enum allows; every text rule passed and the loader returned zero assets
    from 18 files.
    """
    from governed_bi.corpus.store import load_file

    bad: list[Finding] = []
    for path in paths:
        _, problems = load_file(path)
        for problem in problems:
            bad.append(Finding(f"{where_of_file(path)}: {getattr(problem, 'reason', problem)}"))
    return bad


def check_delivery_closure(paths: Iterable[Path]) -> list[Finding]:
    """V16 -- a table plus the roster its columns fold into fits :data:`CLOSURE_CAP`.

    **Measured with the renderer that does the delivering**, not with a second copy of its
    arithmetic: ``serve/context.py::rendered_closure_chars`` is what decides the cost, so a rule
    that recomputed it here would be free to drift from the thing it claims to bound. That
    function is the interface and it exists because of this rule -- until 2026-08-25 this reached
    past it to ``_structural_line`` and ``_roster_entry``, two private functions, and did the
    addition itself, which is the drift it was written to avoid arriving by the back door.
    Loading the file through ``corpus/store.py`` rather than reading the raw YAML the other rules
    use is the same argument: inline columns only become assets on the way through the loader, and
    it is the assets that are rendered.

    Per file, which is exact rather than convenient: a column asset exists only inside its
    table's file, so one file holds a whole closure and no cross-file pass is needed.

    A *hit* column keeps its own body instead of folding, so the true worst case is slightly
    above what this measures. The gap is small -- column bodies run ~119 chars at the median, so
    twenty hits add ~2,600 -- and it cannot be computed without a query, which a conformance rule
    does not have.
    """
    from governed_bi.corpus.store import load_file
    from governed_bi.serve.context import rendered_closure_chars

    bad: list[Finding] = []
    for path in paths:
        loaded, _ = load_file(path)
        tables = [a for a in loaded if type(a).__name__ == "TableAsset"]
        columns = [a for a in loaded if type(a).__name__ == "ColumnAsset"]
        for table in tables:
            cost, roster = rendered_closure_chars(table, columns)
            total = cost + roster
            if total > CLOSURE_CAP:
                bad.append(
                    Finding(
                        f"{where_of_file(path)}: renders {total:,} chars "
                        f"({cost:,} table + {roster:,} roster over {len(columns)} columns), "
                        f"cap {CLOSURE_CAP:,}"
                    )
                )
    return bad


def check_split_leak(assets, test_split: Path) -> list[Finding]:
    """V12 -- no asset quotes a held-out question."""
    questions = {
        " ".join(json.loads(line).get("question", "").lower().split())
        for line in test_split.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    questions.discard("")
    bad: list[Finding] = []
    for kind, a, path in assets:
        blob = " ".join(" ".join(str(a.get(f) or "").lower().split()) for f in ("summary", "body"))
        for q in questions:
            if len(q) > 25 and q in blob:
                bad.append(Finding(f"{where_of(kind, a, path)}: quotes a test-split question"))
                break
    return bad
