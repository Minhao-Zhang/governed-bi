"""What each governed tool actually does — the bodies, with no LangGraph in them.

Each function is of ``(identifiers, bounds, corpus, connector, policy)`` and mentions
``runtime``, ``Command`` and ``_reply`` exactly zero times; ``serve/tools.py`` is the adapter
that makes them tools.

Every function returns a **tuple** whose second element is a fact the adapter has to record:
``read_body`` returns whether the payload counts as a *delivery* (an out-of-scope refusal is
received by the model and is not one, and ``delivery_hash`` audits what was shown); ``run_query``
and ``sample_rows`` return the ledger row for the attempt.

Both executing tools take the same route — build a statement, pass it through :func:`prepare`,
ledger the verdict. ``sample_rows`` used to call ``connector.sample_values`` directly, reaching
the database through none of the layers and writing no ledger row, which made
``guardrail_errors == 0`` hold vacuously for that path and let one policy refuse a suspect
column in ``run_query`` while returning its values here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlglot import expressions as exp

from governed_bi.corpus.analyst import AnalystCorpus
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.layers import refuse
from governed_bi.govern.ledger import (
    AttemptRecord,
    attempt_record,
)
from governed_bi.govern.pipeline import prepare, spellings_for
from governed_bi.govern.policy import GovernancePolicy

_DEFAULT_READ_BODY_MAX_CHARS = 80_000

#: Ceiling on the number of distinct values ``sample_rows`` returns.
#:
#: The old bound was ``max(1, int(limit))`` — clamped from below only, so the row bound was a
#: model-supplied argument with no ceiling (ADR 0006 §8: a tool that grants privilege must have
#: a bound the model cannot widen).
#:
#: A constant rather than a knob because nothing can set a knob on this surface: ``cost_budget``
#: ships UNSET and no env var, config key or ``int_knob`` entry can write it, so a knob here
#: would be a declaration with no writer.
SAMPLE_ROWS_MAX_VALUES = 20


def read_body_cap(state: Mapping[str, Any], cfg: Mapping[str, Any]) -> int:
    for source in (state, state.get("knobs_resolved") or {}, cfg):
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
    """``(payload, delivered)``.

    The flag is why this is a tuple rather than a string: an out-of-scope refusal is a payload
    the model receives but **not** a delivery, and ``delivery_hash`` audits what the corpus
    handed over.
    """
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
    """``SELECT DISTINCT c FROM t WHERE c IS NOT NULL ORDER BY c LIMIT n``, as a tree.

    **Built as a syntax tree and rendered, never interpolated.** Postgres has no
    quote-doubling, so the f-string this replaces let a ``physical_name`` containing ``"``
    close the quote and turn the rest of the value into SQL — and ``physical_name`` holds the
    engine's identifier verbatim (any character, any case, any script; ``corpus/validate.py``
    validates only its slug). Rendering from ``exp.Identifier`` nodes puts the escaping in
    sqlglot's generator, so the identifier reaches the engine as a *name*.
    """
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
    """``(payload, delivered, attempt_row)``. A governed executor path, like ``run_query``.

    The ledger row is ``path="sample"``. It is ``None`` only when no statement was ever built:
    an out-of-scope column id produces no SQL, so there is no governance decision to record.

    Layer coverage is the point: ``check()`` refuses a suspect column at COLUMNS when
    ``hard_block_suspect`` is on, which closes the bypass where one policy refused a suspect
    column in ``run_query`` and handed over its values here. ADR 0006 §7 said the
    exclusion/suspect filter was applied "in the tool"; it was not applied anywhere.
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
        return (
            "sample_rows error: no connector configured",
            False,
            attempt_record(
                refuse("r_not_a_read", "no connector configured"), "sample", executed_sql=None
            ),
        )
    if not isinstance(corpus, AnalystCorpus):
        # G1: a missing corpus is a wiring failure, and refusing on it would record a
        # governance verdict for it.
        raise GovernanceUsageError(
            "sample_rows has no AnalystCorpus: configurable['corpus'] is "
            f"{type(corpus).__name__}. The sample path now runs through check(), which "
            "derives column authorization from AnalystCorpus as a type (ADR 0006 §8)."
        )

    table = str(asset_attr(col, "parent_table") or "")
    physical = str(asset_attr(col, "physical_name") or "")
    schema = str(asset_attr(col, "schema") or "")
    # **The engine's spelling, not the corpus key** (ADR 0008 D1: a key is not a name).
    # `parent_table.split(".")[-1]` yields the *slug* — `Air_Carriers_66c534` for the table
    # whose physical name is `Air Carriers` — which is not a relation in any engine, and the
    # schema was dropped so the connector fell back to `public`. Together they produce 42P01,
    # which surfaced as a tool error nothing counted.
    parent = assets.get(table)
    table_name = str(asset_attr(parent, "physical_name") or "") if parent is not None else ""
    if not table_name:
        table_name = table.split(".")[-1] if table else ""
    if not physical or not table_name:
        # No identifier to name a relation with. Out of scope rather than a refusal: there is
        # no statement, so there is nothing for a layer to have decided.
        return OUT_OF_SCOPE_MESSAGE, False, None

    dialect = getattr(connector, "dialect", None) or "sqlite"
    statement = distinct_values_statement(
        schema=schema,
        table=table_name,
        column=physical,
        limit=max(1, min(int(limit), SAMPLE_ROWS_MAX_VALUES)),
        dialect=dialect,
    )
    spellings, ambiguous = spellings_for(corpus, bounds.licensed)
    prepared = prepare(
        statement,
        licensed=bounds.licensed,
        corpus=corpus,
        spellings=spellings,
        ambiguous_folds=ambiguous,
        dialect=dialect,
        policy=policy,
    )
    attempt = attempt_record(verdict=prepared.verdict, path="sample", executed_sql=prepared.sql)
    if prepared.sql is None:
        detail = prepared.verdict.get("detail") or prepared.verdict.get("reason_code")
        return f"sample_rows refused: {detail}", False, attempt

    try:
        _columns, rows, _truncated = connector.execute(prepared.sql)
    except Exception as exc:  # noqa: BLE001 — the row is the point, not the traceback
        # Returned with the attempt, for the reason ``run_query`` gives: it cleared every
        # layer and was sent, so the ledger owes it a row.
        return f"sample_rows error: {type(exc).__name__}: {exc}", False, attempt
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
    """``(payload, attempt_row)``. Exactly one ledger row per admitted call.

    The cap is not decided here any more — :class:`~governed_bi.serve.agent_state.AttemptBook`
    owns it, because counting attempts requires knowing what a *previous node execution*
    committed and this function only ever saw one closure's list.
    """
    if connector is None:
        return (
            "run_query error: no connector configured",
            attempt_record(
                refuse("r_not_a_read", "no connector configured"), "agent", executed_sql=None
            ),
        )

    if not isinstance(corpus, AnalystCorpus):
        # G1: an empty corpus fails closed, so nothing leaks — but it records "the corpus was
        # never wired up" as ``r_column_not_allowed`` with ``guardrail_errors: 0``,
        # indistinguishable from "the model asked for a column it may not see". ``serve/`` must
        # not catch this and substitute a default.
        raise GovernanceUsageError(
            "run_query has no AnalystCorpus: configurable['corpus'] is "
            f"{type(corpus).__name__}. Every tool reads through AnalystCorpus as a type, "
            "not a convention (ADR 0006 §8), and a turn served without one cannot tell a "
            "governance refusal from its own wiring failure."
        )

    # ADR 0008 D2/D7. Without these two the model's spelling reaches the engine unchanged:
    # `check()` compares folded keys, so `FROM address.cbsa` matches the licensed
    # `address.CBSA` and passes every layer, then Postgres folds the unquoted name and reports
    # no such relation — 81 tables and 610 columns in the obfuscated lake failed that way,
    # each *after* a passing verdict. Scoped to `bounds.licensed`, because a corpus-wide map
    # makes `name`, `id` and `city` ambiguous and would refuse nearly every query.
    spellings, ambiguous = spellings_for(corpus, bounds.licensed)
    prepared = prepare(
        sql,
        licensed=bounds.licensed,
        corpus=corpus,
        spellings=spellings,
        ambiguous_folds=ambiguous,
        dialect=getattr(connector, "dialect", None) or "sqlite",
        policy=policy,
    )
    attempt = attempt_record(
        verdict=prepared.verdict, path="agent", executed_sql=prepared.sql
    )
    if prepared.sql is None:
        detail = prepared.verdict.get("detail") or prepared.verdict.get("reason_code")
        return f"run_query refused: {detail}", attempt

    try:
        columns, rows, truncated = connector.execute(prepared.sql)
    except Exception as exc:  # noqa: BLE001 — the row is the point, not the traceback
        # **The attempt is returned even though the execution failed.** It passed every layer
        # and was sent to the database, so it is a governed statement and the ledger owes it a
        # row; returning only the error string makes a driver failure look like a turn that
        # never attempted anything.
        return f"run_query error: {type(exc).__name__}: {exc}", attempt
    preview = [list(r) for r in list(rows)[:20]]
    payload = json.dumps(
        {
            "columns": list(columns),
            "rows": preview,
            "truncated": bool(truncated),
            "row_count": len(rows),
        },
        default=str,
    )
    return payload, attempt
