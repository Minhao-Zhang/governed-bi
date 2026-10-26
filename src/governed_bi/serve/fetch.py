"""Tool bodies with no LangGraph in them. ``serve/tools.py`` is the adapter.

An execution returns its ledger row. Both executing tools pass the statement
through :func:`prepare`. See ADR 0006."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlglot import expressions as exp

from governed_bi.corpus.analyst import AnalystCorpus
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.ledger import AttemptRecord, attempt_record, capped_error
from governed_bi.govern.pipeline import prepare, spellings_for
from governed_bi.govern.policy import GovernancePolicy

__all__ = [
    "SAMPLE_ROWS_MAX_VALUES",
    "asset_attr",
    "asset_is_table",
    "distinct_values_statement",
    "inspect_schema",
    "read_body",
    "read_body_cap",
    "run_query",
    "sample_rows",
]

_DEFAULT_READ_BODY_MAX_CHARS = 80_000

#: Ceiling ``sample_rows`` cannot widen. Nothing here writes a setting (ADR 0006).
SAMPLE_ROWS_MAX_VALUES = 20

#: Rows of a ``run_query`` result the agent sees. The answer delivers all of them.
PREVIEW_ROWS = 20


def read_body_cap(state: Mapping[str, Any], cfg: Mapping[str, Any]) -> int:
    for source in (state, state.get("settings_resolved") or {}, cfg):
        if not isinstance(source, Mapping):
            continue
        raw = source.get("read_body_max_tokens")
        if raw is not None:
            return max(256, int(raw) * 4)
    return _DEFAULT_READ_BODY_MAX_CHARS


def asset_attr(asset: Any, name: str) -> Any:
    if isinstance(asset, Mapping):
        return asset.get(name)
    return getattr(asset, name, None)


def read_body(
    asset_ids: Sequence[str],
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    max_chars: int,
) -> tuple[str, bool]:
    """``(payload, delivered)``. An out-of-scope refusal is not a delivery (ADR 0005 §3.6)."""
    parts: list[str] = []
    used = 0
    for raw_id in asset_ids:
        aid = str(raw_id)
        if not bounds.may_read_body(aid):
            return OUT_OF_SCOPE_MESSAGE, False
        asset = assets.get(aid)
        if asset is None:
            return OUT_OF_SCOPE_MESSAGE, False
        body = asset_attr(asset, "body")
        text = "" if body is None else str(body)
        chunk = f"### {aid}\n{text}"
        if used + len(chunk) > max_chars:
            remain = max_chars - used
            if remain <= 0:
                break
            chunk = chunk[:remain] + "\n…[truncated]"
            parts.append(chunk)
            break
        parts.append(chunk)
        used += len(chunk)
    payload = "\n\n".join(parts) if parts else "(empty)"
    return payload, True


def inspect_schema(
    table_id: str,
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
) -> tuple[str, bool]:
    tid = str(table_id)
    if not bounds.may_inspect_schema(tid):
        return OUT_OF_SCOPE_MESSAGE, False
    table = assets.get(tid)
    if table is None or not asset_is_table(table):
        return OUT_OF_SCOPE_MESSAGE, False
    columns: list[dict[str, Any]] = []
    for col_id in asset_attr(table, "columns") or ():
        col = assets.get(str(col_id))
        if col is None:
            columns.append({"id": str(col_id)})
            continue
        columns.append(
            {
                "id": str(asset_attr(col, "id") or col_id),
                "physical_name": asset_attr(col, "physical_name"),
                "physical_type": asset_attr(col, "physical_type"),
                "nullable": asset_attr(col, "nullable"),
            }
        )
    payload = json.dumps(
        {
            "table_id": tid,
            "physical_name": asset_attr(table, "physical_name"),
            "schema": asset_attr(table, "schema"),
            "columns": columns,
        },
        sort_keys=True,
        default=str,
    )
    return payload, True


def asset_is_table(asset: Any) -> bool:
    if isinstance(asset, TableAsset):
        return True
    at = asset_attr(asset, "asset_type")
    return str(getattr(at, "value", at) or "") == "table"


def distinct_values_statement(
    *, schema: str, table: str, column: str, limit: int, dialect: str
) -> str:
    """A sqlglot tree, so ``physical_name`` cannot close a quoted string (ADR 0006)."""
    col = exp.column(column, table=table, quoted=True)
    query = (
        exp.select(col)
        .distinct()
        .from_(exp.table_(table, db=schema or None, quoted=True))
        .where(exp.Not(this=col.is_(exp.null())))
        .order_by(col)
        .limit(limit)
    )
    return query.sql(dialect=dialect)


def sample_rows(
    column_id: str,
    *,
    limit: int,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    connector: Any,
    corpus: Any,
    policy: GovernancePolicy,
) -> tuple[str, bool, AttemptRecord | None]:
    """The ledger row is ``None`` only when no statement was built.

    Suspect columns are refused in :func:`prepare` (ADR 0006).
    """
    cid = str(column_id)
    if not bounds.may_sample(cid):
        return OUT_OF_SCOPE_MESSAGE, False, None
    col = assets.get(cid)
    if col is None:
        return OUT_OF_SCOPE_MESSAGE, False, None
    at = asset_attr(col, "asset_type")
    is_col = isinstance(col, ColumnAsset) or str(getattr(at, "value", at) or "") == "column"
    if not is_col:
        return OUT_OF_SCOPE_MESSAGE, False, None
    if connector is None:
        # Missing connector is wiring, not a governance refusal. ``r_not_a_read`` would blame ``Layer.NO_WRITE``.
        raise GovernanceUsageError(
            "sample_rows has no connector: configurable['connector'] is None. A missing "
            "connector is a wiring failure, and recording it as a governance result attributes "
            "our own misconfiguration to the statement."
        )
    if not isinstance(corpus, AnalystCorpus):
        # A missing corpus is wiring. Refusing it would record a governance result.
        raise GovernanceUsageError(
            "sample_rows has no AnalystCorpus: configurable['corpus'] is "
            f"{type(corpus).__name__}. The sample path now runs through check(), which "
            "derives column authorization from AnalystCorpus as a type (ADR 0006 §8)."
        )

    table = str(asset_attr(col, "parent_table") or "")
    physical = str(asset_attr(col, "physical_name") or "")
    schema = str(asset_attr(col, "schema") or "")
    # Engine spelling, not the corpus key. A missing schema makes the connector guess ``public`` (ADR 0008).
    parent = assets.get(table)
    table_name = str(asset_attr(parent, "physical_name") or "") if parent is not None else ""
    if not table_name:
        table_name = table.split(".")[-1] if table else ""
    if not physical or not table_name:
        # No identifier names a relation. There is no statement to refuse.
        return OUT_OF_SCOPE_MESSAGE, False, None

    dialect = getattr(connector, "dialect", None) or "sqlite"
    statement = distinct_values_statement(
        schema=schema,
        table=table_name,
        column=physical,
        limit=max(1, min(int(limit), SAMPLE_ROWS_MAX_VALUES)),
        dialect=dialect,
    )
    spellings, ambiguous, by_table = spellings_for(corpus, bounds.retrieved_tables)
    prepared = prepare(
        statement,
        retrieved_tables=bounds.retrieved_tables,
        corpus=corpus,
        spellings=spellings,
        ambiguous_folds=ambiguous,
        spellings_by_table=by_table,
        dialect=dialect,
        policy=policy,
    )
    attempt = attempt_record(
        result=prepared.result,
        path="sample",
        executed_sql=prepared.sql,
        checked_sql=prepared.canonical if prepared.sql is not None else None,
        submitted_sql=statement,
    )
    if prepared.sql is None:
        detail = prepared.result.get("detail") or prepared.result.get("reason_code")
        return f"sample_rows refused: {detail}", False, attempt

    try:
        _columns, rows, _truncated = connector.execute(prepared.sql)
    except Exception as exc:  # noqa: BLE001 — the row is the point, not the traceback
        # Cleared every layer and was sent, so the ledger owes a row.
        error = f"{type(exc).__name__}: {exc}"
        return f"sample_rows error: {error}", False, {**attempt, "error": capped_error(error)}
    payload = json.dumps(
        {
            "column_id": cid,
            "schema": schema,
            "table": table,
            "values": [row[0] for row in rows],
        },
        sort_keys=True,
        default=str,
    )
    return payload, True, attempt


def run_query(
    sql: str,
    *,
    bounds: ToolBounds,
    corpus: Any,
    connector: Any,
    policy: GovernancePolicy,
) -> tuple[str, Any]:
    """One ledger row per admitted call. The attempt cap is on ``AttemptBook``, which sees other executions."""
    if connector is None:
        # Missing connector is wiring. ``r_not_a_read`` would match a model that proposed a write.
        raise GovernanceUsageError(
            "run_query has no connector: configurable['connector'] is None. A missing connector "
            "is a wiring failure, and a turn served without one cannot tell a governance "
            "refusal from its own wiring failure."
        )

    if not isinstance(corpus, AnalystCorpus):
        # An unwired corpus must not look like ``r_column_not_allowed`` with no guardrail errors (ADR 0006).
        raise GovernanceUsageError(
            "run_query has no AnalystCorpus: configurable['corpus'] is "
            f"{type(corpus).__name__}. Every tool reads through AnalystCorpus as a type, "
            "not a convention (ADR 0006 §8), and a turn served without one cannot tell a "
            "governance refusal from its own wiring failure."
        )

    # Quoted spellings, scoped to retrieved tables. A corpus-wide map refuses nearly every query (ADR 0008).
    spellings, ambiguous, by_table = spellings_for(corpus, bounds.retrieved_tables)
    prepared = prepare(
        sql,
        retrieved_tables=bounds.retrieved_tables,
        corpus=corpus,
        spellings=spellings,
        ambiguous_folds=ambiguous,
        spellings_by_table=by_table,
        dialect=getattr(connector, "dialect", None) or "sqlite",
        policy=policy,
    )
    attempt = attempt_record(
        result=prepared.result,
        path="agent",
        executed_sql=prepared.sql,
        checked_sql=prepared.canonical if prepared.sql is not None else None,
        submitted_sql=sql,
    )
    if prepared.sql is None:
        detail = prepared.result.get("detail") or prepared.result.get("reason_code")
        return f"run_query refused: {detail}", attempt

    try:
        columns, rows, truncated = connector.execute(prepared.sql)
    except Exception as exc:  # noqa: BLE001 — the row is the point, not the traceback
        # Passed every layer and was sent. An error string alone looks like no attempt.
        error = f"{type(exc).__name__}: {exc}"
        return f"run_query error: {error}", {**attempt, "error": capped_error(error)}
    preview = [list(r) for r in list(rows)[:PREVIEW_ROWS]]
    body: dict[str, Any] = {
        "columns": list(columns),
        "rows": preview,
        "truncated": bool(truncated),
        "row_count": len(rows),
    }
    if len(rows) > PREVIEW_ROWS:
        # Without this, agents re-ran the statement with LIMIT and OFFSET to read on.
        body["note"] = (
            f"Only the first {PREVIEW_ROWS} of {len(rows)} rows are shown. The answer delivers every "
            "row of this statement, so do not re-run it with LIMIT or OFFSET to read more."
        )
    payload = json.dumps(body, default=str)
    return payload, attempt
