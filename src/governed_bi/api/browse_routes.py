"""Browsing routes: filtering, lean catalog, table detail (ADR 0009).

HTTP shell over :mod:`governed_bi.api.browse`. Projections match the client's declared shapes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from governed_bi.api.browse import (
    apply_where,
    columns_for,
    parse_where,
    predicate_columns,
    row_for,
    sort_rows,
)
from governed_bi.corpus.schema import class_for
from governed_bi.register.assets import ASSET_REGISTER

__all__ = ["router"]

#: Mounted by ``routes.app``. Declare ``/schema/summary`` before ``/schema/{table_id}``.
router = APIRouter()


def _request_session() -> Any:
    """This request's session (imported lazily to avoid a circular import with ``routes``)."""
    from governed_bi.api.routes import _session

    return _session()


@router.get("/corpus/fields")
def corpus_fields(type: str | None = None) -> dict[str, Any]:
    """Filterable columns of one asset type, derived from its dataclass."""
    known = {t.value: t for t in ASSET_REGISTER}
    if type is None or type not in known:
        return {
            "type": None,
            "columns": [],
            "types": sorted(known),
            "detail": None if type is None else f"unknown asset type {type!r}",
        }
    asset_type = known[type]
    return {
        "type": type,
        "columns": columns_for(asset_type, class_for(type)),
        "types": sorted(known),
    }


@router.get("/corpus/rows")
def corpus_rows(
    type: str,
    where: list[str] | None = Query(default=None),
    sort: str | None = None,
    order: str = "asc",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Filtered, sorted, paginated assets of one type (ADR 0009 D1).

    ``where`` repeats as ``field:op:value``. Unknown predicates land in ``unknown_where``
    and are not applied. ``total`` is the count after filtering.
    """
    known_types = {t.value: t for t in ASSET_REGISTER}
    if type not in known_types:
        return {
            "rows": [],
            "total": 0,
            "offset": 0,
            "limit": limit,
            "columns": [],
            "unknown_where": [],
            "detail": f"unknown asset type {type!r}",
        }

    session = _request_session()
    columns = columns_for(known_types[type], class_for(type))
    ops_by_field = {column["name"]: column["ops"] for column in columns}

    predicates, malformed = parse_where(where or ())
    assets = [a for a in session.assets_by_id.values() if a.asset_type.value == type]
    matched, unknown = apply_where(assets, predicates, ops_by_field)
    ordered = sort_rows(matched, sort, order)

    start = max(0, int(offset))
    end = start + max(1, min(500, int(limit)))
    return {
        "rows": [row_for(asset) for asset in ordered[start:end]],
        "total": len(ordered),
        "offset": start,
        "limit": end - start,
        "columns": columns,
        "unknown_where": [*unknown, *malformed],
    }


#: Default/ceiling for ``/schema/summary``. Catalog consumers need the full table list.
SUMMARY_PAGE_LIMIT = 1000


@router.get("/schema/summary")
def schema_summary(schema: str | None = None, limit: int = SUMMARY_PAGE_LIMIT, offset: int = 0) -> dict[str, Any]:
    """Lean table catalog: enough for a browser row and a badge, no prose.

    ``offset`` and ``limit`` are echoed as applied after clamping.
    """
    session = _request_session()
    tables = sorted(
        (
            a
            for a in session.assets_by_id.values()
            if a.asset_type.value == "table" and (schema is None or getattr(a, "schema", None) == schema)
        ),
        key=lambda a: a.id,
    )
    start = max(0, int(offset))
    applied_limit = max(1, min(SUMMARY_PAGE_LIMIT, int(limit)))
    items = [_table_summary(session, table) for table in tables[start : start + applied_limit]]
    return {"total": len(tables), "offset": start, "limit": applied_limit, "items": items}


@router.get("/schema/{table_id}")
def schema_detail(table_id: str) -> dict[str, Any]:
    """One table's full detail. Declared after ``/schema/summary`` so the literal path wins."""
    session = _request_session()
    table = session.assets_by_id.get(table_id)
    if table is None or table.asset_type.value != "table":
        raise HTTPException(status_code=404, detail=f"no table asset {table_id!r}")
    return _table_view(session, table)


def _table_summary(session: Any, table: Any) -> dict[str, Any]:
    columns = [session.assets_by_id.get(cid) for cid in (getattr(table, "columns", ()) or ())]
    columns = [c for c in columns if c is not None]
    lean = [
        {
            "id": c.id,  # asset id; callers must never derive one (ADR 0008 D4)
            "physical_name": getattr(c, "physical_name", ""),
            "physical_type": getattr(c, "physical_type", None) or "",
            "role": getattr(getattr(c, "role", None), "value", None),
            "reliability": getattr(getattr(c, "reliability", None), "status", None).value
            if getattr(getattr(c, "reliability", None), "status", None) is not None
            else "ok",
            "excluded": bool(getattr(getattr(c, "governance", None), "excluded", False)),
            # Tri-state: None means not observed (ADR 0005 §6).
            "nullable": getattr(c, "nullable", None),
            "is_unique": getattr(c, "is_unique", None),
        }
        for c in columns
    ]
    provenance = getattr(getattr(table, "audit", None), "provenance", None)
    return {
        "id": table.id,
        "physical_name": getattr(table, "physical_name", table.id),
        "schema": getattr(table, "schema", "") or "",
        "row_count": getattr(table, "row_count", None),
        "n_columns": len(lean),
        "excluded": bool(getattr(getattr(table, "governance", None), "excluded", False)),
        "has_suspect": any(c["reliability"] == "suspect" for c in lean),
        "provenance_status": getattr(getattr(provenance, "status", None), "value", None),
        "columns": lean,
    }


def _table_view(session: Any, table: Any) -> dict[str, Any]:
    """One table as the client's ``TableView``. ``description`` is ``body`` falling back to ``summary``."""
    governance = getattr(table, "governance", None)
    provenance = getattr(getattr(table, "audit", None), "provenance", None)
    columns = [
        _column_view(c)
        for cid in (getattr(table, "columns", ()) or ())
        if (c := session.assets_by_id.get(cid)) is not None
    ]
    return {
        "id": table.id,
        "physical_name": getattr(table, "physical_name", table.id),
        "schema": getattr(table, "schema", "") or "",
        "row_count": getattr(table, "row_count", None),
        "description": getattr(table, "body", None) or getattr(table, "summary", None),
        "grain": getattr(table, "grain", None),
        "confidence": getattr(table, "confidence", None),
        "excluded": bool(getattr(governance, "excluded", False)),
        "excluded_reason": getattr(governance, "reason", None),
        "provenance_status": getattr(getattr(provenance, "status", None), "value", None),
        "columns": columns,
        "rules": list(getattr(table, "rules", ()) or ()),
    }


def _column_view(column: Any) -> dict[str, Any]:
    """One column as the client's ``ColumnView``.

    ``is_unique`` defaults to ``False`` when unobserved — the contract requires a boolean.
    """
    governance = getattr(column, "governance", None)
    reliability = getattr(column, "reliability", None)
    provenance = getattr(getattr(column, "audit", None), "provenance", None)
    return {
        "id": column.id,
        "physical_name": getattr(column, "physical_name", ""),
        "physical_type": getattr(column, "physical_type", None) or "",
        "logical_type": getattr(getattr(column, "logical_type", None), "value", None) or "",
        "nullable": bool(getattr(column, "nullable", False)),
        "is_unique": bool(getattr(column, "is_unique", False)),
        "sample_values": list(getattr(column, "sample_values", ()) or ()),
        "description": getattr(column, "body", None) or getattr(column, "summary", None),
        "role": getattr(getattr(column, "role", None), "value", None),
        "references": getattr(column, "references", None),
        "confidence": getattr(column, "confidence", None),
        "reliability": getattr(getattr(reliability, "status", None), "value", None) or "ok",
        "reliability_note": getattr(reliability, "note", None),
        "excluded": bool(getattr(governance, "excluded", False)),
        "excluded_reason": getattr(governance, "reason", None),
        "provenance_status": getattr(getattr(provenance, "status", None), "value", None),
        "evidence": getattr(getattr(column, "audit", None), "evidence", None),
    }


def _column_ref(session: Any, column_id: str) -> dict[str, Any] | None:
    """A column as the client's ``columnRefSchema``, or ``None`` if the id names nothing."""
    column = session.assets_by_id.get(column_id)
    if column is None or column.asset_type.value != "column":
        return None
    return {
        "column_id": column.id,
        "table_id": getattr(column, "parent_table", "") or "",
        "physical_name": getattr(column, "physical_name", "") or "",
    }


@router.get("/columns/{column_id}/related")
def column_related(column_id: str) -> dict[str, Any]:
    """Every semantic-layer item touching one physical column.

    Unknown id → 200 with ``column_resolvable: false`` (not 404).
    Joins match by parsing the ON clause, not by substring.
    """
    session = _request_session()
    by_id = session.assets_by_id
    column = by_id.get(column_id)
    if column is None or column.asset_type.value != "column":
        return {
            "column": {
                "id": column_id,
                "table_id": "",
                "table_physical_name": "",
                "schema": None,
                "physical_name": "",
            },
            "terms": [],
            "rules": [],
            "fk_out": None,
            "fk_in": [],
            "joins": [],
            "metrics": [],
            "meta": {"column_resolvable": False},
        }

    table_id = getattr(column, "parent_table", "") or ""
    table = by_id.get(table_id)
    table_physical = getattr(table, "physical_name", "") or ""
    physical_name = getattr(column, "physical_name", "") or ""

    terms = [
        {
            "id": asset.id,
            "name": getattr(asset, "name", asset.id),
            "synonyms": list(getattr(asset, "synonyms", ()) or ()),
            "confidence": getattr(asset, "confidence", None),
            "provenance_status": getattr(
                getattr(getattr(asset, "audit", None), "provenance", None), "status", None
            ).value
            if getattr(getattr(getattr(asset, "audit", None), "provenance", None), "status", None) is not None
            else None,
        }
        for asset in sorted(by_id.values(), key=lambda a: a.id)
        if asset.asset_type.value == "term" and getattr(getattr(asset, "binding", None), "target_id", None) == column_id
    ]

    # Positional ids: table rules are strings, not assets.
    rules = [
        {
            "id": f"{table_id}#rule-{index}",
            "kind": "table",
            "statement": str(statement),
            "confidence": None,
            "provenance_status": None,
        }
        for index, statement in enumerate(getattr(table, "rules", ()) or ())
    ]

    fk_out = _column_ref(session, getattr(column, "references", None) or "")
    fk_in = [
        ref
        for ref in (
            _column_ref(session, other.id)
            for other in sorted(by_id.values(), key=lambda a: a.id)
            if other.asset_type.value == "column" and getattr(other, "references", None) == column_id
        )
        if ref is not None
    ]

    wanted = physical_name.casefold()
    table_key = table_physical.casefold()
    joins = []
    for asset in sorted(by_id.values(), key=lambda a: a.id):
        if asset.asset_type.value != "join":
            continue
        named = predicate_columns(getattr(asset, "on", "") or "")
        if (table_key, wanted) not in named and ("", wanted) not in named:
            continue
        left, right = getattr(asset, "left_table", ""), getattr(asset, "right_table", "")
        other = right if left == table_id else left
        confidence = getattr(asset, "confidence", None)
        joins.append(
            {
                "id": asset.id,
                "left_table": left,
                "right_table": right,
                "other_table_id": other,
                "on": getattr(asset, "on", "") or "",
                "cardinality": getattr(getattr(asset, "cardinality", None), "value", None),
                "confidence": confidence,
                "low_confidence": bool(confidence is not None and confidence < 0.5),
            }
        )

    metrics = [
        {"id": asset.id, "name": getattr(asset, "name", asset.id), "granularity": "table"}
        for asset in sorted(by_id.values(), key=lambda a: a.id)
        if asset.asset_type.value == "metric" and getattr(asset, "base_table", None) == table_id
    ]

    return {
        "column": {
            "id": column.id,
            "table_id": table_id,
            "table_physical_name": table_physical,
            "schema": getattr(column, "schema", None),
            "physical_name": physical_name,
        },
        "terms": terms,
        "rules": rules,
        "fk_out": fk_out,
        "fk_in": fk_in,
        "joins": joins,
        "metrics": metrics,
        "meta": {"column_resolvable": True},
    }
