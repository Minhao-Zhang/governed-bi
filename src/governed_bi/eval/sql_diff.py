"""Structured comparison of a generated SQL statement against its gold.

Execution accuracy answers one bit — did the rows match — and that bit is the
only thing the grader can be trusted on. But a run of 2030 wrong answers with no
further structure is what produced a report whose per-stage estimates were off by
an order of magnitude: every failure landed in one undifferentiated
"right schema, wrong SQL" bucket covering 45.8% of the benchmark (RETIRED figure;
see docs/measurement.md).

This module splits that bucket. It parses both statements, resolves every column
reference through its lexical scope, and reports a verdict *per dimension* —
tables, projection, joins, filters, aggregation, grouping, ordering — rather than
one blended score. The dimensions are deliberately not collapsed into a single
"error type": a query is routinely wrong along several at once, and a taxonomy
that forces one label per query is what makes per-class headroom look additive
when it is not.

Two properties are load-bearing:

**Columns stay table-qualified.** ``customers.name`` and ``orders.name`` are
different mistakes. A comparison on bare lowercased names cannot tell "we read the
right column from the wrong table" from "we read the wrong column", which are the
two most common failures and have nothing to do with each other.

**Aliases are resolved lexically, not textually.** Gold SQL writes ``T1.party``
where a model may write ``congress.party``; both name the same column and must
compare equal. The resolution walks enclosing scopes (:func:`_binding`), so a
correlated subquery qualifying a column with an outer alias resolves correctly and
a reused alias in a nested query does not cross-attribute.

Pure and offline: no database, no model, no settings. It runs over an archived
``generations.*.jsonl`` as happily as over a live row, which is the point — the
diagnosis can be recomputed on old runs when the taxonomy improves.

CLI::

    uv run python -m governed_bi.eval.sql_diff runs/datalake/<ts> \\
      --bird-dir ../BIRD-Data-Obfuscation --arm curated_sme
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "Dimension",
    "Verdict",
    "DimensionDiff",
    "SqlFeatures",
    "SqlDiff",
    "diff_sql",
    "extract_features",
    "is_frozen_constant",
    "main",
]

#: When a filter leaves at most this many rows, the CLI includes per-row diffs
#: alongside the aggregate incidence. Larger sets stay aggregate-only.
_PER_ROW_CAP = 20


class Dimension(str, Enum):
    """One comparable aspect of a SQL statement.

    Ordered roughly outside-in: which data was reached, then what was done to it.
    :mod:`governed_bi.eval.error_taxonomy` maps these onto pipeline stages; the
    split here is purely syntactic and takes no view on blame.
    """

    schema_set = "schema_set"
    table_set = "table_set"
    join_graph = "join_graph"
    join_keys = "join_keys"
    join_type = "join_type"
    projection = "projection"
    filter_columns = "filter_columns"
    filter_literals = "filter_literals"
    aggregation = "aggregation"
    group_by = "group_by"
    order_limit = "order_limit"
    distinct = "distinct"
    set_ops = "set_ops"


class Verdict(str, Enum):
    """Outcome of comparing one dimension.

    ``unknown`` is not a polite ``match``. It means the comparison could not be
    made — unparseable SQL, or a scope walk that yielded no bindings — and the
    caller must keep it out of both the numerator and the denominator. Folding it
    into ``match`` is how a parser gap starts reading as model competence.
    """

    match = "match"
    mismatch = "mismatch"
    unknown = "unknown"


@dataclass(frozen=True)
class DimensionDiff:
    """Verdict for one dimension, with the elements that differ.

    ``missing`` is in gold but not generated; ``extra`` is generated but not in
    gold. Keeping both directions separate is what makes a fix legible: models
    that over-project need a different instruction from models that under-project,
    and a symmetric "these differ" cannot tell you which you have.
    """

    dimension: Dimension
    verdict: Verdict
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    #: Set to True when ``missing`` and ``extra`` are both empty but the ordered
    #: sequences differ — the elements are right and only their order is wrong.
    #: BIRD's strict grader is column-order sensitive, so this is a real failure,
    #: but a far cheaper one to fix than a wrong column set.
    order_only: bool = False

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.match


@dataclass(frozen=True)
class SqlFeatures:
    """Everything :func:`diff_sql` compares, extracted from one statement."""

    parsed: bool
    scoped: bool  # False when alias resolution was unavailable (see _column_map)
    schemas: frozenset[str] = frozenset()
    tables: frozenset[str] = frozenset()
    join_edges: frozenset[frozenset[str]] = frozenset()
    join_keys: frozenset[frozenset[str]] = frozenset()
    join_types: tuple[tuple[str, int], ...] = ()
    projection: tuple[str, ...] = ()
    filter_columns: frozenset[str] = frozenset()
    filter_literals: frozenset[str] = frozenset()
    aggregations: tuple[tuple[str, int], ...] = ()
    group_keys: frozenset[str] = frozenset()
    order_keys: tuple[str, ...] = ()
    limit: str | None = None
    distinct: bool = False
    set_ops: tuple[tuple[str, int], ...] = ()


@dataclass
class SqlDiff:
    """Per-dimension comparison of one generated statement against its gold."""

    dimensions: dict[Dimension, DimensionDiff] = field(default_factory=dict)
    gen_parsed: bool = False
    gold_parsed: bool = False
    gold_frozen: bool = False

    def mismatched(self) -> list[Dimension]:
        """Dimensions that genuinely differ, in :class:`Dimension` order."""
        return [d for d in Dimension if self.dimensions.get(d, _UNKNOWN[d]).verdict is Verdict.mismatch]

    def unknown(self) -> list[Dimension]:
        return [d for d in Dimension if self.dimensions.get(d, _UNKNOWN[d]).verdict is Verdict.unknown]

    def comparable(self) -> bool:
        """True when both statements parsed and a real comparison happened."""
        return self.gen_parsed and self.gold_parsed and not self.gold_frozen

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form for a generations row or a summary artifact."""
        return {
            "gen_parsed": self.gen_parsed,
            "gold_parsed": self.gold_parsed,
            "gold_frozen": self.gold_frozen,
            "mismatched": [d.value for d in self.mismatched()],
            "unknown": [d.value for d in self.unknown()],
            "detail": {
                d.value: {
                    "verdict": diff.verdict.value,
                    "missing": list(diff.missing),
                    "extra": list(diff.extra),
                    **({"order_only": True} if diff.order_only else {}),
                }
                for d, diff in self.dimensions.items()
                if diff.verdict is not Verdict.match
            },
        }


_UNKNOWN: dict[Dimension, DimensionDiff] = {
    d: DimensionDiff(d, Verdict.unknown) for d in Dimension
}


# --------------------------------------------------------------------------- #
# Gold shapes that cannot be compared
# --------------------------------------------------------------------------- #

#: A gold answer of the form ``SELECT ... FROM (VALUES ('x'), ('y'))`` is a frozen
#: literal constant: the benchmark hardcoded the expected rows rather than deriving
#: them from the database. No generator can reach it from schema, so counting these
#: as SQL-generation failures inflates every error class at once. Detected here and
#: excluded by the taxonomy's denominator.
_FROZEN_GOLD_RE = re.compile(r"\bVALUES\s*\(", re.IGNORECASE)


def is_frozen_constant(sql: str | None) -> bool:
    """True when the gold SQL hardcodes its answer rather than querying for it."""
    return bool(sql and _FROZEN_GOLD_RE.search(sql))


# --------------------------------------------------------------------------- #
# Feature extraction
# --------------------------------------------------------------------------- #


def _parse(sql: str | None, dialect: str):
    """Parsed statement, or ``None`` when absent, refused, or unparseable."""
    if not sql:
        return None
    text = sql.strip()
    if not text or text.upper() == "REFUSED":
        return None
    import sqlglot

    try:
        return sqlglot.parse_one(text, read=dialect)
    except Exception:  # noqa: BLE001 — sqlglot raises several unrelated types
        return None


def _binding(scope: Any, qualifier: str) -> Any | None:
    """Innermost lexical binding for a column qualifier, or None if undeclared.

    Walks up the enclosing scopes because a correlated subquery may legitimately
    qualify a column with an alias declared by the outer query.
    """
    want = qualifier.lower()
    current = scope
    while current is not None:
        for name, source in current.sources.items():
            if name.lower() == want:
                return source
        current = current.parent
    return None


def _column_map(tree) -> tuple[dict[int, str], bool]:
    """Map each column node to ``table.column``, and whether scopes resolved.

    The boolean is the honesty flag. When ``traverse_scope`` yields nothing there
    is no trustworthy alias map, so every column degrades to ``?.name`` and the
    caller must treat table-sensitive dimensions as :attr:`Verdict.unknown` rather
    than compare names that may belong to different tables.
    """
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope

    out: dict[int, str] = {}
    try:
        scopes = traverse_scope(tree)
    except Exception:  # noqa: BLE001 — scope building is best-effort
        scopes = []

    if not scopes:
        for col in tree.find_all(exp.Column):
            if not isinstance(col.this, exp.Star):
                out[id(col)] = f"?.{col.name.lower()}"
        return out, False

    for scope in scopes:
        # ``Scope.find_all`` stops at nested-scope boundaries, so each column node
        # is claimed by exactly one scope and resolved against the aliases actually
        # visible where it was written.
        table_sources = [s for s in scope.sources.values() if isinstance(s, exp.Table)]
        for col in scope.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                continue  # ``t.*`` names no single column to attribute
            table: str | None = None
            qualifier = col.table
            if qualifier:
                source = _binding(scope, qualifier)
                if isinstance(source, exp.Table):
                    table = source.name.lower()
                elif source is not None:
                    # A subquery/CTE alias: keep the alias itself. It is stable
                    # across both statements only if both name it the same, so this
                    # is weaker than a real table, but better than dropping it.
                    table = qualifier.lower()
            elif len(scope.sources) == 1 and table_sources:
                # Unqualified column in a single-source scope is unambiguous. Gold
                # SQL often aliases where a model does not; without this, every
                # single-table query would compare as a table mismatch.
                table = table_sources[0].name.lower()
            out[id(col)] = f"{table or '?'}.{col.name.lower()}"
    return out, True


def _resolved(node, colmap: dict[int, str]) -> set[str]:
    """Resolved ``table.column`` names referenced anywhere under ``node``."""
    from sqlglot import exp

    return {
        colmap[id(c)]
        for c in node.find_all(exp.Column)
        if id(c) in colmap
    }


def _literal_text(node) -> str:
    """A literal's value, normalised so casing and quoting do not create noise.

    Deliberately case-folded: ``'ARECIBO'`` vs ``'Arecibo'`` is a real and common
    failure, but it is a *value* error, not a structural one. Folding here keeps the
    structural diff from firing on every casing difference and drowning the structural
    signal; the row still lands in ``ErrorClass.value_level``, and
    ``Attribution.result_shape`` says whether that value returned nothing or returned
    gold's row count with different contents.
    """
    return str(node.this).strip().lower()


def extract_features(sql: str | None, *, dialect: str = "postgres") -> SqlFeatures:
    """Everything comparable about one statement. Never raises."""
    from sqlglot import exp

    tree = _parse(sql, dialect)
    if tree is None:
        return SqlFeatures(parsed=False, scoped=False)

    colmap, scoped = _column_map(tree)

    schemas: set[str] = set()
    tables: set[str] = set()
    for node in tree.find_all(exp.Table):
        if node.name:
            tables.add(node.name.lower())
            if node.db:
                schemas.add(node.db.lower())

    # Joins as unordered key pairs. Representing the graph by its ON-equalities
    # rather than by left/right nesting makes it insensitive to the join order the
    # model happened to choose, which is not an error when the result is the same.
    join_keys: set[frozenset[str]] = set()
    join_types: Counter = Counter()
    for join in tree.find_all(exp.Join):
        join_types[(join.side or join.kind or "INNER").upper()] += 1
        on = join.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            left, right = eq.this, eq.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                a, b = colmap.get(id(left)), colmap.get(id(right))
                if a and b and a != b:
                    join_keys.add(frozenset({a, b}))
    join_edges = {
        frozenset(name.split(".", 1)[0] for name in pair) for pair in join_keys
    }
    join_edges = {e for e in join_edges if len(e) == 2}

    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    projection: list[str] = []
    distinct = False
    if select is not None:
        distinct = bool(select.args.get("distinct"))
        for expression in select.expressions:
            target = expression.unalias() if hasattr(expression, "unalias") else expression
            if isinstance(target, exp.Column) and id(target) in colmap:
                projection.append(colmap[id(target)])
            else:
                # An expression, not a bare column: fold to its function shape plus
                # the columns it touches, so COUNT(a) and COUNT(b) differ but
                # COUNT(t1.a) and COUNT(t2.a) also differ.
                inner = sorted(_resolved(target, colmap))
                projection.append(f"{type(target).__name__.lower()}({','.join(inner)})")

    filter_columns: set[str] = set()
    filter_literals: set[str] = set()
    for clause_type in (exp.Where, exp.Having):
        for clause in tree.find_all(clause_type):
            filter_columns |= _resolved(clause, colmap)
            for lit in clause.find_all(exp.Literal):
                filter_literals.add(_literal_text(lit))

    aggregations: Counter = Counter()
    for fn in (exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Count):
        for node in tree.find_all(fn):
            inner = sorted(_resolved(node, colmap))
            aggregations[f"{fn.__name__.lower()}({','.join(inner)})"] += 1

    group_keys: set[str] = set()
    for group in tree.find_all(exp.Group):
        group_keys |= _resolved(group, colmap)

    order_keys: list[str] = []
    for order in tree.find_all(exp.Order):
        for ordered in order.expressions:
            names = sorted(_resolved(ordered, colmap)) or [ordered.sql().lower()]
            direction = "desc" if ordered.args.get("desc") else "asc"
            order_keys.append(f"{','.join(names)}:{direction}")

    limit_node = tree.find(exp.Limit)
    limit = (
        limit_node.expression.sql().strip().lower()
        if limit_node is not None and limit_node.expression is not None
        else None
    )

    set_ops: Counter = Counter()
    for op in (exp.Union, exp.Intersect, exp.Except):
        count = len(list(tree.find_all(op)))
        if count:
            set_ops[op.__name__.lower()] = count

    return SqlFeatures(
        parsed=True,
        scoped=scoped,
        schemas=frozenset(schemas),
        tables=frozenset(tables),
        join_edges=frozenset(join_edges),
        join_keys=frozenset(join_keys),
        join_types=tuple(sorted(join_types.items())),
        projection=tuple(projection),
        filter_columns=frozenset(filter_columns),
        filter_literals=frozenset(filter_literals),
        aggregations=tuple(sorted(aggregations.items())),
        group_keys=frozenset(group_keys),
        order_keys=tuple(order_keys),
        limit=limit,
        distinct=distinct,
        set_ops=tuple(sorted(set_ops.items())),
    )


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def _set_diff(dim: Dimension, gold: Iterable, gen: Iterable) -> DimensionDiff:
    gold_set, gen_set = set(gold), set(gen)
    missing = gold_set - gen_set
    extra = gen_set - gold_set
    if not missing and not extra:
        return DimensionDiff(dim, Verdict.match)
    return DimensionDiff(
        dim,
        Verdict.mismatch,
        missing=tuple(sorted(_render_diff_value(m) for m in missing)),
        extra=tuple(sorted(_render_diff_value(e) for e in extra)),
    )


def _render_diff_value(value) -> str:
    """Stable text for a diff element, including the frozenset join-key pairs."""
    if isinstance(value, frozenset):
        return "=".join(sorted(str(v) for v in value))
    if isinstance(value, tuple):
        return ":".join(str(v) for v in value)
    return str(value)


def _seq_diff(dim: Dimension, gold: tuple, gen: tuple) -> DimensionDiff:
    """Ordered comparison that distinguishes a wrong set from a wrong order."""
    if gold == gen:
        return DimensionDiff(dim, Verdict.match)
    gold_set, gen_set = set(gold), set(gen)
    if gold_set == gen_set and len(gold) == len(gen):
        return DimensionDiff(dim, Verdict.mismatch, order_only=True)
    return DimensionDiff(
        dim,
        Verdict.mismatch,
        missing=tuple(sorted(_render_diff_value(m) for m in gold_set - gen_set)),
        extra=tuple(sorted(_render_diff_value(e) for e in gen_set - gold_set)),
    )


def _flag_diff(dim: Dimension, gold: Any, gen: Any) -> DimensionDiff:
    if gold == gen:
        return DimensionDiff(dim, Verdict.match)
    return DimensionDiff(
        dim,
        Verdict.mismatch,
        missing=(_render_diff_value(gold),) if gold not in (None, False) else (),
        extra=(_render_diff_value(gen),) if gen not in (None, False) else (),
    )


#: Dimensions whose elements carry a table qualifier, and which therefore cannot
#: be trusted when either side's aliases failed to resolve.
_TABLE_SENSITIVE: frozenset[Dimension] = frozenset(
    {
        Dimension.join_graph,
        Dimension.join_keys,
        Dimension.projection,
        Dimension.filter_columns,
        Dimension.aggregation,
        Dimension.group_by,
        Dimension.order_limit,
    }
)


def diff_sql(
    generated: str | None,
    gold: str | None,
    *,
    dialect: str = "postgres",
) -> SqlDiff:
    """Compare a generated statement against its gold, dimension by dimension.

    Never raises: a statement that will not parse yields a diff whose every
    dimension is :attr:`Verdict.unknown`, with ``gen_parsed``/``gold_parsed``
    recording which side failed. Callers report those separately rather than
    charging them to a SQL-construction error class.
    """
    gold_frozen = is_frozen_constant(gold)
    gold_features = extract_features(gold, dialect=dialect)
    gen_features = extract_features(generated, dialect=dialect)

    diff = SqlDiff(
        gen_parsed=gen_features.parsed,
        gold_parsed=gold_features.parsed,
        gold_frozen=gold_frozen,
    )
    if not (gold_features.parsed and gen_features.parsed) or gold_frozen:
        diff.dimensions = dict(_UNKNOWN)
        return diff

    resolved = gold_features.scoped and gen_features.scoped
    computed: dict[Dimension, DimensionDiff] = {
        Dimension.schema_set: _set_diff(
            Dimension.schema_set, gold_features.schemas, gen_features.schemas
        ),
        Dimension.table_set: _set_diff(
            Dimension.table_set, gold_features.tables, gen_features.tables
        ),
        Dimension.join_graph: _set_diff(
            Dimension.join_graph, gold_features.join_edges, gen_features.join_edges
        ),
        Dimension.join_keys: _set_diff(
            Dimension.join_keys, gold_features.join_keys, gen_features.join_keys
        ),
        Dimension.join_type: _set_diff(
            Dimension.join_type, gold_features.join_types, gen_features.join_types
        ),
        Dimension.projection: _seq_diff(
            Dimension.projection, gold_features.projection, gen_features.projection
        ),
        Dimension.filter_columns: _set_diff(
            Dimension.filter_columns,
            gold_features.filter_columns,
            gen_features.filter_columns,
        ),
        Dimension.filter_literals: _set_diff(
            Dimension.filter_literals,
            gold_features.filter_literals,
            gen_features.filter_literals,
        ),
        Dimension.aggregation: _set_diff(
            Dimension.aggregation, gold_features.aggregations, gen_features.aggregations
        ),
        Dimension.group_by: _set_diff(
            Dimension.group_by, gold_features.group_keys, gen_features.group_keys
        ),
        Dimension.order_limit: _seq_diff(
            Dimension.order_limit,
            (*gold_features.order_keys, f"limit:{gold_features.limit}"),
            (*gen_features.order_keys, f"limit:{gen_features.limit}"),
        ),
        Dimension.distinct: _flag_diff(
            Dimension.distinct, gold_features.distinct, gen_features.distinct
        ),
        Dimension.set_ops: _set_diff(
            Dimension.set_ops, gold_features.set_ops, gen_features.set_ops
        ),
    }

    if not resolved:
        # Alias resolution failed on at least one side. Names are still comparable
        # where they carry no table qualifier; where they do, a match would be luck
        # and a mismatch would be an artifact.
        for dim in _TABLE_SENSITIVE:
            computed[dim] = DimensionDiff(dim, Verdict.unknown)

    diff.dimensions = computed
    return diff


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _resolve_split(
    arms: dict[str, list[dict[str, Any]]],
    *,
    run_dir: Path,
    split: str | None,
) -> str:
    """Same refusal rules as :func:`governed_bi.eval.analysis.analyse_run`."""
    if split is not None:
        return split
    recorded = {r.get("split") for rows in arms.values() for r in rows}
    splits = recorded - {None}
    if len(splits) > 1:
        raise RuntimeError(f"{run_dir} mixes splits {sorted(map(str, splits))}")
    if not splits:
        raise RuntimeError(
            f"{run_dir} records no split on any row (empty, or predating the "
            "field); pass --split explicitly instead of letting the gold file "
            "be guessed"
        )
    if None in recorded:
        raise RuntimeError(
            f"{run_dir} mixes rows recording split {next(iter(splits))!r} with "
            "rows recording no split at all; the unlabelled rows may be from "
            "another split. Pass --split explicitly to assert they are not."
        )
    return splits.pop()  # type: ignore[return-value]


def _filter_rows(
    rows: Iterable[dict[str, Any]],
    *,
    db: str | None,
    question_id: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if db is not None and str(row.get("db_id") or "") != db:
            continue
        if question_id is not None and str(
            row.get("question_id") or row.get("request_id") or ""
        ) != question_id:
            continue
        out.append(row)
    return out


def report_run(
    run_dir: Path | str,
    *,
    bird_dir: Path | str,
    split: str | None = None,
    arm: str | None = None,
    db: str | None = None,
    question_id: str | None = None,
    dialect: str = "postgres",
) -> dict[str, Any]:
    """Diff filtered generations against gold; return a JSON-safe report."""
    # Lazy: analysis imports this module at top level.
    from .analysis import load_arm_rows, load_gold_sql

    run_dir = Path(run_dir)
    arms = load_arm_rows(run_dir)
    if not arms:
        raise FileNotFoundError(f"no generations.*.jsonl under {run_dir}")
    if arm is not None:
        if arm not in arms:
            raise SystemExit(
                f"unknown arm {arm!r}; available: {sorted(arms)}"
            )
        arms = {arm: arms[arm]}

    split = _resolve_split(arms, run_dir=run_dir, split=split)
    gold_sql = load_gold_sql(bird_dir, split=split)
    qids = {str(r.get("question_id")) for rows in arms.values() for r in rows}
    if qids and not qids & gold_sql.keys():
        raise RuntimeError(
            f"none of the {len(qids)} question ids under {run_dir} appear in the "
            f"{split!r} gold file; wrong --split or wrong --bird-dir"
        )

    include_rows = question_id is not None
    out: dict[str, Any] = {
        "run_dir": str(run_dir),
        "split": split,
        "filters": {
            "arm": arm,
            "db": db,
            "question_id": question_id,
        },
        "arms": {},
    }
    for name, rows in arms.items():
        filtered = _filter_rows(rows, db=db, question_id=question_id)
        incidence: Counter = Counter()
        row_payloads: list[dict[str, Any]] = []
        n_comparable = 0
        for row in filtered:
            qid = str(row.get("question_id") or row.get("request_id") or "")
            gold = gold_sql.get(qid) or None
            diff = diff_sql(row.get("generated_sql"), gold, dialect=dialect)
            if diff.comparable():
                n_comparable += 1
            for dim in diff.mismatched():
                incidence[dim.value] += 1
            row_payloads.append(
                {
                    "question_id": qid,
                    "db_id": row.get("db_id"),
                    "correct": bool(row.get("correct")),
                    "diff": diff.to_dict(),
                }
            )
        arm_out: dict[str, Any] = {
            "n": len(filtered),
            "n_comparable": n_comparable,
            "dimension_incidence": dict(incidence.most_common()),
        }
        if include_rows or len(row_payloads) <= _PER_ROW_CAP:
            arm_out["rows"] = row_payloads
        out["arms"][name] = arm_out
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("run_dir", type=Path, help="A runs/datalake/<timestamp> directory")
    p.add_argument("--bird-dir", type=Path, default=Path("../BIRD-Data-Obfuscation"))
    p.add_argument(
        "--split",
        default=None,
        help="Override the split recorded in rows; required if no row records one",
    )
    p.add_argument("--arm", default=None, help="Restrict to one arm (default: all)")
    p.add_argument("--db", default=None, help="Restrict to one db_id")
    p.add_argument(
        "--question-id",
        default=None,
        help="Restrict to one question_id (also emits per-row diffs)",
    )
    p.add_argument("--out", type=Path, default=None, help="Write JSON here as well")
    args = p.parse_args(argv)

    report = report_run(
        args.run_dir,
        bird_dir=args.bird_dir,
        split=args.split,
        arm=args.arm,
        db=args.db,
        question_id=args.question_id,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
