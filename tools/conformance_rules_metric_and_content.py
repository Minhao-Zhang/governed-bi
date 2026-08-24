"""The conformance rules added on 2026-08-23, split out of ``check_corpus_conformance.py``.

Imported back into that module, which owns the ``RULES`` table and the report; these are the
predicates. The split is along a real line rather than at a line number: every rule here reuses
machinery from **elsewhere in the tree** -- ``sqlglot`` and ``govern/functions.py`` for the metric
rules, ``govern/guard.py`` for V21 -- where every older rule is a predicate over one YAML mapping.

**Not named ``check_*.py``.** ``tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation
.py`` globs ``tools/check_*.py`` as the inventory of gates, and a module of predicates with no
``main`` is not a gate. A file that looked like one would have to be declared manual with a reason
that does not exist.

| rule | what it asks | live findings on ../BIRD-corpus, measured 2026-08-23 |
|---|---|---|
| V17a | a metric expression parses **and** calls only permitted functions | 107 across 85 of 478 metrics |
| V17b | its identifiers resolve on ``base_table`` or through a declared join | 17 |
| V19  | no model-visible ``body`` names a governance-excluded column | 0 -- nothing is excluded yet |
| V21  | model-visible text passes ``guard``'s own encoding rule | 1 |
| V23  | one asset id, one file | 0 |

Three of the five have a live population, which is what separates them from rules written on a
hunch. The two at zero are there because their failure mode is *late*: V19's is a disclosure nobody
sees, and V23's is a ``ValueError`` in ``build_index`` after the commit.

**A zero was doing double duty, and that is worth naming.** V23 reported zero *and* could not be
keyed by the reporter, so a duplicate id would have reported zero as well. Two more rules were in
the same state -- V14 and V16, both file-level, both emitting a one-colon line the reporter dropped.
All three read zero on this corpus, so the zero was indistinguishable from blindness. Findings now
carry a two-part identity by construction and the reporter raises on any that does not, which is why
the blindness surfaced at all.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from governed_bi.register.assets import AssetType


class Finding(str):
    """One violation line. A ``str`` so the report can just sort them."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _where(kind: str, asset: dict[str, Any], path: Path) -> str:
    """``file:asset`` for a finding, and it must name an *asset* and never a kind.

    Inline columns carry no ``id`` in YAML -- the loader derives it -- so the label falls back to
    ``physical_name`` and then to ``name``. Both fallbacks are there because the alternative was
    measured: on a two-column table with neither field set, five findings collapsed to three
    identities, both columns reporting as ``t.yaml:column``.

    That costs more than a vague report. ``check_ratchet.py`` pins ``(rule, where)``, so two columns
    sharing one identity means fixing one while breaking the other moves no line in the pin file and
    the ratchet reports a hold on a tree that changed. A column has a stable name; there is no
    reason to key on its kind.
    """
    label = asset.get("id") or _column_label(kind, asset) or kind
    return f"{path.name}:{label}"


def _column_label(kind: str, asset: dict[str, Any]) -> str:
    if kind != "column":
        return ""
    return _text(asset.get("physical_name")) or _text(asset.get("name"))



SQL_DIALECT = "postgres"


def check_metric_expression(a: dict[str, Any], where: str) -> list[Finding]:
    """V17a: the expression parses as SQL.

    An expression is a SQL fragment the engine substitutes, and the corpus carries 28 of 478 that
    are not SQL at all -- ``DIVIDE(a, b)``, ``COUNT(x WHERE y)``, a literal ``<condition>``
    placeholder. Each one is a metric that cannot be used, and today nothing says so until a turn
    tries: the loader accepts any string.

    Parsed as a projection (``SELECT <expr>``) rather than bare, because ``sqlglot`` will happily
    read most fragments as a column reference on their own and give the answer "fine" to something
    that is not.

    **And parsing is not the whole rule.** ``DIVIDE(a, b)`` parses cleanly as an anonymous function
    call and names a function no dialect has, so the expression is checked against
    ``govern/functions.py::PERMITTED_FUNCTIONS`` -- the same allowlist ``govern/`` checks generated
    SQL against. Reused rather than restated: a corpus expression that passes here and fails there
    is a metric that fails at serve time, and two allowlists is how that happens.
    """
    expression = _text(a.get("expression"))
    if not expression:
        return [Finding(f"{where}: metric has no expression")]
    try:
        import sqlglot
    except ImportError:  # pragma: no cover - sqlglot is a declared dependency
        return []
    try:
        parsed = sqlglot.parse_one(f"SELECT {expression}", dialect=SQL_DIALECT)
    except Exception as err:  # noqa: BLE001 - any parse failure is the finding
        return [Finding(f"{where}: expression does not parse -- {type(err).__name__}: {err}")]
    if parsed is None:
        return [Finding(f"{where}: expression parses to nothing")]
    from sqlglot import exp

    if list(parsed.find_all(exp.Placeholder)):
        return [
            Finding(f"{where}: expression carries a placeholder rather than a definition")
        ]

    # Parsing is necessary and not sufficient. `DIVIDE(a, b)` and `MULTIPLY(x, y)` parse as
    # anonymous function calls in any dialect and exist in none this engine runs, so the
    # allowlist `govern/` checks *generated* SQL against is the same question asked once.
    from governed_bi.govern.functions import PERMITTED_FUNCTIONS, canonical_function_name

    out: list[Finding] = []
    for node in parsed.find_all(exp.Func):
        name = canonical_function_name(node)
        if name and name not in PERMITTED_FUNCTIONS:
            out.append(
                Finding(
                    f"{where}: expression calls {name!r}, which is not in the engine's permitted "
                    "function set -- govern/ would refuse the generated SQL"
                )
            )
    return out


def _identifiers_in(expression: str) -> set[str]:
    """Bare column names an expression reads. Qualified refs come back as ``table.column``."""
    try:
        import sqlglot
        from sqlglot import exp

        parsed = sqlglot.parse_one(f"SELECT {expression}", dialect=SQL_DIALECT)
    except Exception:  # noqa: BLE001 - V17a reports the parse failure; this rule stays silent
        return set()
    if parsed is None:
        return set()
    out: set[str] = set()
    for column in parsed.find_all(exp.Column):
        table = column.table
        out.add(f"{table}.{column.name}" if table else column.name)
    return out


def check_metric_bindings(assets: list[tuple[str, dict[str, Any], Path]]) -> list[Finding]:
    """V17b: every identifier resolves on ``base_table``, or on a table a declared join reaches.

    **And then the join must be declared**, which is the half that makes this a rule rather than a
    lint: an expression reading a column on another table is a query with an undeclared join in it,
    and the engine has no way to write that join. Measured on BIRD: 23 metrics reference 28 columns
    that are not on their base table -- 10 reachable through a join that exists, 18 reachable
    nowhere.

    Unqualified identifiers are checked against the base table only. A bare name that happens to
    exist on a joined table is *not* accepted: SQL would resolve it ambiguously or not at all, and
    accepting it here would bless an expression the warehouse rejects.
    """
    columns_of: dict[str, set[str]] = defaultdict(set)
    joins: dict[str, set[str]] = defaultdict(set)
    for kind, a, _ in assets:
        if kind == AssetType.table.value:
            table_id = _text(a.get("id"))
            for column in a.get("columns") or ():
                if isinstance(column, dict):
                    name = _text(column.get("name")) or _text(column.get("physical_name"))
                    if name:
                        columns_of[table_id].add(name.lower())
        elif kind == AssetType.column.value:
            table_id = _text(a.get("table"))
            name = _text(a.get("name")) or _text(a.get("physical_name"))
            if table_id and name:
                columns_of[table_id].add(name.lower())
        elif kind == AssetType.join.value:
            left, right = _text(a.get("left_table")), _text(a.get("right_table"))
            if left and right:
                joins[left].add(right)
                joins[right].add(left)

    out: list[Finding] = []
    for kind, a, path in assets:
        if kind != AssetType.metric.value:
            continue
        where = _where(kind, a, path)
        base = _text(a.get("base_table"))
        expression = _text(a.get("expression"))
        if not base or not expression:
            continue
        for reference in sorted(_identifiers_in(expression)):
            if "." in reference:
                table, _, column = reference.rpartition(".")
                # A qualified ref may name the base table by its short physical name.
                candidates = {table, f"{base.split('.')[0]}.{table}"} | (
                    {base} if table in (base, base.split(".")[-1]) else set()
                )
                reachable = {base} | joins.get(base, set())
                hit = next(
                    (c for c in candidates if c in reachable and column.lower() in columns_of[c]),
                    None,
                )
                if hit is None:
                    known = "unknown table" if not any(
                        c in columns_of for c in candidates
                    ) else "not reachable from base_table through a declared join"
                    out.append(
                        Finding(f"{where}: {reference} does not resolve -- {known}")
                    )
            elif reference.lower() not in columns_of.get(base, set()):
                elsewhere = sorted(
                    table for table, names in columns_of.items() if reference.lower() in names
                )
                hint = (
                    f"; it exists on {elsewhere[0]}, which needs a declared join and a qualified "
                    "reference"
                    if elsewhere
                    else "; it exists on no table in this corpus"
                )
                out.append(Finding(f"{where}: {reference} is not a column of {base}{hint}"))
    return out


def check_excluded_not_named(assets: list[tuple[str, dict[str, Any], Path]]) -> list[Finding]:
    """V19: no model-visible ``body`` names a ``governance.excluded`` column or asset.

    **`body`, not `summary`.** ``summary`` never reaches the prompt -- ``serve/context.py`` reads
    ``body`` -- it reaches the retrieval index. So a name in a ``summary`` is a routing signal and a
    name in a ``body`` is a disclosure, and conflating them would make the rule fire on the wrong
    field.

    ADR 0003 found exactly this: an asset naming an excluded column in text that was then injected
    verbatim into the SQL prompt, and concluded a content-scanning validator was the structural
    answer. None shipped. **Measured 2026-08-23: zero assets are excluded in either corpus**, so
    the rule has no population and cannot refuse a legitimate asset today -- which is precisely why
    adding it now is free.

    **V10 and V12 are not this control.** V10 forbids disclosing how an obfuscation decoy was made
    and V12 forbids quoting a held-out question; both police benchmark integrity, and on a
    production corpus they police nothing. V19 is the first rule of its kind here.
    """
    excluded: set[str] = set()
    for kind, a, _ in assets:
        governance = a.get("governance")
        if isinstance(governance, dict) and governance.get("excluded"):
            asset_id = _text(a.get("id"))
            if asset_id:
                excluded.add(asset_id)
                excluded.add(asset_id.rsplit(".", 1)[-1])
        if kind == AssetType.table.value:
            for column in a.get("columns") or ():
                if not isinstance(column, dict):
                    continue
                column_governance = column.get("governance")
                if isinstance(column_governance, dict) and column_governance.get("excluded"):
                    name = _text(column.get("name")) or _text(column.get("physical_name"))
                    if name:
                        excluded.add(name)
    if not excluded:
        return []

    out: list[Finding] = []
    for kind, a, path in assets:
        body = _text(a.get("body"))
        if not body:
            continue
        where = _where(kind, a, path)
        for name in sorted(excluded):
            if re.search(rf"\b{re.escape(name)}\b", body):
                out.append(
                    Finding(
                        f"{where}: body names {name!r}, which is governance-excluded. The column is "
                        "hidden and its name is not."
                    )
                )
    return out


def check_guard_rules(kind: str, a: dict[str, Any], where: str) -> list[Finding]:
    """V21: model-visible text passes ``govern/guard.py``'s own rules.

    **Reusing them, not restating them.** The rules that screen a reader's question are the rules
    that should screen text the corpus injects into the same prompt, and a second implementation
    here would be a second answer able to disagree with the first. The corpus is the *more*
    dangerous channel of the two: a question is one turn and an asset body is every turn that
    retrieves it.

    Measured: one finding, ``public_review_platform/few-shots/fs_public_review_platform_0012.yaml``,
    which ships two U+200B zero-width spaces.

    ``g_length`` is skipped: it is a cap on a reader's question and V13 already caps a body, so
    running it here would refuse a long asset for the wrong reason.
    """
    body = _text(a.get("body"))
    if not body:
        return []
    try:
        from governed_bi.govern.guard import has_control_characters
    except ImportError:  # pragma: no cover
        return []
    if has_control_characters(body):
        return [
            Finding(
                f"{where}: body carries a control or zero-width character that guard's g_encoding "
                "rule refuses on a reader's question"
            )
        ]
    return []


def check_unique_ids(assets: list[tuple[str, dict[str, Any], Path]]) -> list[Finding]:
    """V23: one id, one file.

    A duplicate passes every other rule here, loads with zero problems, and then raises
    ``ValueError: duplicate index id`` in ``build_index`` -- **after** the commit. Measured, and it
    is exactly what ``corpus/store.py::write`` produces on an existing id, which is why
    ``corpus/patch.py`` exists and why a bundle is a diff rather than a file copy.

    **One finding per file, keyed like every other rule.** This used to emit one line per *id*,
    beginning with the id rather than the file, and the reporter could not key it: ``_where_of``
    wants ``file:asset`` and a POSIX path in the message yields no third field, so the finding was
    dropped from the JSON and the ratchet was blind to the one failure mode that lands *after* the
    commit. On Windows the drive letter supplied the missing colon, so the identity became
    ``"an_id: declared in 2 files -- C"`` -- present, and carrying the file *count*, so a third
    duplicate read as one closure plus one new finding. Per file, both go away.
    """
    seen: dict[str, list[Path]] = defaultdict(list)
    for _, a, path in assets:
        asset_id = _text(a.get("id"))
        if asset_id:
            seen[asset_id].append(path)
    return [
        Finding(
            f"{path.name}:{asset_id}: this id is also declared in "
            f"{', '.join(other.name for other in sorted(set(paths) - {path}))}. "
            "build_index raises on this."
        )
        for asset_id, paths in sorted(seen.items())
        if len(paths) > 1
        for path in sorted(set(paths))
    ]
