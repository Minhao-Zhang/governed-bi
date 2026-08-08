"""Serve tools (ADR 0005 §3.5, ADR 0006 bounds).

Factory ``build_tools(state, config)`` closes over frozen
:class:`~governed_bi.govern.bounds.ToolBounds`. Out-of-scope and missing
identifiers share :data:`~governed_bi.govern.bounds.OUT_OF_SCOPE_MESSAGE`.
Tool exceptions become error strings — never refuse/decline — except
:class:`~governed_bi.govern.check.GovernanceUsageError`, which propagates.
Every tool returns a :class:`~langgraph.types.Command`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command, interrupt

from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.ledger import (
    statement_sha256,
)
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.prompts import prompt_text
from governed_bi.serve import fetch
from governed_bi.serve.agent_state import AttemptBook
from governed_bi.serve.delivery import payload_digest, tool_bounds_from_state
from governed_bi.serve.events import emit, tool_event_id
from governed_bi.serve.ledger import attempt_field, cap_attempt, execution_from_attempts
from governed_bi.serve.runtime import bool_knob, configurable
from governed_bi.serve.schema_term_guard import find_schema_leak
from governed_bi.serve.structured_check import percentage_scale_suffix

__all__ = [
    "SYSTEM_PROMPT",
    "build_tools",
    "resolve_assets",
    "attempt_field",
    "execution_from_attempts",
    "tool_bounds_from_state",
]

#: Agent standing instructions (from ``register/prompts.py``).
SYSTEM_PROMPT = prompt_text("analyst")

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


def _call_id(runtime: Any) -> str:
    """This tool call's id. It keys every durable thing a tool writes."""
    return str(getattr(runtime, "tool_call_id", "") or "")


def _reply(runtime: Any, text: str, **updates: Any) -> Command:
    """ToolMessage plus durable channel updates."""
    update: dict[str, Any] = {
        "messages": [ToolMessage(content=text, tool_call_id=_call_id(runtime))]
    }
    update.update(updates)
    return Command(update=update)


async def _fetch(
    stage: str,
    runtime: Any,
    detail: dict[str, Any],
    work: Callable[[], tuple[Any, ...]],
) -> Command:
    """Corpus tools with stream events. Status: ok / blocked / error.

    ``work`` returns ``(payload, delivered)``, or ``(payload, delivered, attempt)`` when the
    tool is an **executor path** and owes the ledger a row. ``sample_rows`` is that case, and
    it used to return the two-tuple — which is precisely what "the sample path writes no ledger
    entry" was: a shape with nowhere to put the fact.
    """
    event_id = tool_event_id(stage, _call_id(runtime))
    emit(kind="tool", step=stage, status="start", event_id=event_id, detail=detail)
    try:
        result = await asyncio.to_thread(work)
    except GovernanceUsageError:
        # A security parameter was never wired up (G1). Must not become an error string that
        # reads like a tool failing on the model's input.
        emit(
            kind="tool",
            step=stage,
            status="error",
            event_id=event_id,
            detail={**detail, "error_type": "GovernanceUsageError"},
        )
        raise
    except Exception as exc:  # noqa: BLE001 — tool surface
        emit(
            kind="tool",
            step=stage,
            status="error",
            event_id=event_id,
            detail={**detail, "error_type": type(exc).__name__},
        )
        return _reply(runtime, f"{stage} error: {type(exc).__name__}: {exc}")
    payload, delivered = result[0], result[1]
    attempt = result[2] if len(result) > 2 else None
    if delivered:
        status = "ok"
    elif payload == OUT_OF_SCOPE_MESSAGE or (attempt is not None and not attempt["passed"]):
        status = "blocked"
    else:
        status = "error"
    updates: dict[str, Any] = dict(_delivered(runtime, payload)) if delivered else {}
    if attempt is not None:
        # The verdict rides **this** step's detail rather than emitting ``check`` / ``execute``
        # rows. Those two steps are ``run_query``'s attempt numbering, and a sample verdict
        # appearing among them would read as a SQL attempt the model never made.
        detail = {
            **detail,
            "layer": attempt["verdict_layer"],
            "reason_code": attempt["reason_code"],
            "executed": attempt["executed_sql"] is not None,
        }
        if attempt["executed_sql"] is not None:
            detail["sql_sha256"] = statement_sha256(attempt["executed_sql"])
        # **The ledger row rides the same Command as the payload.** With no ``attempts_by_call``
        # write on this path, ``guardrail_errors == 0`` and an empty attempt list were true of
        # every value the tool ever showed the model.
        updates["attempts_by_call"] = {f"{stage}:{_call_id(runtime)}": attempt}
    emit(kind="tool", step=stage, status=status, event_id=event_id, detail=detail)
    return _reply(runtime, payload, **updates)


def _emit_attempt(runtime: Any, attempt: Mapping[str, Any], *, number: int, payload: str) -> None:
    """Emit ``check`` and (when executed) ``execute`` stream events — no ``run_query`` step."""
    call_id = _call_id(runtime)
    passed = bool(attempt.get("passed"))
    # null layer on pass is the observation ("no layer refused"), not a missing field.
    emit(
        kind="tool",
        step="check",
        status="ok" if passed else "blocked",
        event_id=tool_event_id("check", call_id),
        detail={
            "attempt": number,
            "layer": attempt.get("verdict_layer"),
            "reason_code": attempt.get("reason_code"),
        },
    )

    executed = attempt.get("executed_sql")
    if executed is None:
        return

    # Status from payload shape, not from ``passed`` (driver can fail after clearing layers).
    detail: dict[str, Any] = {"sql": executed, "sql_sha256": statement_sha256(executed)}
    status = "error"
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, Mapping) and "row_count" in decoded:
        status = "ok"
        detail["row_count"] = decoded.get("row_count")
        detail["truncated"] = bool(decoded.get("truncated"))
        columns = decoded.get("columns")
        if isinstance(columns, (list, tuple)):
            detail["n_columns"] = len(columns)
    emit(
        kind="tool",
        step="execute",
        status=status,
        event_id=tool_event_id("execute", call_id),
        detail=detail,
    )


def _result_table(payload: str) -> dict[str, Any] | None:
    """Structured result table from a successful query payload, or ``None``."""
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, Mapping) or "row_count" not in decoded:
        return None
    columns = decoded.get("columns")
    rows = decoded.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return None
    return {
        "columns": [str(c) for c in columns],
        "rows": rows,
        "row_count": decoded.get("row_count"),
        "truncated": bool(decoded.get("truncated")),
        "preview_rows": len(rows),
    }


def _delivered(runtime: Any, payload: str) -> dict[str, Any]:
    """Delivery digest keyed by tool call id."""
    return {"tool_delivered": {_call_id(runtime): payload_digest(payload)}}


def build_tools(
    state: Mapping[str, Any],
    config: Mapping[str, Any] | None,
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
    # Passed through as whatever is on ``configurable``. A wrong-typed or absent corpus
    # is a wiring failure and ``_run_query`` raises on it; coercing it to ``None`` here
    # is what let the default below stand in for it (G1).
    corpus = cfg.get("corpus")
    read_cap = fetch.read_body_cap(state, cfg)
    book = AttemptBook(policy.run_query_attempt_cap)
    turn_id = str(state.get("turn_id") or "")

    @tool
    async def read_body(asset_ids: list[str], runtime: ToolRuntime) -> Command:
        """Return bodies for retrieved asset ids (hits ∪ pulled_in only)."""
        return await _fetch(
            "read_body",
            runtime,
            {"n_asset_ids": len(asset_ids or ())},
            lambda: fetch.read_body(asset_ids, bounds=bounds, assets=assets, max_chars=read_cap),
        )

    @tool
    async def inspect_schema(table_id: str, runtime: ToolRuntime) -> Command:
        """List columns and physical types for a licensed table."""
        return await _fetch(
            "inspect_schema",
            runtime,
            {"table_id": table_id},
            lambda: fetch.inspect_schema(table_id, bounds=bounds, assets=assets),
        )

    @tool
    async def sample_rows(column_id: str, runtime: ToolRuntime, limit: int = 5) -> Command:
        """Sample distinct values for a ColumnAsset id whose table is licensed."""
        return await _fetch(
            "sample_rows",
            runtime,
            {"column_id": column_id, "limit": limit},
            lambda: fetch.sample_rows(
                column_id,
                limit=limit,
                bounds=bounds,
                assets=assets,
                connector=connector,
                corpus=corpus,
                policy=policy,
            ),
        )

    @tool
    async def run_query(sql: str, runtime: ToolRuntime) -> Command:
        """Governed SQL execution against licensed tables only."""
        call_id = _call_id(runtime)
        committed = (getattr(runtime, "state", None) or {}).get("attempts_by_call")
        if not book.admit(committed, call_id):
            update: dict[str, Any] = {}
            if not book.cap_recorded:
                book.cap_recorded = True
                update["attempts_by_call"] = {f"cap:{call_id}": cap_attempt()}
                emit(
                    kind="tool",
                    step="cap",
                    status="cap",
                    event_id=tool_event_id("cap", call_id),
                    detail={"cap": book.cap},
                )
            return _reply(runtime, f"run_query capped: attempt limit {book.cap} reached", **update)
        attempt_number = book.charged(committed)
        emit(
            kind="tool",
            step="check",
            status="start",
            event_id=tool_event_id("check", call_id),
            detail={"attempt": attempt_number},
        )
        try:
            payload, attempt = await asyncio.to_thread(
                fetch.run_query,
                sql,
                bounds=bounds,
                corpus=corpus,
                connector=connector,
                policy=policy,
            )
        except GovernanceUsageError:
            # Wiring failure — must not look like a governance refusal.
            emit(
                kind="tool",
                step="check",
                status="error",
                event_id=tool_event_id("check", call_id),
                detail={"attempt": attempt_number, "error_type": "GovernanceUsageError"},
            )
            raise
        except Exception as exc:  # noqa: BLE001
            book.refund(call_id)
            emit(
                kind="tool",
                step="check",
                status="error",
                event_id=tool_event_id("check", call_id),
                detail={"attempt": attempt_number, "error_type": type(exc).__name__},
            )
            return _reply(runtime, f"run_query error: {type(exc).__name__}: {exc}")
        _emit_attempt(runtime, attempt, number=attempt_number, payload=payload)
        table = _result_table(payload)
        suffix = ""
        if bool_knob(state, "enable_structured_percentage_check"):
            # G4 (ADR 0006): check what was executed, not what the model asked for.
            suffix = percentage_scale_suffix(state.get("question"), attempt_field(attempt, "executed_sql"))
        return _reply(
            runtime,
            payload + suffix,
            attempts_by_call={call_id: attempt},
            **({"result_table": table} if table is not None else {}),
        )

    @tool
    async def ask_user(question: str, runtime: ToolRuntime, why: str = "") -> Command:
        """Pause and ask the human a clarifying question (HITL interrupt).

        ``question``/``why`` reach a business user, never an engineer — write them in
        plain language, with no table/column names, no dotted `table.column` paths, and
        no snake_case or camelCase identifiers. A leaked identifier is rejected before
        this pauses the turn; rephrase and call ``ask_user`` again.
        """
        leak = find_schema_leak(question, why)
        if leak is not None:
            return _reply(
                runtime,
                f"ask_user rejected: {leak!r} looks like a raw schema identifier, not "
                "plain business language. Rephrase question/why without table.column "
                "paths, snake_case, or camelCase identifiers, then call ask_user again.",
            )
        digest = hashlib.sha256(f"{turn_id}\x1f{question}".encode()).hexdigest()[:12]
        clarification_id = f"clar-{turn_id}-{digest}"
        started = {"clarification_id": clarification_id}
        emit(
            kind="tool",
            step="ask_user",
            status="start",
            event_id=tool_event_id("ask_user", _call_id(runtime)),
            detail=started,
        )
        answer = interrupt(
            {
                "kind": "clarification",
                "clarification_id": clarification_id,
                "question": question,
                "why": why or "The question is ambiguous and the answer depends on which reading is meant.",
            }
        )
        text = _clarification_answer(answer)
        declined = bool(answer.get("declined")) if isinstance(answer, Mapping) else False
        emit(
            kind="tool",
            step="ask_user",
            status="declined" if declined else "ok",
            event_id=tool_event_id("ask_user", _call_id(runtime)),
            detail=started,
        )
        return _reply(
            runtime,
            text,
            clarifications_by_call={
                _call_id(runtime): {
                    "clarification_id": clarification_id,
                    "question": question,
                    "why": why,
                    "answer": text,
                    "turn_id": turn_id,
                }
            },
        )

    return [read_body, inspect_schema, sample_rows, run_query, ask_user]


def _clarification_answer(resume: Any) -> str:
    """Human answer from a bare string or structured ``{answer|choice_id|declined}`` reply."""
    if isinstance(resume, Mapping):
        if resume.get("declined"):
            return "The user declined to answer this clarification."
        for key in ("answer", "choice_id", "text"):
            value = resume.get(key)
            if value:
                return str(value)
        return ""
    return str(resume)
