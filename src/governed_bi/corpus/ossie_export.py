"""Write a corpus as Ossie semantic models, and read the result back. See ADR 0020.

One model per database, one report for what did not map. Only this module knows Ossie; a spec change
touches it alone. Run: ``python -m governed_bi.corpus.ossie_export --corpus-dir DIR --out DIR``.
Ossie relationships are many to one with no cardinality, so cardinality lives in the extension.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from .identity import derive_column_id, table_id
from .parse import from_mapping, to_mapping
from .schema import (
    Asset,
    Cardinality,
    ColumnAsset,
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    NegativeExampleAsset,
    SchemaAsset,
    TableAsset,
    TermAsset,
)
from .store import load

__all__ = [
    "OSSIE_COMMIT",
    "OSSIE_VERSION",
    "VENDOR",
    "DatabaseReport",
    "export_assets",
    "export_corpus",
    "main",
    "read_model",
    "read_model_file",
    "round_trip_loss",
]

OSSIE_VERSION = "0.2.0.dev0"
OSSIE_COMMIT = "53418ba"
VENDOR = "GOVERNED_BI"
REPORT_NAME = "export-report.json"

_LOGICAL_TO_OSSIE = {
    "string": "String",
    "integer": "Integer",
    "decimal": "Decimal",
    "date": "Date",
    "datetime": "DateTime",
    "boolean": "Boolean",
}
_OSSIE_TO_LOGICAL = {
    "String": "string",
    "Integer": "integer",
    "Decimal": "decimal",
    "Float": "decimal",
    "Date": "date",
    "DateTime": "datetime",
    "DateTimeTz": "datetime",
    "Boolean": "boolean",
}
_FLOAT_PHYSICAL = ("real", "float", "double")

#: Keys the mapping writes natively. Everything else on an asset goes to its extension.
_NATIVE: dict[str, frozenset[str]] = {
    "schema": frozenset({"asset_type", "name", "summary", "body", "rules"}),
    "table": frozenset({"asset_type", "schema", "physical_name", "summary", "body", "rules", "columns"}),
    "column": frozenset(
        {
            "asset_type",
            "id",
            "schema",
            "parent_table",
            "physical_name",
            "summary",
            "body",
            "physical_type",
            "logical_type",
        }
    ),
    "join": frozenset({"asset_type", "left_table", "right_table", "on", "summary", "body"}),
    "metric": frozenset({"asset_type", "id", "expression", "summary", "body"}),
}
_FEW_SHOT_NATIVE = frozenset({"asset_type", "id", "schema", "sql", "summary", "body"})


@dataclass
class DatabaseReport:
    """What one database exported and what it could not map."""

    datasets: int = 0
    fields: int = 0
    relationships: int = 0
    many_to_many: int = 0
    without_cardinality: list[str] = field(default_factory=list)
    unmapped_joins: dict[str, str] = field(default_factory=dict)
    metrics: int = 0
    unparsed_metrics: list[str] = field(default_factory=list)
    terms: int = 0
    unresolved_bindings: list[str] = field(default_factory=list)
    verified_queries: int = 0
    unanswerable: int = 0


def _text(summary: str, body: str | None) -> str:
    return summary if not body else f"{summary}\n\n{body}"


def _split_text(text: str) -> tuple[str, str | None]:
    summary, sep, body = text.partition("\n\n")
    return summary, (body if sep else None)


def _extension(data: Mapping[str, Any]) -> list[dict[str, str]]:
    if not data:
        return []
    return [{"vendor_name": VENDOR, "data": json.dumps(data, sort_keys=True, ensure_ascii=False)}]


def _extension_data(entity: Mapping[str, Any]) -> dict[str, Any]:
    for item in entity.get("custom_extensions") or []:
        if item.get("vendor_name") == VENDOR:
            loaded = json.loads(item["data"])
            if isinstance(loaded, dict):
                return loaded
    return {}


def _extras(asset: Asset, kind: str) -> dict[str, Any]:
    native = _NATIVE[kind]
    return {k: v for k, v in to_mapping(asset).items() if k not in native}


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    if value:
        target[key] = value


def _ai_context(instructions: str | None, synonyms: Sequence[str]) -> dict[str, Any] | None:
    context: dict[str, Any] = {}
    _put(context, "instructions", instructions)
    _put(context, "synonyms", list(dict.fromkeys(synonyms)))
    return context or None


def _identifier(name: str) -> str:
    return exp.to_identifier(name).sql(dialect="postgres")


def _datatype(column: ColumnAsset) -> str | None:
    if column.logical_type is None:
        return None
    physical = (column.physical_type or "").casefold()
    if column.logical_type.value == "decimal" and physical.startswith(_FLOAT_PHYSICAL):
        return "Float"
    if column.logical_type.value == "datetime" and "with time zone" in physical:
        return "DateTimeTz"
    return _LOGICAL_TO_OSSIE[column.logical_type.value]


def parse_on(on: str, left: str, right: str) -> list[tuple[str, str]] | str:
    """Column pairs ``(left column, right column)`` of an AND of equalities, or the reason it is not one."""
    try:
        tree = sqlglot.parse_one(on, dialect="postgres")
    except Exception as err:  # noqa: BLE001 - any parse failure is a reported reason
        return f"on does not parse: {type(err).__name__}"
    pairs: list[tuple[str, str]] = []
    for part in tree.flatten() if isinstance(tree, exp.And) else [tree]:
        if (
            not isinstance(part, exp.EQ)
            or not isinstance(part.this, exp.Column)
            or not isinstance(part.expression, exp.Column)
        ):
            return "on is not an AND of column equalities"
        a, b = part.this, part.expression
        sides = (a.table.casefold(), b.table.casefold())
        lo, ro = left.casefold(), right.casefold()
        if lo == ro or sides == (lo, ro):
            pairs.append((a.name, b.name))
        elif sides == (ro, lo):
            pairs.append((b.name, a.name))
        else:
            return "a column in on names neither table of the join"
    return pairs


def _expression_parses(expression: str) -> bool:
    """The same two attempts as ``validate.py``: the expression alone, then after ``SELECT``."""
    for text in (expression, f"SELECT {expression}"):
        try:
            sqlglot.parse_one(text)
            return True
        except Exception:  # noqa: BLE001, S112 - any parse failure counts as unparsed
            continue
    return False


def _owner(asset: Asset, schemas: set[str], table_schema: Mapping[str, str]) -> str | None:
    """The database an asset belongs to, or ``None`` when no rule places it."""
    if isinstance(asset, SchemaAsset):
        return asset.name
    if isinstance(asset, JoinAsset):
        return table_schema.get(asset.left_table)
    if isinstance(asset, MetricAsset):
        return table_schema.get(asset.base_table)
    if isinstance(asset, TermAsset):
        if asset.binding and asset.binding.target_id.split(".")[0] in schemas:
            return asset.binding.target_id.split(".")[0]
        for name in sorted(schemas, key=len, reverse=True):
            if asset.id.startswith(f"term_{name}_"):
                return name
        return None
    schema = getattr(asset, "schema", None)
    return schema if isinstance(schema, str) else None


def export_assets(assets: Sequence[Asset]) -> tuple[dict[str, dict[str, Any]], dict[str, DatabaseReport], list[str]]:
    """Models by database name, a report per database, and the ids of assets no database claims."""
    schemas = {a.name for a in assets if isinstance(a, SchemaAsset)}
    table_schema = {a.id: a.schema for a in assets if isinstance(a, TableAsset)}
    groups: dict[str, list[Asset]] = defaultdict(list)
    unplaced: list[str] = []
    for asset in assets:
        owner = _owner(asset, schemas, table_schema)
        if owner in schemas:
            groups[str(owner)].append(asset)
        else:
            unplaced.append(asset.id)
    models: dict[str, dict[str, Any]] = {}
    reports: dict[str, DatabaseReport] = {}
    for name in sorted(schemas):
        models[name], reports[name] = _export_database(name, groups[name])
    return models, reports, unplaced


def _export_database(name: str, assets: Sequence[Asset]) -> tuple[dict[str, Any], DatabaseReport]:
    report = DatabaseReport()
    schema = next(a for a in assets if isinstance(a, SchemaAsset))
    tables = [a for a in assets if isinstance(a, TableAsset)]
    columns = {a.id: a for a in assets if isinstance(a, ColumnAsset)}
    by_table = {t.id: t for t in tables}

    synonyms: dict[tuple[str, str], list[str]] = defaultdict(list)
    for term in (a for a in assets if isinstance(a, TermAsset)):
        report.terms += 1
        binding = term.binding
        if binding is None:
            continue
        known = (
            binding.target_id in columns
            or binding.target_id in by_table
            or binding.target_id == schema.id
            or any(m.id == binding.target_id for m in assets if isinstance(m, MetricAsset))
        )
        if known:
            synonyms[(binding.target_type.value, binding.target_id)].extend(term.synonyms)
        else:
            report.unresolved_bindings.append(term.id)

    datasets = [_dataset(t, columns, synonyms, report) for t in tables]
    relationships: list[dict[str, Any]] = []
    model_ext: dict[str, Any] = {
        "ossie_commit": OSSIE_COMMIT,
        "exporter": "governed_bi.corpus.ossie_export",
        "many_to_many": [],
        "without_cardinality": [],
        "unmapped_joins": [],
    }
    _put(model_ext, "schema", {k: v for k, v in to_mapping(schema).items() if k not in _NATIVE["schema"]})
    for join in (a for a in assets if isinstance(a, JoinAsset)):
        _export_join(join, by_table, relationships, model_ext, report)

    metrics: list[dict[str, Any]] = []
    for metric in (a for a in assets if isinstance(a, MetricAsset)):
        report.metrics += 1
        if not _expression_parses(metric.expression):
            report.unparsed_metrics.append(metric.id)
        entry: dict[str, Any] = {
            "name": metric.id,
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": metric.expression}]},
            "description": _text(metric.summary, metric.body),
        }
        _put(entry, "ai_context", _ai_context(None, synonyms.get(("metric", metric.id), [])))
        entry["custom_extensions"] = _extension(_extras(metric, "metric"))
        metrics.append(entry)

    model_context: dict[str, Any] = _ai_context("\n".join(schema.rules), synonyms.get(("schema", schema.id), [])) or {}
    model_context["terms"] = [
        {k: v for k, v in to_mapping(t).items() if k != "asset_type"} for t in assets if isinstance(t, TermAsset)
    ]
    model_context["verified_queries"] = [_verified_query(a) for a in assets if isinstance(a, FewShotAsset)]
    model_context["unanswerable"] = [
        {k: v for k, v in to_mapping(a).items() if k != "asset_type"}
        for a in assets
        if isinstance(a, NegativeExampleAsset)
    ]
    report.verified_queries = len(model_context["verified_queries"])
    report.unanswerable = len(model_context["unanswerable"])
    model_context = {k: v for k, v in model_context.items() if v}

    model: dict[str, Any] = {"version": OSSIE_VERSION, "name": name, "description": _text(schema.summary, schema.body)}
    _put(model, "ai_context", model_context)
    model["datasets"] = datasets
    _put(model, "relationships", relationships)
    _put(model, "metrics", metrics)
    model["custom_extensions"] = _extension(model_ext)
    return model, report


def _dataset(
    table: TableAsset,
    columns: Mapping[str, ColumnAsset],
    synonyms: Mapping[tuple[str, str], list[str]],
    report: DatabaseReport,
) -> dict[str, Any]:
    ordered = [columns[c] for c in table.columns if c in columns]
    fields_out: list[dict[str, Any]] = []
    for column in ordered:
        entry: dict[str, Any] = {
            "name": column.physical_name,
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": _identifier(column.physical_name)}]},
            "description": _text(column.summary, column.body),
        }
        _put(entry, "datatype", _datatype(column))
        _put(entry, "ai_context", _ai_context(None, synonyms.get(("column", column.id), [])))
        _put(entry, "custom_extensions", _extension(_extras(column, "column")))
        fields_out.append(entry)
    report.datasets += 1
    report.fields += len(fields_out)
    dataset: dict[str, Any] = {
        "name": table.physical_name,
        "source": f"{table.schema}.{table.physical_name}",
        "description": _text(table.summary, table.body),
    }
    _put(
        dataset,
        "primary_key",
        [c.physical_name for c in ordered if c.role is not None and c.role.value == "primary_key"],
    )
    _put(dataset, "ai_context", _ai_context("\n".join(table.rules), synonyms.get(("table", table.id), [])))
    dataset["fields"] = fields_out
    dataset["custom_extensions"] = _extension(_extras(table, "table"))
    return dataset


def _export_join(
    join: JoinAsset,
    tables: Mapping[str, TableAsset],
    relationships: list[dict[str, Any]],
    model_ext: dict[str, Any],
    report: DatabaseReport,
) -> None:
    left, right = tables.get(join.left_table), tables.get(join.right_table)
    pairs = (
        parse_on(join.on, left.physical_name, right.physical_name)
        if left and right
        else "a joined table is not in this database"
    )
    if isinstance(pairs, str):
        report.unmapped_joins[join.id] = pairs
        model_ext["unmapped_joins"].append(to_mapping(join))
        return
    if join.cardinality is None:
        report.without_cardinality.append(join.id)
        model_ext["without_cardinality"].append(to_mapping(join))
        return
    if join.cardinality is Cardinality.many_to_many:
        report.many_to_many += 1
        model_ext["many_to_many"].append(to_mapping(join))
        return
    assert left and right
    flip = join.cardinality is Cardinality.one_to_many
    many, one = (right, left) if flip else (left, right)
    entry: dict[str, Any] = {
        "name": join.id,
        "from": many.physical_name,
        "to": one.physical_name,
        "from_columns": [(r if flip else lc) for lc, r in pairs],
        "to_columns": [(lc if flip else r) for lc, r in pairs],
        "ai_context": {"instructions": _text(join.summary, join.body)},
        "custom_extensions": _extension(_extras(join, "join")),
    }
    report.relationships += 1
    relationships.append(entry)


def _verified_query(shot: FewShotAsset) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": shot.id, "question": shot.summary, "sql": shot.sql}
    extras = {k: v for k, v in to_mapping(shot).items() if k not in _FEW_SHOT_NATIVE}
    _put(entry, "governed_bi", extras)
    return entry


def export_corpus(corpus_dir: Path | str, out_dir: Path | str) -> dict[str, Any]:
    """Load, export and write one file per database plus the report. Returns the report."""
    assets, problems = load(corpus_dir)
    models, reports, unplaced = export_assets(assets)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, model in models.items():
        (out / f"{name}.yaml").write_text(_dump(model), encoding="utf-8", newline="\n")
    report = _report(reports, unplaced, [str(p) for p in problems])
    (out / REPORT_NAME).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return report


def _report(reports: Mapping[str, DatabaseReport], unplaced: list[str], problems: list[str]) -> dict[str, Any]:
    totals = {
        "models": len(reports),
        "datasets": sum(r.datasets for r in reports.values()),
        "relationships": sum(r.relationships for r in reports.values()),
        "many_to_many": sum(r.many_to_many for r in reports.values()),
        "without_cardinality": sum(len(r.without_cardinality) for r in reports.values()),
        "unmapped_joins": sum(len(r.unmapped_joins) for r in reports.values()),
        "metrics": sum(r.metrics for r in reports.values()),
        "unparsed_metrics": sum(len(r.unparsed_metrics) for r in reports.values()),
        "unresolved_bindings": sum(len(r.unresolved_bindings) for r in reports.values()),
        "unplaced_assets": len(unplaced),
    }
    return {
        "ossie_version": OSSIE_VERSION,
        "ossie_commit": OSSIE_COMMIT,
        "totals": totals,
        "unplaced_assets": unplaced,
        "load_problems": problems,
        "databases": {name: asdict(r) for name, r in reports.items()},
    }


def _dump(model: Mapping[str, Any]) -> str:
    return yaml.safe_dump(model, sort_keys=False, allow_unicode=True, width=120)


def read_model_file(path: Path | str) -> list[Asset]:
    return read_model(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def read_model(model: Mapping[str, Any]) -> list[Asset]:
    """Assets from a model this exporter wrote. Not a general Ossie reader."""
    name = model["name"]
    ext = _extension_data(model)
    context = model.get("ai_context") or {}
    summary, body = _split_text(model.get("description", ""))
    out: list[dict[str, Any]] = [
        {
            "asset_type": "schema",
            "name": name,
            "summary": summary,
            **({"body": body} if body else {}),
            **({"rules": context["instructions"].split("\n")} if context.get("instructions") else {}),
            **ext.get("schema", {}),
        }
    ]
    table_ids: dict[str, str] = {}
    for dataset in model["datasets"]:
        out.extend(_read_dataset(name, dataset, table_ids))
    for rel in model.get("relationships", []):
        out.append(_read_relationship(name, rel, table_ids))
    out.extend(ext.get("many_to_many", []) + ext.get("without_cardinality", []) + ext.get("unmapped_joins", []))
    for metric in model.get("metrics", []):
        msummary, mbody = _split_text(metric.get("description", ""))
        out.append(
            {
                "asset_type": "metric",
                "id": metric["name"],
                "expression": metric["expression"]["dialects"][0]["expression"],
                "summary": msummary,
                **({"body": mbody} if mbody else {}),
                **_extension_data(metric),
            }
        )
    out.extend({"asset_type": "term", **t} for t in context.get("terms", []))
    out.extend({"asset_type": "negative_example", **n} for n in context.get("unanswerable", []))
    for query in context.get("verified_queries", []):
        out.append(
            {
                "asset_type": "few_shot",
                "id": query["name"],
                "schema": name,
                "sql": query["sql"],
                "summary": query["question"],
                **query.get("governed_bi", {}),
            }
        )
    return [from_mapping(item) for item in out]


def _read_dataset(schema: str, dataset: Mapping[str, Any], table_ids: dict[str, str]) -> list[dict[str, Any]]:
    ext = _extension_data(dataset)
    phys = dataset["name"]
    tid = ext.get("id", table_id(schema, phys))
    table_ids[phys] = tid
    summary, body = _split_text(dataset.get("description", ""))
    instructions = (dataset.get("ai_context") or {}).get("instructions")
    column_rows: list[dict[str, Any]] = []
    for fld in dataset.get("fields", []):
        csummary, cbody = _split_text(fld.get("description", ""))
        logical = _OSSIE_TO_LOGICAL.get(fld.get("datatype", ""))
        column_rows.append(
            {
                "asset_type": "column",
                "id": derive_column_id(tid, fld["name"]),
                "schema": schema,
                "parent_table": tid,
                "physical_name": fld["name"],
                "summary": csummary,
                **({"body": cbody} if cbody else {}),
                **({"logical_type": logical} if logical else {}),
                **_extension_data(fld),
            }
        )
    table = {
        "asset_type": "table",
        "schema": schema,
        "physical_name": phys,
        "summary": summary,
        **({"body": body} if body else {}),
        **({"rules": instructions.split("\n")} if instructions else {}),
        "columns": [r["id"] for r in column_rows],
        **ext,
    }
    return [table, *column_rows]


def _read_relationship(schema: str, rel: Mapping[str, Any], table_ids: Mapping[str, str]) -> dict[str, Any]:
    ext = _extension_data(rel)
    flip = ext.get("cardinality") == Cardinality.one_to_many.value
    many, one = rel["from"], rel["to"]
    left, right = (one, many) if flip else (many, one)
    left_cols, right_cols = (
        (rel["to_columns"], rel["from_columns"]) if flip else (rel["from_columns"], rel["to_columns"])
    )
    on = " AND ".join(
        f"{exp.column(lc, table=left, db=schema).sql(dialect='postgres')} = "
        f"{exp.column(rc, table=right, db=schema).sql(dialect='postgres')}"
        for lc, rc in zip(left_cols, right_cols, strict=True)
    )
    summary, body = _split_text((rel.get("ai_context") or {}).get("instructions", ""))
    return {
        "asset_type": "join",
        "left_table": table_ids[left],
        "right_table": table_ids[right],
        "on": on,
        "summary": summary,
        **({"body": body} if body else {}),
        **ext,
    }


def _comparable(asset: Asset, tables: Mapping[str, str]) -> dict[str, Any]:
    """An asset's mapping with the join ``on`` reduced to its column pairs, so spelling is not a loss."""
    mapping = to_mapping(asset)
    if isinstance(asset, JoinAsset):
        pairs = parse_on(asset.on, tables.get(asset.left_table, ""), tables.get(asset.right_table, ""))
        mapping["on"] = asset.on if isinstance(pairs, str) else pairs
    return mapping


def round_trip_loss(assets: Sequence[Asset]) -> dict[tuple[str, str], int]:
    """Count, per (asset type, field), the assets whose field differs after export and read-back.

    A whole asset that does not come back counts under the field ``"*"``.
    """
    models, _, unplaced = export_assets(assets)
    back: list[Asset] = []
    for model in models.values():
        back.extend(read_model(yaml.safe_load(_dump(model))))
    physical = {a.id: a.physical_name for a in assets if isinstance(a, TableAsset)}
    physical.update({a.id: a.physical_name for a in back if isinstance(a, TableAsset)})
    returned = {a.id: a for a in back}
    lost: dict[tuple[str, str], int] = defaultdict(int)
    for asset in assets:
        kind = asset.asset_type.value
        twin = returned.get(asset.id)
        if twin is None:
            lost[(kind, "*")] += 1
            continue
        before, after = _comparable(asset, physical), _comparable(twin, physical)
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                lost[(kind, key)] += 1
    for asset_id in unplaced:
        lost[("unplaced", asset_id)] += 1
    return dict(lost)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m governed_bi.corpus.ossie_export", description=__doc__)
    parser.add_argument("--corpus-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = export_corpus(args.corpus_dir, args.out)
    print(json.dumps(report["totals"], indent=2))
    if report["load_problems"]:
        print(f"{len(report['load_problems'])} load problems; see {REPORT_NAME}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
