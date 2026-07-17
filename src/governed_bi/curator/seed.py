"""Deterministic seed pass: extract join/metric candidates from train gold SQL.

Runs before/under the deep-agent curator so the agent verifies rather than
invents. Pattern mirrors ``eval.arms`` sqlglot column walking.

BIRD gold SQL is heavily alias-qualified
(``FROM "schema"."RA" AS "T1" … ON "T1".x = "T2".y``). Candidates MUST use
physical table names so ``AssetBag.propose_join`` can look them up; emitting
aliases silently drops every seed join.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "x"


@dataclass(frozen=True)
class JoinCandidate:
    left_table: str
    right_table: str
    on: str
    source_sql: str


@dataclass(frozen=True)
class MetricCandidate:
    name: str
    base_table: str
    expression: str
    source_sql: str


@dataclass
class SeedBundle:
    joins: list[JoinCandidate] = field(default_factory=list)
    metrics: list[MetricCandidate] = field(default_factory=list)

    def render(self) -> str:
        lines: list[str] = ["## Deterministic seed candidates (verify, do not invent)"]
        if self.joins:
            lines.append("### Joins")
            for j in self.joins:
                lines.append(f"- {j.left_table} ⋈ {j.right_table} ON {j.on}")
        else:
            lines.append("### Joins\n(none extracted)")
        if self.metrics:
            lines.append("### Metrics")
            for m in self.metrics:
                lines.append(f"- {m.name}: {m.expression} on {m.base_table}")
        else:
            lines.append("### Metrics\n(none extracted)")
        return "\n".join(lines)


def _physical_name(node: exp.Table) -> str | None:
    """Bare physical table name (strip schema/catalog); never the alias."""
    name = node.name
    return name if name else None


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    """Map alias (and physical name) → physical table name for the whole query."""
    mapping: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        physical = _physical_name(table)
        if not physical:
            continue
        mapping[physical] = physical
        # sqlglot: ``table.alias_or_name`` is the alias when present, else name.
        alias = table.alias_or_name
        if alias:
            mapping[alias] = physical
    return mapping


def _resolve(name: str | None, aliases: dict[str, str]) -> str | None:
    if not name:
        return None
    return aliases.get(name, name)


def _eq_on_clause(
    predicate: exp.Expression, aliases: dict[str, str]
) -> tuple[str, str, str] | None:
    """Return ``(left_physical, right_physical, on_sql)`` for an equality ON."""
    if not isinstance(predicate, exp.EQ):
        return None
    left, right = predicate.left, predicate.right
    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
        return None
    if not left.table or not right.table:
        return None
    left_phys = _resolve(left.table, aliases)
    right_phys = _resolve(right.table, aliases)
    if not left_phys or not right_phys:
        return None
    on_sql = f"{left_phys}.{left.name} = {right_phys}.{right.name}"
    return left_phys, right_phys, on_sql


def extract_joins_from_sql(sql: str, *, dialect: str = "postgres") -> list[JoinCandidate]:
    """Pull ``JOIN … ON`` edges from one gold SQL string (physical names only)."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return []  # unparseable gold SQL is tolerated; a non-parse bug is not
    aliases = _alias_map(tree)
    out: list[JoinCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        resolved = _eq_on_clause(on, aliases)
        if resolved is None:
            continue
        left_name, right_name, on_sql = resolved
        # Prefer the JOIN's right table physical name when available.
        right_node = join.this
        if isinstance(right_node, exp.Table):
            right_name = _physical_name(right_node) or right_name
        key = (left_name, right_name, on_sql)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            JoinCandidate(
                left_table=left_name,
                right_table=right_name,
                on=on_sql,
                source_sql=sql,
            )
        )
    return out


def extract_metrics_from_sql(sql: str, *, dialect: str = "postgres") -> list[MetricCandidate]:
    """Pull simple aggregate expressions (SUM/AVG/COUNT/MIN/MAX) as metric seeds."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return []  # unparseable gold SQL is tolerated; a non-parse bug is not
    aliases = _alias_map(tree)
    tables = list(tree.find_all(exp.Table))
    if not tables:
        return []
    base = _physical_name(tables[0])
    if not base:
        return []
    out: list[MetricCandidate] = []
    seen: set[str] = set()
    for agg in tree.find_all(exp.AggFunc):
        # Rewrite alias-qualified columns in the aggregate to physical names.
        agg_copy = agg.copy()
        for col in agg_copy.find_all(exp.Column):
            if col.table:
                phys = _resolve(col.table, aliases)
                if phys:
                    col.set("table", phys)
        expr = agg_copy.sql(dialect=dialect)
        if expr in seen:
            continue
        seen.add(expr)
        name = _slug(expr)[:48]
        out.append(
            MetricCandidate(
                name=name,
                base_table=base,
                expression=expr,
                source_sql=sql,
            )
        )
    return out


def seed_from_train_sql(
    sqls: list[str], *, dialect: str = "postgres"
) -> SeedBundle:
    """Aggregate join/metric candidates across a batch of train gold SQLs."""
    bundle = SeedBundle()
    join_seen: set[tuple[str, str, str]] = set()
    metric_seen: set[str] = set()
    for sql in sqls:
        for j in extract_joins_from_sql(sql, dialect=dialect):
            key = (j.left_table, j.right_table, j.on)
            if key in join_seen:
                continue
            join_seen.add(key)
            bundle.joins.append(j)
        for m in extract_metrics_from_sql(sql, dialect=dialect):
            if m.expression in metric_seen:
                continue
            metric_seen.add(m.expression)
            bundle.metrics.append(m)
    return bundle
