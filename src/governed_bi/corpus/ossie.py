"""The corpus on disk: one Ossie document per schema, read into and written from the asset types.

The only module that knows Ossie (ADR 0022). A field Ossie defines holds a value only when it means
the same thing; `ai_context` uses only `instructions`; everything else sits in the JSON string of a
`GOVERNED_BI` extension under the engine's own field names. The writer is canonical, so write,
read, write gives the same bytes. The placement table is section 3 of
`docs/reviews/2026-10-workspace-and-ossie-storage.md`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from .identity import derive_column_id, table_id
from .parse import to_mapping
from .schema import (
    Asset,
    Cardinality,
    ColumnAsset,
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    SchemaAsset,
    TableAsset,
    TermAsset,
)

__all__ = ["OSSIE_COMMIT", "OSSIE_VERSION", "VENDOR", "group_by_schema", "model_text", "parse_on", "read_model"]

OSSIE_VERSION = "0.2.0.dev0"
#: The `apache/ossie` commit the format was checked against. Not written into the files, so a bump
#: that keeps the format rewrites nothing.
OSSIE_COMMIT = "d85e9e1"
VENDOR = "GOVERNED_BI"

_LOGICAL_TO_OSSIE = {
    "string": "String",
    "integer": "Integer",
    "decimal": "Decimal",
    "date": "Date",
    "datetime": "DateTime",
    "boolean": "Boolean",
}
_OSSIE_TO_LOGICAL = {**{v: k for k, v in _LOGICAL_TO_OSSIE.items()}, "Float": "decimal", "DateTimeTz": "datetime"}
_FLOAT_PHYSICAL = ("real", "float", "double")
_RELATIONSHIP_CARDINALITIES = {Cardinality.many_to_one, Cardinality.one_to_many, Cardinality.one_to_one}

# What each entity may carry, in the spec's key order, and the keys its extension may hold.
_MODEL_KEYS = (
    "version",
    "name",
    "description",
    "ai_context",
    "datasets",
    "relationships",
    "metrics",
    "custom_extensions",
)
_DATASET_KEYS = ("name", "source", "primary_key", "description", "ai_context", "fields", "metrics", "custom_extensions")
_FIELD_KEYS = ("name", "expression", "description", "datatype", "custom_extensions")
_RELATIONSHIP_KEYS = ("name", "from", "to", "from_columns", "to_columns", "ai_context", "custom_extensions")
_METRIC_KEYS = ("name", "expression", "description", "custom_extensions")
_MODEL_EXT = {"id", "governance", "audit", "terms", "joins", "few_shots"}
_DATASET_EXT = {"grain", "row_count", "governance", "audit", "terms"}
_FIELD_EXT = {"physical_type", "nullable", "role", "reliability", "governance", "audit", "terms"}
_RELATIONSHIP_EXT = {"cardinality", "governance", "audit"}
_METRIC_EXT = {"name", "base_table", "dimensions", "governance", "audit", "terms"}


# ── text ──────────────────────────────────────────────────────────────────────


def _text(summary: str, body: str | None) -> str:
    """Summary, a blank line, body. The loader refuses a summary with a newline, so this splits back."""
    return summary if body is None else f"{summary}\n\n{body}"


def _split(text: str) -> tuple[str, str | None]:
    summary, sep, body = text.partition("\n\n")
    return summary, (body if sep else None)


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    if value not in (None, [], {}, ""):
        target[key] = value


def _expression(sql: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def _expression_text(entity: Mapping[str, Any], where: str) -> str:
    dialects = (entity.get("expression") or {}).get("dialects") or []
    if len(dialects) != 1 or dialects[0].get("dialect") != "ANSI_SQL":
        raise ValueError(f"{where}: expected one ANSI_SQL expression")
    return str(dialects[0]["expression"])


# ── extensions ────────────────────────────────────────────────────────────────


def _extension(data: Mapping[str, Any]) -> list[dict[str, str]]:
    if not data:
        return []
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
    return [{"vendor_name": VENDOR, "data": text}]


def _extension_of(entity: Mapping[str, Any], allowed: set[str], where: str) -> dict[str, Any]:
    entries = entity.get("custom_extensions") or []
    ours = [e for e in entries if e.get("vendor_name") == VENDOR]
    if len(entries) != len(ours) or len(ours) > 1:
        raise ValueError(f"{where}: expected at most one custom_extensions entry, from {VENDOR}")
    if not ours:
        return {}
    data = json.loads(ours[0]["data"])
    if not isinstance(data, dict):
        raise ValueError(f"{where}: {VENDOR} data is not a JSON object")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{where}: unknown {VENDOR} key(s) {unknown}")
    return data


def _only(entity: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    unknown = sorted(set(entity) - set(allowed))
    if unknown:
        raise ValueError(f"{where}: key(s) {unknown} are not read; the corpus uses only what ADR 0022 places")


def _instructions(entity: Mapping[str, Any], where: str) -> str | None:
    context = entity.get("ai_context")
    if context is None:
        return None
    if not isinstance(context, Mapping) or set(context) - {"instructions"}:
        raise ValueError(f"{where}: ai_context may hold only `instructions`")
    value = context.get("instructions")
    return None if value is None else str(value)


# ── joins ─────────────────────────────────────────────────────────────────────


def parse_on(on: str, left: str, right: str) -> list[tuple[str, str]] | str:
    """Column pairs ``(left column, right column)`` of an AND of equalities, or why it is not one."""
    try:
        tree = sqlglot.parse_one(on, dialect="postgres")
    except Exception as err:  # noqa: BLE001 - any parse failure is a reason
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


def _on_text(schema: str, left: str, right: str, pairs: Iterable[tuple[str, str]]) -> str:
    return " AND ".join(
        f"{exp.column(lc, table=left, db=schema).sql(dialect='postgres')} = "
        f"{exp.column(rc, table=right, db=schema).sql(dialect='postgres')}"
        for lc, rc in pairs
    )


# ── writing ───────────────────────────────────────────────────────────────────


class _Dumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_str)


def _dump(document: Mapping[str, Any]) -> str:
    return yaml.dump(document, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=2**31 - 1)


def _ordered(entity: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {k: entity[k] for k in keys if k in entity}


def _owner_of(asset: Asset, owners: Mapping[str, str]) -> str | None:
    if isinstance(asset, SchemaAsset):
        return asset.name
    if isinstance(asset, JoinAsset):
        return owners.get(asset.left_table)
    if isinstance(asset, MetricAsset):
        return owners.get(asset.base_table)
    if isinstance(asset, TermAsset):
        return owners.get(asset.binding.target_id) if asset.binding else None
    return getattr(asset, "schema", None)


def group_by_schema(assets: Sequence[Asset]) -> dict[str, list[Asset]]:
    """The assets of each model file. An asset no schema claims raises: there is no file for it."""
    owners: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, TermAsset):
            owner = _owner_of(asset, {})
            if owner:
                owners[asset.id] = owner
    for asset in assets:
        if isinstance(asset, (JoinAsset, MetricAsset)):
            owner = _owner_of(asset, owners)
            if owner:
                owners[asset.id] = owner
    groups: dict[str, list[Asset]] = defaultdict(list)
    for asset in assets:
        owner = _owner_of(asset, owners)
        if owner is None:
            raise ValueError(f"{asset.id}: no schema claims this asset, so no model file can hold it")
        groups[owner].append(asset)
    return dict(groups)


def _bare(asset: Asset, *drop: str) -> dict[str, Any]:
    mapping = to_mapping(asset)
    for key in ("asset_type", *drop):
        mapping.pop(key, None)
    return mapping


def _datatype(column: ColumnAsset) -> str | None:
    if column.logical_type is None:
        return None
    physical = (column.physical_type or "").casefold()
    if column.logical_type.value == "decimal" and physical.startswith(_FLOAT_PHYSICAL):
        return "Float"
    if column.logical_type.value == "datetime" and "with time zone" in physical:
        return "DateTimeTz"
    return _LOGICAL_TO_OSSIE[column.logical_type.value]


def _scoped(expression: str, field_names: set[str]) -> bool:
    """True when every column the expression names is a bare field of the dataset."""
    for text in (expression, f"SELECT {expression}"):
        try:
            tree = sqlglot.parse_one(text)
            break
        except Exception:  # noqa: BLE001, S112 - try the next form
            continue
    else:
        return False
    for column in tree.find_all(exp.Column):
        if column.table or column.name.casefold() not in field_names:
            return False
    return True


def model_text(name: str, assets: Sequence[Asset]) -> str:
    """The canonical file for one schema. Raises when an asset has no place."""
    return _dump(_model(name, assets))


def _model(name: str, assets: Sequence[Asset]) -> dict[str, Any]:
    by_id = {a.id: a for a in assets}
    schemas = [a for a in assets if isinstance(a, SchemaAsset)]
    if len(schemas) != 1:
        raise ValueError(f"{name}: expected one schema asset, found {len(schemas)}")
    schema = schemas[0]
    terms_for: dict[str, list[dict[str, Any]]] = defaultdict(list)
    model_terms: list[dict[str, Any]] = []
    for term in sorted((a for a in assets if isinstance(a, TermAsset)), key=lambda a: a.id):
        target = term.binding.target_id if term.binding else None
        if (
            target is not None
            and term.binding is not None
            and term.binding.target_type.value in ("column", "table", "metric")
            and target in by_id
        ):
            terms_for[target].append(_bare(term, "binding"))
        else:
            model_terms.append(_bare(term))

    datasets: list[dict[str, Any]] = []
    dataset_of: dict[str, dict[str, Any]] = {}
    seen_columns: set[str] = set()
    for table in sorted((a for a in assets if isinstance(a, TableAsset)), key=lambda a: a.id):
        if table.schema != name or table.id != table_id(name, table.physical_name):
            raise ValueError(f"{table.id}: a table id must be table_id(schema, physical_name)")
        fields: list[dict[str, Any]] = []
        primary_key: list[str] = []
        for column_id in table.columns:
            column = by_id.get(column_id)
            if not isinstance(column, ColumnAsset) or column.id != derive_column_id(table.id, column.physical_name):
                raise ValueError(f"{table.id}: column {column_id!r} is not a column derived from this table")
            seen_columns.add(column.id)
            entry: dict[str, Any] = {
                "name": column.physical_name,
                "expression": _expression(exp.to_identifier(column.physical_name).sql(dialect="postgres")),
                "description": _text(column.summary, column.body),
            }
            _put(entry, "datatype", _datatype(column))
            ext = _bare(column, "id", "schema", "parent_table", "physical_name", "summary", "body", "logical_type")
            if ext.get("role") == "primary_key":
                primary_key.append(column.physical_name)
                ext.pop("role")
            _put(ext, "terms", terms_for.pop(column.id, []))
            _put(entry, "custom_extensions", _extension(ext))
            fields.append(entry)
        dataset: dict[str, Any] = {
            "name": table.physical_name,
            "source": f"{name}.{table.physical_name}",
            "description": _text(table.summary, table.body),
        }
        _put(dataset, "primary_key", primary_key)
        if table.rules:
            dataset["ai_context"] = {"instructions": "\n".join(table.rules)}
        dataset["fields"] = fields
        ext = _bare(table, "id", "schema", "physical_name", "summary", "body", "rules", "columns")
        _put(ext, "terms", terms_for.pop(table.id, []))
        _put(dataset, "custom_extensions", _extension(ext))
        datasets.append(dataset)
        dataset_of[table.id] = dataset
    stray = [a.id for a in assets if isinstance(a, ColumnAsset) and a.id not in seen_columns]
    if stray:
        raise ValueError(f"{name}: columns no table lists: {stray[:5]}")

    relationships: list[dict[str, Any]] = []
    other_joins: list[dict[str, Any]] = []
    for join in sorted((a for a in assets if isinstance(a, JoinAsset)), key=lambda a: a.id):
        relationship = _relationship(name, join, by_id, dataset_of)
        if relationship is None:
            other_joins.append(_bare(join))
        else:
            relationships.append(relationship)

    model_metrics: list[dict[str, Any]] = []
    for metric in sorted((a for a in assets if isinstance(a, MetricAsset)), key=lambda a: a.id):
        entry = {
            "name": metric.id,
            "expression": _expression(metric.expression),
            "description": _text(metric.summary, metric.body),
        }
        ext = _bare(metric, "id", "expression", "summary", "body", "base_table")
        _put(ext, "terms", terms_for.pop(metric.id, []))
        home = dataset_of.get(metric.base_table)
        names = {f["name"].casefold() for f in home["fields"]} if home else set()
        if home is not None and metric.id.casefold() not in names and _scoped(metric.expression, names):
            entry["custom_extensions"] = _extension(ext)
            home.setdefault("metrics", []).append(_ordered(entry, _METRIC_KEYS))
            continue
        ext["base_table"] = metric.base_table
        entry["custom_extensions"] = _extension(ext)
        model_metrics.append(_ordered(entry, _METRIC_KEYS))

    if terms_for:
        raise ValueError(f"{name}: terms bound to {sorted(terms_for)[:5]} found no entity in this model")

    model: dict[str, Any] = {"version": OSSIE_VERSION, "name": name, "description": _text(schema.summary, schema.body)}
    if schema.rules:
        model["ai_context"] = {"instructions": "\n".join(schema.rules)}
    model["datasets"] = [_ordered(d, _DATASET_KEYS) for d in datasets]
    _put(model, "relationships", relationships)
    _put(model, "metrics", model_metrics)
    ext = _bare(schema, "name", "summary", "body", "rules")
    if ext.get("id") == name:
        ext.pop("id")
    _put(ext, "terms", model_terms)
    _put(ext, "joins", other_joins)
    _put(
        ext,
        "few_shots",
        [_bare(a, "schema") for a in sorted(assets, key=lambda a: a.id) if isinstance(a, FewShotAsset)],
    )
    _put(model, "custom_extensions", _extension(ext))
    for shot in (a for a in assets if isinstance(a, FewShotAsset)):
        if shot.schema != name:
            raise ValueError(f"{shot.id}: a few-shot of schema {shot.schema!r} is not in model {name!r}")
    return model


def _relationship(
    name: str, join: JoinAsset, by_id: Mapping[str, Asset], dataset_of: Mapping[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """A relationship when Ossie can say what the join says; otherwise ``None``."""
    left, right = by_id.get(join.left_table), by_id.get(join.right_table)
    if (
        join.cardinality not in _RELATIONSHIP_CARDINALITIES
        or not isinstance(left, TableAsset)
        or not isinstance(right, TableAsset)
    ):
        return None
    if left.id not in dataset_of or right.id not in dataset_of:
        return None
    pairs = parse_on(join.on, left.physical_name, right.physical_name)
    if isinstance(pairs, str) or _on_text(name, left.physical_name, right.physical_name, pairs) != join.on:
        return None
    left_fields = {f["name"] for f in dataset_of[left.id]["fields"]}
    right_fields = {f["name"] for f in dataset_of[right.id]["fields"]}
    if not all(lc in left_fields and rc in right_fields for lc, rc in pairs):
        return None
    flip = join.cardinality is Cardinality.one_to_many
    many, one = (right, left) if flip else (left, right)
    relationship: dict[str, Any] = {
        "name": join.id,
        "from": many.physical_name,
        "to": one.physical_name,
        "from_columns": [rc if flip else lc for lc, rc in pairs],
        "to_columns": [lc if flip else rc for lc, rc in pairs],
        "ai_context": {"instructions": _text(join.summary, join.body)},
    }
    ext = _bare(join, "id", "left_table", "right_table", "on", "summary", "body")
    if ext.get("cardinality") == Cardinality.many_to_one.value:
        ext.pop("cardinality")
    _put(relationship, "custom_extensions", _extension(ext))
    return relationship


# ── reading ───────────────────────────────────────────────────────────────────


def read_model(document: Any, *, where: str) -> list[dict[str, Any]]:
    """Asset mappings from one Ossie document. Raises ``ValueError`` on anything ADR 0022 does not place."""
    if not isinstance(document, Mapping):
        raise ValueError(f"{where}: expected an Ossie document (a mapping)")
    _only(document, _MODEL_KEYS, where)
    if document.get("version") != OSSIE_VERSION:
        raise ValueError(f"{where}: version {document.get('version')!r} is not {OSSIE_VERSION}")
    name = str(document["name"])
    out: list[dict[str, Any]] = []
    ext = _extension_of(document, _MODEL_EXT, where)
    summary, body = _split(str(document.get("description", "")))
    schema: dict[str, Any] = {"asset_type": "schema", "id": ext.pop("id", name), "name": name, "summary": summary}
    _put(schema, "body", body)
    rules = _instructions(document, where)
    if rules is not None:
        schema["rules"] = rules.split("\n")
    for key in ("governance", "audit"):
        _put(schema, key, ext.get(key))
    out.append(schema)
    out.extend({"asset_type": "term", **t} for t in ext.get("terms", []))
    out.extend({"asset_type": "join", **j} for j in ext.get("joins", []))
    out.extend({"asset_type": "few_shot", "schema": name, **f} for f in ext.get("few_shots", []))

    table_ids: dict[str, str] = {}
    fields_of: dict[str, set[str]] = {}
    for dataset in document.get("datasets") or []:
        label = f"{where}: dataset {dataset.get('name')!r}"
        _only(dataset, _DATASET_KEYS, label)
        physical = str(dataset["name"])
        if dataset.get("source") != f"{name}.{physical}":
            raise ValueError(f"{label}: source must be {name}.{physical}")
        tid = table_id(name, physical)
        table_ids[physical] = tid
        ext = _extension_of(dataset, _DATASET_EXT, label)
        summary, body = _split(str(dataset.get("description", "")))
        table: dict[str, Any] = {
            "asset_type": "table",
            "id": tid,
            "schema": name,
            "physical_name": physical,
            "summary": summary,
        }
        _put(table, "body", body)
        rules = _instructions(dataset, label)
        if rules is not None:
            table["rules"] = rules.split("\n")
        primary_key = set(dataset.get("primary_key") or [])
        columns: list[dict[str, Any]] = []
        for field in dataset.get("fields") or []:
            flabel = f"{label} field {field.get('name')!r}"
            _only(field, _FIELD_KEYS, flabel)
            column_name = str(field["name"])
            expected = exp.to_identifier(column_name).sql(dialect="postgres")
            if _expression_text(field, flabel) != expected:
                raise ValueError(f"{flabel}: a field's expression must be its column name, {expected}")
            cid = derive_column_id(tid, column_name)
            fext = _extension_of(field, _FIELD_EXT, flabel)
            summary, body = _split(str(field.get("description", "")))
            column: dict[str, Any] = {
                "asset_type": "column",
                "id": cid,
                "schema": name,
                "parent_table": tid,
                "physical_name": column_name,
                "summary": summary,
            }
            _put(column, "body", body)
            if field.get("datatype") is not None:
                if field["datatype"] not in _OSSIE_TO_LOGICAL:
                    raise ValueError(f"{flabel}: datatype {field['datatype']!r} is not read")
                column["logical_type"] = _OSSIE_TO_LOGICAL[field["datatype"]]
            if column_name in primary_key:
                if "role" in fext:
                    raise ValueError(f"{flabel}: a primary key field states a second role")
                column["role"] = "primary_key"
            out.extend(_terms(fext.pop("terms", []), "column", cid))
            column.update(fext)
            columns.append(column)
        missing = primary_key - {c["physical_name"] for c in columns}
        if missing:
            raise ValueError(f"{label}: primary_key names no field: {sorted(missing)}")
        fields_of[physical] = {c["physical_name"] for c in columns}
        table["columns"] = [c["id"] for c in columns]
        out.extend(_terms(ext.pop("terms", []), "table", tid))
        table.update(ext)
        out.append(table)
        out.extend(columns)
        for metric in dataset.get("metrics") or []:
            out.extend(_metric(metric, tid, f"{label} metric {metric.get('name')!r}"))

    for metric in document.get("metrics") or []:
        out.extend(_metric(metric, None, f"{where}: metric {metric.get('name')!r}"))

    for relationship in document.get("relationships") or []:
        rlabel = f"{where}: relationship {relationship.get('name')!r}"
        _only(relationship, _RELATIONSHIP_KEYS, rlabel)
        rext = _extension_of(relationship, _RELATIONSHIP_EXT, rlabel)
        cardinality = rext.pop("cardinality", Cardinality.many_to_one.value)
        flip = cardinality == Cardinality.one_to_many.value
        many, one = str(relationship["from"]), str(relationship["to"])
        left, right = (one, many) if flip else (many, one)
        from_columns, to_columns = list(relationship["from_columns"]), list(relationship["to_columns"])
        left_columns, right_columns = (to_columns, from_columns) if flip else (from_columns, to_columns)
        if left not in table_ids or right not in table_ids or len(left_columns) != len(right_columns):
            raise ValueError(f"{rlabel}: from and to must name datasets of this model, with as many columns each")
        if not set(left_columns) <= fields_of[left] or not set(right_columns) <= fields_of[right]:
            raise ValueError(f"{rlabel}: from_columns and to_columns must name fields of their datasets")
        text = _instructions(relationship, rlabel) or ""
        summary, body = _split(text)
        join: dict[str, Any] = {
            "asset_type": "join",
            "id": relationship["name"],
            "left_table": table_ids[left],
            "right_table": table_ids[right],
            "on": _on_text(name, left, right, zip(left_columns, right_columns)),
            "summary": summary,
            "cardinality": cardinality,
        }
        _put(join, "body", body)
        join.update(rext)
        out.append(join)
    return out


def _terms(items: Iterable[Mapping[str, Any]], target_type: str, target_id: str) -> list[dict[str, Any]]:
    return [{"asset_type": "term", **t, "binding": {"target_type": target_type, "target_id": target_id}} for t in items]


def _metric(metric: Mapping[str, Any], base_table: str | None, where: str) -> list[dict[str, Any]]:
    _only(metric, _METRIC_KEYS, where)
    ext = _extension_of(metric, _METRIC_EXT, where)
    if (base_table is None) == ("base_table" not in ext):
        raise ValueError(
            f"{where}: base_table is the dataset that holds the metric, or in the extension at model level"
        )
    summary, body = _split(str(metric.get("description", "")))
    out: dict[str, Any] = {
        "asset_type": "metric",
        "id": metric["name"],
        "base_table": base_table or ext.pop("base_table"),
        "expression": _expression_text(metric, where),
        "summary": summary,
    }
    _put(out, "body", body)
    terms = _terms(ext.pop("terms", []), "metric", str(metric["name"]))
    out.update(ext)
    return [out, *terms]
