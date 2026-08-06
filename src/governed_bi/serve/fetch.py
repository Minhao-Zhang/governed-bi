"""What each governed tool actually does — the bodies, with no LangGraph in them.

**The seam is measured, not asserted.** These four functions mention ``runtime``, ``Command``
and ``_reply`` exactly **zero** times: each is a function of ``(identifiers, bounds, corpus,
connector, policy)`` returning a payload, and nothing about how that payload reaches the model.
``serve/tools.py`` is the adapter that makes them tools; this is the work.

Splitting them out is what took ``tools.py`` under ADR 0005 §6's 400-line tier, but the reason to
put the cut *here* is the boundary rather than the arithmetic: a reader asking "what does
``sample_rows`` show the model, and what does governance stop it showing" now reads one file with
no tool-call plumbing in it, and a reader asking "how does a tool answer" reads the other with no
SQL in it.

Every function returns a **tuple**, and each second element is a different fact the adapter has
to record: ``read_body`` returns whether the payload counts as a *delivery* (an out-of-scope
refusal is received by the model and is not one, and ``delivery_hash`` audits what was shown);
``run_query`` returns the ledger row for the attempt. A single string would have made both
invisible.

__all__ = ["read_body", "inspect_schema", "sample_rows", "run_query", "read_body_cap"]
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from governed_bi.corpus.analyst import AnalystCorpus
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.layers import refuse
from governed_bi.govern.ledger import (
    attempt_record,
)
from governed_bi.govern.pipeline import prepare, spellings_for
from governed_bi.govern.policy import GovernancePolicy

_DEFAULT_READ_BODY_MAX_CHARS = 80_000


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

    The flag is the reason this is a tuple rather than a string: an out-of-scope refusal is a
    payload the model receives but **not** a delivery, and ``delivery_hash`` is an audit of
    what the corpus handed over. The tracker call used to be skipped by an early ``return``,
    which encoded the same distinction where a caller could not see it.
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


def sample_rows(
    column_id: str,
    *,
    limit: int,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    connector: Any,
) -> tuple[str, bool]:
    cid = str(column_id)
    if not bounds.may_sample(cid):
        return OUT_OF_SCOPE_MESSAGE, False
    col = assets.get(cid)
    if col is None:
        return OUT_OF_SCOPE_MESSAGE, False
    at = asset_attr(col, "asset_type")
    is_col = isinstance(col, ColumnAsset) or str(getattr(at, "value", at) or "") == "column"
    if not is_col:
        return OUT_OF_SCOPE_MESSAGE, False
    if connector is None:
        return "sample_rows error: no connector configured", False
    table = str(asset_attr(col, "parent_table") or "")
    physical = str(asset_attr(col, "physical_name") or "")
    schema = str(asset_attr(col, "schema") or "")
    # **The engine's spelling, not the corpus key** (ADR 0008 D1: a key is not a name).
    # `parent_table.split(".")[-1]` yields the *slug* — `Air_Carriers_66c534` for the table
    # whose physical name is `Air Carriers` — which is not a relation in any engine. And the
    # schema was read into a local and then dropped, so the connector fell back to `public`.
    # Both halves had to be wrong for the failure to be invisible: an unqualified slug and a
    # default schema produce 42P01, which surfaced as a tool error nothing counted.
    parent = assets.get(table)
    table_name = str(asset_attr(parent, "physical_name") or "") if parent is not None else ""
    if not table_name:
        table_name = table.split(".")[-1] if table else ""
    values = list(
        connector.sample_values(
            table_name, physical, limit=max(1, int(limit)), schema=schema or None
        )
    )
    payload = json.dumps(
        {
            "column_id": cid,
            "schema": schema,
            "table": table,
            "values": values,
        },
        sort_keys=True,
        default=str,
    )
    return payload, True


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
        # G1: absence refuses. An empty corpus here fails closed, so nothing leaks — but
        # it records "the corpus was never wired up" as ``r_column_not_allowed`` with
        # ``guardrail_errors: 0``, indistinguishable from "the model asked for a column it
        # may not see". ``check()`` raises this for the same input; ``serve/`` must not
        # catch it and substitute a default.
        raise GovernanceUsageError(
            "run_query has no AnalystCorpus: configurable['corpus'] is "
            f"{type(corpus).__name__}. Every tool reads through AnalystCorpus as a type, "
            "not a convention (ADR 0006 §8), and a turn served without one cannot tell a "
            "governance refusal from its own wiring failure."
        )

    # ADR 0008 D2/D7. Without these two the model's spelling reaches the engine
    # unchanged: `check()` compares folded keys, so `FROM address.cbsa` matches the
    # licensed `address.CBSA` and passes every layer, and Postgres then folds the
    # unquoted name and reports that the relation does not exist. 81 tables and 610
    # columns in the obfuscated lake failed that way, each *after* a passing verdict.
    # Scoped to `bounds.licensed`, because a corpus-wide map makes `name`, `id` and
    # `city` ambiguous and would refuse nearly every query.
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
        # **The attempt is returned even though the execution failed.** It passed every
        # governance layer and was sent to the database, so it is a governed statement and the
        # ledger owes it a row. The old shared-box code kept the row by accident — it appended
        # before executing and the box outlived the exception — and returning only the error
        # string here would have made a driver failure look like a turn that never attempted
        # anything, which is the empty-ledger-holds-vacuously shape.
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
