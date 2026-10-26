"""Tools closed over this turn's bounds (ADR 0005 §3.5, ADR 0006).

Out-of-scope and missing identifiers share one message. Tool exceptions become
error strings, except :class:`~governed_bi.govern.check.GovernanceUsageError`."""

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

from governed_bi.corpus.schema import TermAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.ledger import (
    ExecutorPath,
    statement_sha256,
)
from governed_bi.govern.policy import DEFAULT_DIALECT, GovernancePolicy
from governed_bi.registry.prompts import prompt_text
from governed_bi.serve import fetch
from governed_bi.serve.agent_state import CAP_LEDGER_KEY, AttemptBook
from governed_bi.serve.assumptions import (
    AmbiguousGuess,
    AssumptionClaim,
    ambiguous_guesses,
    check_layer_claims,
    delivered_statement,
    found_in_query,
    merged,
)
from governed_bi.serve.clarification import (
    interrupt_payload,
    parse_basis,
    parse_resume,
)
from governed_bi.serve.delivery import payload_digest, tool_bounds_from_state
from governed_bi.serve.events import emit, tool_event_id
from governed_bi.serve.ledger import (
    attempt_field,
    cap_attempt,
    execution_from_attempts,
    pipeline_error_attempt,
)
from governed_bi.serve.paging import is_page_of
from governed_bi.serve.runtime import bool_setting, configurable, prompt_variants
from governed_bi.serve.schema_term_guard import find_schema_leak
from governed_bi.serve.structured_check import percentage_scale_suffix

__all__ = [
    "analyst_prompt",
    "build_tools",
    "policy_from_config",
    "resolve_assets",
    "attempt_field",
    "execution_from_attempts",
    "tool_bounds_from_state",
]


def analyst_prompt(config: Mapping[str, Any] | None = None) -> str:
    """This run's variant. An import-time constant would send the default under the override's hash."""
    return prompt_text("analyst", prompt_variants(config))

def resolve_assets(config: Mapping[str, Any] | None) -> dict[str, Any]:
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


def policy_from_config(config: Mapping[str, Any] | None) -> GovernancePolicy:
    """The turn's policy, or a default. One reader, so the cap cannot diverge from ``agent_core``."""
    policy = configurable(config).get("policy")
    return policy if isinstance(policy, GovernancePolicy) else GovernancePolicy()


def _call_id(runtime: Any) -> str:
    """Tool-call id. It keys every durable write this call makes."""
    return str(getattr(runtime, "tool_call_id", "") or "")


def _reply(runtime: Any, text: str, **updates: Any) -> Command:
    update: dict[str, Any] = {
        "messages": [ToolMessage(content=text, tool_call_id=_call_id(runtime))]
    }
    update.update(updates)
    return Command(update=update)


def _attempt_budget(text: str, number: int, cap: int) -> str:
    """Name the slot spent, so the model can see the cap."""
    return f"{text}\nattempt {number} of {cap}"


async def _fetch(
    stage: str,
    runtime: Any,
    detail: dict[str, Any],
    work: Callable[[], tuple[Any, ...]],
    ledger_path: ExecutorPath | None = None,
) -> Command:
    """Off the loop. An executor path still owes a ledger row when it dies before a result."""
    event_id = tool_event_id(stage, _call_id(runtime))
    emit(kind="tool", step=stage, status="start", event_id=event_id, detail=detail)
    try:
        result = await asyncio.to_thread(work)
    except GovernanceUsageError:
        # A security parameter was never wired (G1). Must not look like a bad argument.
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
        updates: dict[str, Any] = {}
        if ledger_path is not None:
            updates["attempts_by_call"] = {
                _call_id(runtime): pipeline_error_attempt(
                    ledger_path, f"{type(exc).__name__}: {exc}"
                )
            }
        return _reply(runtime, f"{stage} error: {type(exc).__name__}: {exc}", **updates)
    payload, delivered = result[0], result[1]
    attempt = result[2] if len(result) > 2 else None
    if delivered:
        status = "ok"
    elif payload == OUT_OF_SCOPE_MESSAGE or (attempt is not None and not attempt["passed"]):
        status = "blocked"
    else:
        status = "error"
    updates = dict(_delivered(runtime, payload)) if delivered else {}
    if attempt is not None:
        # ``check`` and ``execute`` numbering belongs to ``run_query``, not this step.
        detail = {
            **detail,
            "layer": attempt["result_layer"],
            "reason_code": attempt["reason_code"],
            "executed": attempt["executed_sql"] is not None,
        }
        if attempt["executed_sql"] is not None:
            detail["sql_sha256"] = statement_sha256(attempt["executed_sql"])
        # Same Command as the payload. An empty attempt list reads as a clean pass.
        updates["attempts_by_call"] = {f"{stage}:{_call_id(runtime)}": attempt}
    emit(kind="tool", step=stage, status=status, event_id=event_id, detail=detail)
    return _reply(runtime, payload, **updates)


def _emit_attempt(runtime: Any, attempt: Mapping[str, Any], *, number: int, payload: str) -> None:
    """``check``, and ``execute`` only when a statement ran."""
    call_id = _call_id(runtime)
    passed = bool(attempt.get("passed"))
    # A null layer on pass means no layer refused.
    emit(
        kind="tool",
        step="check",
        status="ok" if passed else "blocked",
        event_id=tool_event_id("check", call_id),
        detail={
            "attempt": number,
            "layer": attempt.get("result_layer"),
            "reason_code": attempt.get("reason_code"),
        },
    )

    executed = attempt.get("executed_sql")
    if executed is None:
        return

    # Status from the payload. The driver can fail after every layer cleared.
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


PAGE_NOTE = (
    "\nThis re-ran an earlier statement with OFFSET, so it only read more rows. The answer "
    "delivers every row of the earlier statement, not this page."
)


def _reads_page(attempt: Any, committed: Any, call_id: str, *, dialect: str | None) -> bool:
    """Against earlier statements that ran cleanly on the answering path, not samples or other pages."""
    if attempt_field(attempt, "executed_sql") is None:
        return False
    earlier = [
        attempt_field(row, "checked_sql")
        for key, row in (committed or {}).items()
        if key != call_id
        and attempt_field(row, "path") == "agent"
        and attempt_field(row, "executed_sql") is not None
        and not attempt_field(row, "error")
        and not attempt_field(row, "reads_page")
    ]
    return is_page_of(attempt_field(attempt, "checked_sql"), earlier, dialect=dialect or DEFAULT_DIALECT)


def _result_table(payload: str) -> dict[str, Any] | None:
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
    return {"tool_delivered": {_call_id(runtime): payload_digest(payload)}}


_MAX_CHOICES = 4


def _choices_reply(choices: Any) -> str | None:
    """A correction the agent can apply, or ``None`` when ``choices`` may be sent."""
    if choices is None:
        return None
    if not isinstance(choices, list) or any(not isinstance(item, str) for item in choices):
        return (
            "ask_user rejected: choices must be a list of at most four short strings. "
            "Call ask_user again."
        )
    if len(choices) > _MAX_CHOICES:
        return (
            "ask_user rejected: choices accepts at most four short strings, "
            f"got {len(choices)}. Call ask_user again with four or fewer."
        )
    if any(not item.strip() for item in choices):
        return (
            "ask_user rejected: a choice was empty. Give each choice a short phrase "
            "and call ask_user again."
        )
    return None


def build_tools(
    state: Mapping[str, Any],
    config: Mapping[str, Any] | None,
    *,
    bounds: ToolBounds | None = None,
    answer_epoch: str | None = None,
) -> list[Any]:
    cfg = configurable(config)
    bounds = bounds or tool_bounds_from_state(state)
    assets = resolve_assets(config)
    policy = policy_from_config(config)
    connector = cfg.get("connector")
    # Uncoerced. A wrong-typed corpus must raise, not become ``None`` (G1).
    corpus = cfg.get("corpus")
    read_cap = fetch.read_body_cap(state, cfg)
    book = AttemptBook(policy.run_query_attempt_cap)
    turn_id = str(state.get("turn_id") or "")
    epoch = turn_id if answer_epoch is None else answer_epoch
    question = str(state.get("question") or "")

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
        """List columns and physical types for a retrieved table."""
        return await _fetch(
            "inspect_schema",
            runtime,
            {"table_id": table_id},
            lambda: fetch.inspect_schema(table_id, bounds=bounds, assets=assets),
        )

    @tool
    async def sample_rows(column_id: str, runtime: ToolRuntime, limit: int = 5) -> Command:
        """Sample distinct values for a ColumnAsset id whose table is retrieved_tables."""
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
            ledger_path="sample",
        )

    @tool
    async def run_query(sql: str, runtime: ToolRuntime) -> Command:
        """Governed SQL execution against retrieved tables only."""
        call_id = _call_id(runtime)
        committed = (getattr(runtime, "state", None) or {}).get("attempts_by_call")
        if not book.admit(committed, call_id):
            # Backstop when there is no agent middleware. ``CAP_LEDGER_KEY`` makes a second write a no-op.
            update: dict[str, Any] = {"attempts_by_call": {CAP_LEDGER_KEY: cap_attempt()}}
            if not book.cap_recorded:
                book.cap_recorded = True
                emit(
                    kind="tool",
                    step="cap",
                    status="cap",
                    event_id=tool_event_id("cap", call_id),
                    detail={"cap": book.cap},
                )
            return _reply(
                runtime,
                _attempt_budget(
                    f"run_query capped: attempt limit {book.cap} reached",
                    book.cap,
                    book.cap,
                ),
                **update,
            )
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
            # Wiring failure. Must not look like a governance refusal.
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
            # The checker broke before a result. An empty ledger reads as answered.
            return _reply(
                runtime,
                _attempt_budget(
                    f"run_query error: {type(exc).__name__}: {exc}",
                    attempt_number,
                    book.cap,
                ),
                attempts_by_call={
                    call_id: pipeline_error_attempt(
                        "agent", f"{type(exc).__name__}: {exc}", submitted_sql=sql
                    )
                },
            )
        page = _reads_page(attempt, committed, call_id, dialect=getattr(connector, "dialect", None))
        if page:
            attempt = {**attempt, "reads_page": True}
        _emit_attempt(runtime, attempt, number=attempt_number, payload=payload)
        # A page keeps the earlier statement's table, the one the answer delivers.
        table = None if page else _result_table(payload)
        # Hint from ``executed_sql``, on the reply text only. Layers rewrite the
        # argument, and ``result_table`` stays free of the hint.
        suffix = ""
        if bool_setting(state, "enable_structured_percentage_check"):
            suffix = percentage_scale_suffix(
                state.get("question"), attempt_field(attempt, "executed_sql")
            )
        return _reply(
            runtime,
            _attempt_budget(payload + suffix + (PAGE_NOTE if page else ""), attempt_number, book.cap),
            attempts_by_call={call_id: attempt},
            **({"result_table": table} if table is not None else {}),
        )

    # One outstanding question. Two sends share this closure, so a second
    # pause would record the answer on the wrong one.
    pending_clarification: list[str] = []

    @tool
    async def ask_user(
        question: str,
        basis: str,
        runtime: ToolRuntime,
        why: str = "",
        choices: list[str] | None = None,
    ) -> Command:
        """Pause for one clarification in plain language, with no table or column names.

        A question or choice that names a schema identifier is rejected before the turn pauses.
        ``choices`` is at most four short strings, and is omitted from the payload when empty.
        """
        if pending_clarification:
            # Refused, not queued. No await before the append, so two sends cannot both pass.
            return _reply(
                runtime,
                "Only one clarifying question may be outstanding at a time, and this turn "
                "already has one. Ask the single question whose answer most changes the SQL; "
                "if more are needed, ask them after this one is answered.",
            )
        # Reject before the latch and before ``interrupt``: pausing would show the leak, and ``why`` is rendered.
        choice_reply = _choices_reply(choices)
        if choice_reply is not None:
            return _reply(runtime, choice_reply)
        cleaned = [item.strip() for item in choices] if choices else None
        leak = find_schema_leak(question, why, *(cleaned or ()))
        if leak is not None:
            return _reply(
                runtime,
                f"ask_user rejected: {leak!r} looks like a raw schema identifier, not plain "
                "business language, and this question is shown to a business reader. Rephrase "
                "question, why, and choices without table.column paths, snake_case, or camelCase "
                "identifiers, then call ask_user again.",
            )
        resolved_basis = parse_basis(basis)
        if resolved_basis is None:
            return _reply(
                runtime,
                "ask_user rejected: basis must be 'data_definition' (which reading of a "
                "measure) or 'ranking_ambiguity' (which ranking). Unknown value "
                f"{basis!r}. Call ask_user again with one of those two.",
            )
        why_text = why or (
            "The question is ambiguous and the answer depends on which reading is meant."
        )
        digest = hashlib.sha256(f"{turn_id}\x1f{question}".encode()).hexdigest()[:12]
        clarification_id = f"clar-{turn_id}-{digest}"
        pending_clarification.append(clarification_id)
        started = {"clarification_id": clarification_id}
        # Before ``interrupt``, so a resumed client sees an open row. Same ``event_id`` both times (ADR 0010).
        emit(
            kind="tool",
            step="ask_user",
            status="start",
            event_id=tool_event_id("ask_user", _call_id(runtime)),
            detail=started,
        )
        answer = interrupt(
            interrupt_payload(
                clarification_id=clarification_id,
                question=question,
                why=why_text,
                basis=resolved_basis,
                choices=cleaned,
            )
        )
        # Released when ``interrupt`` returns, so a later question in this execution can pause.
        pending_clarification.remove(clarification_id)
        text, resolution, fail_closed = parse_resume(
            answer, why=why_text, expected_id=clarification_id
        )
        emit(
            kind="tool",
            step="ask_user",
            status="declined" if fail_closed else "ok",
            event_id=tool_event_id("ask_user", _call_id(runtime)),
            detail=started,
        )
        row = {
            "clarification_id": clarification_id,
            "question": question,
            "why": why_text,
            "answer": text,
            "turn_id": turn_id,
            "basis": resolved_basis,
            "deferred": resolution == "deferred",
            "resolution": resolution,
        }
        return _reply(
            runtime,
            text,
            clarification_requested=fail_closed,
            clarifications_by_call={_call_id(runtime): row},
        )

    @tool
    async def answer(
        answer_text: str,
        assumptions: list[AssumptionClaim],
        runtime: ToolRuntime,
    ) -> Command:
        """Record the answer and every assumption it depends on.

        A layer claim the server cannot back is sent back once. A guess about a phrase
        two retrieved terms bind differently is refused on every call.
        """
        claims = [
            item if isinstance(item, AssumptionClaim) else AssumptionClaim.model_validate(item)
            for item in assumptions or []
        ]
        known_assets = set(assets)
        retrieved_assets = bounds.readable_assets | bounds.retrieved_tables
        checked = check_layer_claims(
            claims,
            known_assets=known_assets,
            retrieved_assets=retrieved_assets,
        )
        # Every call: a later one with the same guess must not become the answer.
        conflicts = ambiguous_guesses(
            checked, _retrieved_terms(assets, bounds.readable_assets)
        )
        if conflicts:
            return _reply(
                runtime,
                _ambiguous_reply(conflicts),
                ambiguous_guess_refusals=1,
            )
        # The first downgrade is a reply the agent can correct. A later call is the answer.
        bad = _downgraded_asset_ids(claims, checked)
        if bad and _completed_answer_calls(runtime) == 0:
            named = ", ".join(bad)
            return _reply(
                runtime,
                "answer rejected: a layer claim named an asset that does not exist or was "
                f"not retrieved this turn ({named}). Fix the basis or the asset id, then "
                "call answer again.",
            )
        turn_state = getattr(runtime, "state", None) or {}
        attempts = [
            row
            for row in (turn_state.get("attempts_by_call") or {}).values()
            if isinstance(row, Mapping)
        ]
        suspect = getattr(corpus, "suspect_columns", ()) or ()
        found = found_in_query(
            delivered_statement(attempts),
            question=str(turn_state.get("question") or question),
            suspect_columns=suspect,
        )
        return _reply(
            runtime,
            "Answer recorded.",
            answer_text=answer_text,
            assumptions=merged(checked, found),
            answer_accepted_epoch=epoch,
        )

    return [read_body, inspect_schema, sample_rows, run_query, ask_user, answer]


def _retrieved_terms(assets: Mapping[str, Any], readable: Any) -> list[TermAsset]:
    """Term assets this turn retrieved. ``readable`` is ``bounds.readable_assets``."""
    return [
        asset
        for asset_id, asset in assets.items()
        if str(asset_id) in readable and isinstance(asset, TermAsset)
    ]


def _ambiguous_reply(conflicts: list[AmbiguousGuess]) -> str:
    pieces = []
    for item in conflicts:
        terms = ", ".join(
            f"{term['id']} ({term['name']}, {term['binding']})" for term in item["terms"]
        )
        pieces.append(f"{item['about']}: {terms}")
    listed = "; ".join(pieces)
    return (
        "answer rejected: a guess is about a phrase the layer defines more than one way "
        f"({listed}). Call ask_user with basis 'data_definition' to ask which one the person means."
    )


def _downgraded_asset_ids(claims: list[AssumptionClaim], checked: list[Any]) -> list[str]:
    """Asset ids of ``layer`` claims the check stored under another basis."""
    bad: list[str] = []
    for index, claim in enumerate(claims):
        if claim.basis != "layer":
            continue
        item = checked[index] if index < len(checked) else None
        basis = item.get("basis") if isinstance(item, Mapping) else None
        if basis != "layer":
            bad.append(str(claim.asset_id))
    return bad


def _completed_answer_calls(runtime: Any) -> int:
    """This turn's answer calls that already have a reply. Earlier turns' calls do not count."""
    messages = list((getattr(runtime, "state", None) or {}).get("messages") or [])
    # The answer reminder is a human message inside the turn, so it does not start one.
    humans = [
        i
        for i, m in enumerate(messages)
        if str(getattr(m, "type", "") or "") == "human"
        and not str(getattr(m, "id", "") or "").startswith("answer-reminder-")
    ]
    messages = messages[humans[-1] + 1 :] if humans else messages
    replied = {
        str(getattr(message, "tool_call_id", "") or "")
        for message in messages
        if str(getattr(message, "type", "") or "") == "tool"
    }
    count = 0
    for message in messages:
        for call in getattr(message, "tool_calls", None) or ():
            if not isinstance(call, Mapping):
                continue
            if call.get("name") == "answer" and str(call.get("id") or "") in replied:
                count += 1
    return count
