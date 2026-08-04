"""The browsing routes: filtering, the lean catalog, one table's detail. ADR 0009.

Split out of :mod:`governed_bi.api.routes` when that file crossed the 1 000-line hard cap
(ADR 0005 §6). The split follows a real seam rather than a convenient line number:
:mod:`governed_bi.api.browse` already holds the *pure* filtering, sorting and subgraph logic,
and this module is the HTTP shell over it. Nothing here decides anything -- it reads query
parameters, calls that module, and projects assets into the shapes the client declares.

**Every projection here is the client's declared shape, and getting one wrong is not a
validation nuisance.** The UI parses each response with zod at the boundary and throws on a
mismatch, so one missing required field takes a whole page down -- and ``/schema`` is not one
page: it is also the fallback catalog source, so its wrong shape emptied the namespace rail
as well.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from governed_bi.api.browse import (
    apply_where,
    columns_for,
    parse_where,
    row_for,
    sort_rows,
)
from governed_bi.corpus.schema import class_for
from governed_bi.register.assets import ASSET_REGISTER

__all__ = ["router"]

#: Mounted by ``routes.app``. Declaration order inside a router is preserved, which
#: ``/schema/summary`` depends on: it must be declared before ``/schema/{table_id}`` or the
#: path parameter swallows ``summary`` as a table id.
router = APIRouter()


def _request_session() -> Any:
    """This request's session, from the **one** definition in :mod:`~governed_bi.api.routes`.

    Imported inside the function rather than at module scope: ``routes`` imports this
    module's ``router``, so a top-level import would be circular.
    ``tools/check_one_implementation.py`` refused a second ``_session`` here and was right --
    two readers of "the session for this request" is two places to change when the session
    stops being process-global.
    """
    from governed_bi.api.routes import _session

    return _session()


@router.get("/schema")
def schema(schema: str | None = None) -> list[dict[str, Any]]:
    """Every table as the client's declared ``TableView``, with its columns.

    Built by the **same** :func:`_table_view` as ``/schema/{id}``, so the flat dump and the
    detail fetch cannot disagree. It had its own inline projection emitting
    ``{id, name, schema, summary, columns:[...]}``, which the contract rejects -- and this
    route is also the fallback catalog source, so the failure took the namespace rail with it.

    It also resolved columns by scanning all 13 981 assets per table and matching an id
    prefix: O(tables x assets), and wrong besides -- ``sales.order`` is a prefix of
    ``sales.order_line``. The table's own ``columns`` field holds the ids, which is the
    declared reference (ADR 0005 §1.2).
    """
    session = _request_session()
    tables = [
        a
        for a in session.assets_by_id.values()
        if a.asset_type.value == "table"
        and (schema is None or getattr(a, "schema", None) == schema)
    ]
    return [_table_view(session, table) for table in sorted(tables, key=lambda a: a.id)]


# ── browsing: filtering, the lean catalog, and bounded relationships ──────────
#
# ADR 0009. The four routes below are the ones `capabilities.can_scope` gates, and the UI
# was already written against them — they returned 404, so the UI fell back to the flat
# `/schema` + `/corpus/assets` dumps (937 KB and 2.25 MB measured on the live lake).


@router.get("/corpus/fields")
def corpus_fields(type: str | None = None) -> dict[str, Any]:
    """The filterable columns of one asset type, **derived from its dataclass**.

    The UI renders its filter row from this, so a field added to ``corpus/schema.py`` becomes
    filterable with no change here and none in TypeScript. A column list written in this
    route would be the drift ``register/`` exists to end — and it would drift silently,
    because a missing column is indistinguishable from one somebody chose not to expose.
    """
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
    """Filtered, sorted, paginated assets of one type. ADR 0009 D1.

    ``where`` repeats as ``field:op:value``. One opaque triple rather than a query parameter
    per field, because the parameter set must not grow with the field set — that is three
    places to forget a field instead of none.

    A predicate naming an unknown field or an unsupported operator comes back in
    ``unknown_where`` and is **not applied**. Ignoring it would render a filtered-looking
    list that is not filtered, which is the same defect class as a gate that never fires.

    ``total`` is the count **after** filtering: returning the unfiltered total beside a
    filtered page is how a reader concludes their filter did nothing.
    """
    known_types = {t.value: t for t in ASSET_REGISTER}
    if type not in known_types:
        return {"rows": [], "total": 0, "offset": 0, "limit": limit, "columns": [],
                "unknown_where": [], "detail": f"unknown asset type {type!r}"}

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


@router.get("/schema/summary")
def schema_summary(
    schema: str | None = None, limit: int = 200, offset: int = 0
) -> dict[str, Any]:
    """The lean table catalog: enough for a browser row and a badge, no prose.

    This is what removes the 937 KB. ``/schema`` inlines every column's ``summary`` and
    ``body``; a catalog row needs a physical name, a type, a role and two flags, and the
    prose is fetched for the one table someone opens.
    """
    session = _request_session()
    tables = sorted(
        (
            a
            for a in session.assets_by_id.values()
            if a.asset_type.value == "table"
            and (schema is None or getattr(a, "schema", None) == schema)
        ),
        key=lambda a: a.id,
    )
    start = max(0, int(offset))
    end = start + max(1, min(1000, int(limit)))
    items = [_table_summary(session, table) for table in tables[start:end]]
    return {"total": len(tables), "items": items}


@router.get("/schema/{table_id}")
def schema_detail(table_id: str) -> dict[str, Any]:
    """One table's full detail, for a detail sheet. Declared **after** ``/schema/summary``
    so the literal path wins the route match — FastAPI resolves in declaration order, and a
    path parameter declared first would swallow ``summary`` as a table id."""
    session = _request_session()
    table = session.assets_by_id.get(table_id)
    if table is None or table.asset_type.value != "table":
        # 404 rather than a hollow TableView. The client's `tableDetail` turns a 404 into a
        # readable ApiError, where a body full of nulls would *parse* and render an empty
        # table as though the corpus carried one.
        raise HTTPException(status_code=404, detail=f"no table asset {table_id!r}")
    return _table_view(session, table)


def _table_summary(session: Any, table: Any) -> dict[str, Any]:
    columns = [session.assets_by_id.get(cid) for cid in (getattr(table, "columns", ()) or ())]
    columns = [c for c in columns if c is not None]
    lean = [
        {
            "physical_name": getattr(c, "physical_name", ""),
            "physical_type": getattr(c, "physical_type", None) or "",
            "role": getattr(getattr(c, "role", None), "value", None),
            "reliability": getattr(getattr(c, "reliability", None), "status", None).value
            if getattr(getattr(c, "reliability", None), "status", None) is not None
            else "ok",
            "excluded": bool(getattr(getattr(c, "governance", None), "excluded", False)),
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
    """One table as the client's declared ``TableView``. Used by ``/schema`` **and**
    ``/schema/{id}``, so the flat dump and the detail fetch cannot disagree about a table.

    **This shape was wrong, and it broke more than one page.** Both routes emitted
    ``{id, name, schema, summary, columns:[{id, name, summary, type}]}`` while the contract
    declares ``physical_name``, ``row_count``, ``description``, ``grain``, ``confidence``,
    ``excluded``, ``excluded_reason`` and ``provenance_status`` -- all required -- plus a much
    richer column. So ``api.schema()`` threw at the zod boundary, and ``/schema`` is not only
    the Tables tab: it is the **fallback catalog source**, so the namespace rail on the schema
    page had nothing to list either. One wrong shape, three broken surfaces.

    ``description`` is ``body`` falling back to ``summary``. v2 replaced v1's ``description``
    with that pair (ADR 0005 §1), and a UI "description" wants the fuller text -- ``summary``
    is the 250-character indexed line, ``body`` is what a reader opening a detail sheet came
    for.
    """
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
        # Additive and not in the declared contract: zod strips unknown keys, so this costs
        # the client nothing and is useful to anything reading the route directly.
        "rules": list(getattr(table, "rules", ()) or ()),
    }


def _column_view(column: Any) -> dict[str, Any]:
    """One column as the client's declared ``ColumnView``.

    ``is_unique`` is ``False`` when the corpus says nothing, and that is a claim about a
    *missing* claim -- the contract types it as a required boolean, so ``None`` is not
    expressible. Written down rather than hidden: an uncurated corpus reports nothing unique,
    which is not the same as having checked and found nothing.
    """
    governance = getattr(column, "governance", None)
    reliability = getattr(column, "reliability", None)
    provenance = getattr(getattr(column, "audit", None), "provenance", None)
    return {
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
