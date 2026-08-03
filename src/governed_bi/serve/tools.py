"""Serve tools (ADR 0005 §3.5, ADR 0006 bounds).

Factory ``build_tools(state, config, tracker)`` closes over frozen
:class:`~governed_bi.govern.bounds.ToolBounds`. Out-of-scope and missing
identifiers share :data:`~governed_bi.govern.bounds.OUT_OF_SCOPE_MESSAGE`.
Tool exceptions become error strings — never refuse/decline.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.tools import tool
from langgraph.types import interrupt

from governed_bi.corpus.analyst import AnalystCorpus, analyst_corpus_from_keys
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from governed_bi.govern.layers import refuse
from governed_bi.govern.ledger import attempt_record, execution_record
from governed_bi.govern.pipeline import prepare
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.delivery import DeliveryTracker, tool_bounds_from_state
from governed_bi.serve.runtime import configurable

__all__ = [
    "SYSTEM_PROMPT",
    "build_tools",
    "resolve_assets",
    "clarifications_from_tools",
    "attempts_from_tools",
    "execution_from_attempts",
    "tool_bounds_from_state",
]

SYSTEM_PROMPT = (
    "You are a governed BI analyst. Use only the context and tools provided. "
    "Prefer run_query for factual answers. Call ask_user only when a missing "
    "fact blocks a correct SQL answer."
)

_DEFAULT_READ_BODY_MAX_CHARS = 80_000


def resolve_assets(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """``assets_by_id`` or ``corpus.by_id`` from configurable."""
    cfg = configurable(config)
    direct = cfg.get("assets_by_id")
    if isinstance(direct, Mapping) and direct:
        return {str(k): v for k, v in direct.items()}
    corpus = cfg.get("corpus")
    if corpus is None:
        return {}
    by_id = getattr(corpus, "by_id", None)
    if isinstance(by_id, Mapping):
        return {str(k): v for k, v in by_id.items()}
    if isinstance(corpus, Mapping):
        return {str(k): v for k, v in corpus.items()}
    return {}


def build_tools(
    state: Mapping[str, Any],
    config: Mapping[str, Any] | None,
    tracker: DeliveryTracker,
    *,
    bounds: ToolBounds | None = None,
) -> list[Any]:
    """Build the five ADR tools closed over this turn's bounds + corpus."""
    cfg = configurable(config)
    bounds = bounds or tool_bounds_from_state(state)
    assets = resolve_assets(config)
    policy = cfg.get("policy")
    if not isinstance(policy, GovernancePolicy):
        policy = GovernancePolicy()
    connector = cfg.get("connector")
    corpus = cfg.get("corpus")
    if not isinstance(corpus, AnalystCorpus):
        corpus = None
    read_cap = _read_body_cap(state, cfg)
    attempts_box: dict[str, Any] = {
        "attempts": list((state.get("execution") or {}).get("attempts") or ()),
        "cap": int(getattr(policy, "run_query_attempt_cap", 3) or 3),
    }
    turn_id = str(state.get("turn_id") or "")
    clar_box: list[dict[str, Any]] = []

    @tool
    def read_body(asset_ids: list[str]) -> str:
        """Return bodies for retrieved asset ids (hits ∪ pulled_in only)."""
        try:
            return _read_body(
                asset_ids,
                bounds=bounds,
                assets=assets,
                max_chars=read_cap,
                tracker=tracker,
                call_id=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001 — tool surface
            return f"read_body error: {type(exc).__name__}: {exc}"

    @tool
    def inspect_schema(table_id: str) -> str:
        """List columns and physical types for a licensed table."""
        try:
            return _inspect_schema(
                table_id,
                bounds=bounds,
                assets=assets,
                tracker=tracker,
                call_id=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            return f"inspect_schema error: {type(exc).__name__}: {exc}"

    @tool
    def sample_rows(column_id: str, limit: int = 5) -> str:
        """Sample distinct values for a ColumnAsset id whose table is licensed."""
        try:
            return _sample_rows(
                column_id,
                limit=limit,
                bounds=bounds,
                assets=assets,
                connector=connector,
                tracker=tracker,
                call_id=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            return f"sample_rows error: {type(exc).__name__}: {exc}"

    @tool
    def run_query(sql: str) -> str:
        """Governed SQL execution against licensed tables only."""
        try:
            return _run_query(
                sql,
                bounds=bounds,
                corpus=corpus,
                connector=connector,
                policy=policy,
                attempts_box=attempts_box,
            )
        except Exception as exc:  # noqa: BLE001
            return f"run_query error: {type(exc).__name__}: {exc}"

    @tool
    def ask_user(question: str) -> str:
        """Pause and ask the human a clarifying question (HITL interrupt)."""
        answer = interrupt({"type": "ask_user", "question": question})
        text = str(answer)
        clar_box.append({"question": question, "answer": text, "turn_id": turn_id})
        return text

    run_query._governed_attempts_box = attempts_box  # type: ignore[attr-defined]
    ask_user._governed_clar_box = clar_box  # type: ignore[attr-defined]
    return [read_body, inspect_schema, sample_rows, run_query, ask_user]


def clarifications_from_tools(tools: Sequence[Any]) -> list[dict[str, Any]]:
    for t in tools:
        box = getattr(t, "_governed_clar_box", None)
        if box is not None:
            return list(box)
    return []


def attempts_from_tools(tools: Sequence[Any]) -> list[Any]:
    for t in tools:
        box = getattr(t, "_governed_attempts_box", None)
        if isinstance(box, dict):
            return list(box.get("attempts") or ())
    return []


def execution_from_attempts(attempts: Sequence[Any], *, has_sql: bool) -> dict[str, Any]:
    terminal: str = "answered" if has_sql else "no_sql"
    return execution_record(list(attempts), terminal)  # type: ignore[arg-type]


def _read_body_cap(state: Mapping[str, Any], cfg: Mapping[str, Any]) -> int:
    for source in (state, state.get("knobs_resolved") or {}, cfg):
        if not isinstance(source, Mapping):
            continue
        raw = source.get("read_body_max_tokens")
        if raw is not None:
            return max(256, int(raw) * 4)
    return _DEFAULT_READ_BODY_MAX_CHARS


def _asset_attr(asset: Any, name: str) -> Any:
    if isinstance(asset, Mapping):
        return asset.get(name)
    return getattr(asset, name, None)


def _read_body(
    asset_ids: Sequence[str],
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    max_chars: int,
    tracker: DeliveryTracker,
    call_id: str,
) -> str:
    parts: list[str] = []
    used = 0
    for raw_id in asset_ids:
        aid = str(raw_id)
        if not bounds.may_read_body(aid):
            return OUT_OF_SCOPE_MESSAGE
        asset = assets.get(aid)
        if asset is None:
            return OUT_OF_SCOPE_MESSAGE
        body = _asset_attr(asset, "body")
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
    tracker.record(call_id, payload)
    return payload


def _inspect_schema(
    table_id: str,
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    tracker: DeliveryTracker,
    call_id: str,
) -> str:
    tid = str(table_id)
    if not bounds.may_inspect_schema(tid):
        return OUT_OF_SCOPE_MESSAGE
    table = assets.get(tid)
    if table is None or not _asset_is_table(table):
        return OUT_OF_SCOPE_MESSAGE
    columns: list[dict[str, Any]] = []
    for col_id in _asset_attr(table, "columns") or ():
        col = assets.get(str(col_id))
        if col is None:
            columns.append({"id": str(col_id)})
            continue
        columns.append(
            {
                "id": str(_asset_attr(col, "id") or col_id),
                "physical_name": _asset_attr(col, "physical_name"),
                "physical_type": _asset_attr(col, "physical_type"),
                "nullable": _asset_attr(col, "nullable"),
            }
        )
    payload = json.dumps(
        {
            "table_id": tid,
            "physical_name": _asset_attr(table, "physical_name"),
            "schema": _asset_attr(table, "schema"),
            "columns": columns,
        },
        sort_keys=True,
        default=str,
    )
    tracker.record(call_id, payload)
    return payload


def _asset_is_table(asset: Any) -> bool:
    if isinstance(asset, TableAsset):
        return True
    at = _asset_attr(asset, "asset_type")
    return str(getattr(at, "value", at) or "") == "table"


def _sample_rows(
    column_id: str,
    *,
    limit: int,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    connector: Any,
    tracker: DeliveryTracker,
    call_id: str,
) -> str:
    cid = str(column_id)
    if not bounds.may_sample(cid):
        return OUT_OF_SCOPE_MESSAGE
    col = assets.get(cid)
    if col is None:
        return OUT_OF_SCOPE_MESSAGE
    at = _asset_attr(col, "asset_type")
    is_col = isinstance(col, ColumnAsset) or str(getattr(at, "value", at) or "") == "column"
    if not is_col:
        return OUT_OF_SCOPE_MESSAGE
    if connector is None:
        return "sample_rows error: no connector configured"
    table = str(_asset_attr(col, "parent_table") or "")
    physical = str(_asset_attr(col, "physical_name") or "")
    schema = str(_asset_attr(col, "schema") or "")
    table_name = table.split(".")[-1] if table else ""
    values = list(
        connector.sample_values(table_name, physical, limit=max(1, int(limit)))
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
    tracker.record(call_id, payload)
    return payload


def _run_query(
    sql: str,
    *,
    bounds: ToolBounds,
    corpus: AnalystCorpus | None,
    connector: Any,
    policy: GovernancePolicy,
    attempts_box: dict[str, Any],
) -> str:
    attempts: list[Any] = attempts_box["attempts"]
    cap = int(attempts_box["cap"])
    if len(attempts) >= cap:
        return f"run_query capped: attempt limit {cap} reached"

    if connector is None:
        attempts.append(
            attempt_record(
                refuse("r_not_a_read", "no connector configured"),
                "agent",
            )
        )
        return "run_query error: no connector configured"

    if corpus is None:
        corpus = analyst_corpus_from_keys(allowed=())

    prepared = prepare(
        sql,
        licensed=bounds.licensed,
        corpus=corpus,
        dialect=getattr(connector, "dialect", None) or "sqlite",
        policy=policy,
    )
    attempts.append(attempt_record(verdict=prepared.verdict, path="agent"))
    if prepared.sql is None:
        detail = prepared.verdict.get("detail") or prepared.verdict.get("reason_code")
        return f"run_query refused: {detail}"

    columns, rows, truncated = connector.execute(prepared.sql)
    preview = [list(r) for r in list(rows)[:20]]
    return json.dumps(
        {
            "columns": list(columns),
            "rows": preview,
            "truncated": bool(truncated),
            "row_count": len(rows),
        },
        default=str,
    )
