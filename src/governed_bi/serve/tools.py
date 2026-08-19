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
    ExecutorPath,
    statement_sha256,
)
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.prompts import prompt_text
from governed_bi.serve import fetch
from governed_bi.serve.agent_state import CAP_LEDGER_KEY, AttemptBook
from governed_bi.serve.delivery import payload_digest, tool_bounds_from_state
from governed_bi.serve.events import emit, tool_event_id
from governed_bi.serve.ledger import (
    attempt_field,
    cap_attempt,
    execution_from_attempts,
    pipeline_error_attempt,
)
from governed_bi.serve.resume import ResumeRejected, authorise_resume
from governed_bi.serve.runtime import configurable, prompt_variants

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
    """Agent standing instructions, at the variant this run selected.

    **A function, not the module constant it was.** ``SYSTEM_PROMPT = prompt_text("analyst")``
    bound at import, so a run selecting a non-default variant sent the default and recorded the
    override's ``prompt_set_hash`` — an artifact naming a treatment it did not receive, which is
    strictly worse than not having the knob. The registry has carried ``variants`` since it was
    written; nothing in ``src/`` ever passed one, so nothing exercised the gap.
    """
    return prompt_text("analyst", prompt_variants(config))

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


def policy_from_config(config: Mapping[str, Any] | None) -> GovernancePolicy:
    """The turn's :class:`GovernancePolicy`, defaulted when configurable carries none.

    One reader, because the attempt cap has two enforcers (:class:`AttemptBook` here and
    ``agent_core``'s ``ToolCallLimitMiddleware``), and a second copy of "how the policy is
    fetched" is how they come to enforce different numbers while both quoting
    ``run_query_attempt_cap``.
    """
    policy = configurable(config).get("policy")
    return policy if isinstance(policy, GovernancePolicy) else GovernancePolicy()


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
    ledger_path: ExecutorPath | None = None,
) -> Command:
    """Corpus tools with stream events. Status: ok / blocked / error.

    ``work`` returns ``(payload, delivered)``, or ``(payload, delivered, attempt)`` when the
    tool is an **executor path** and owes the ledger a row. ``sample_rows`` is that case.
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
        # Same as the `run_query` site (audit C1): an executor path that dies before its verdict
        # exists still owes the ledger a row, or the turn reads as one that attempted nothing.
        # `read_body` and `inspect_schema` build no statement and own no path, so they get no row
        # -- inventing one would put a governance verdict on a corpus read.
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
    updates: dict[str, Any] = dict(_delivered(runtime, payload)) if delivered else {}
    if attempt is not None:
        # The verdict rides **this** step's detail rather than emitting ``check`` / ``execute``
        # rows: those two are ``run_query``'s attempt numbering, and a sample verdict among
        # them would read as a SQL attempt the model never made.
        detail = {
            **detail,
            "layer": attempt["verdict_layer"],
            "reason_code": attempt["reason_code"],
            "executed": attempt["executed_sql"] is not None,
        }
        if attempt["executed_sql"] is not None:
            detail["sql_sha256"] = statement_sha256(attempt["executed_sql"])
        # The ledger row rides the same Command as the payload. Without it,
        # ``guardrail_errors == 0`` and an empty attempt list held vacuously for this path.
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
    bounds = bounds or tool_bounds_from_state(state, cfg)
    assets = resolve_assets(config)
    policy = policy_from_config(config)
    connector = cfg.get("connector")
    # Passed through as whatever is on ``configurable``: a wrong-typed or absent corpus is a
    # wiring failure and ``fetch.run_query`` raises on it, where coercing it to ``None`` would
    # let a default stand in for it (G1).
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
            ledger_path="sample",
        )

    @tool
    async def run_query(sql: str, runtime: ToolRuntime) -> Command:
        """Governed SQL execution against licensed tables only."""
        call_id = _call_id(runtime)
        committed = (getattr(runtime, "state", None) or {}).get("attempts_by_call")
        if not book.admit(committed, call_id):
            # The backstop, not the brake: `agent_core`'s middleware counts the same proposal a
            # node earlier, so this branch belongs to callers that build tools without an agent.
            # The row is written either way (`execution_from_attempts` reads the ledger, not the
            # enforcer), and `CAP_LEDGER_KEY` makes a second write a no-op.
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
            # **A row, not just a string** (audit C1). Everything reaching here failed before a
            # verdict existed -- `fetch.run_query` already returns the attempt row when the
            # *execution* fails, for the reason stated at that site -- so this is our checker
            # breaking. Returning only the string left the ledger empty, and `stamp` reads an
            # empty ledger as "answered from the delivered context": outcome `answered`,
            # `guardrail_errors: 0`, every gate green, `generated_sql` holding a statement that
            # never reached `prepare()`. The refund stays: the attempt cost the model nothing it
            # should be charged for, and the row is what makes the failure countable.
            return _reply(
                runtime,
                f"run_query error: {type(exc).__name__}: {exc}",
                attempts_by_call={
                    call_id: pipeline_error_attempt("agent", f"{type(exc).__name__}: {exc}")
                },
            )
        _emit_attempt(runtime, attempt, number=attempt_number, payload=payload)
        table = _result_table(payload)
        return _reply(
            runtime,
            payload,
            attempts_by_call={call_id: attempt},
            **({"result_table": table} if table is not None else {}),
        )

    #: One pending clarification per turn. Closure-level and mutable, the same shape as
    #: ``book`` above, because both ``Send``s from one assistant message share this closure.
    #:
    #: **Two ``ask_user`` calls in one assistant message cross-wire the answer.** LangGraph
    #: dispatches one ``Send`` per pending tool call and both interrupt; the surfacing order is
    #: a race, ``_clarification`` returns the first interrupt, and ``Command(resume=...)`` always
    #: lands on the first tool call. Measured: the user is shown "which region?", answers it, and
    #: the answer is recorded against — and handed to the model as — "which year?". The resume
    #: surface has no way to say which question is being answered, so the fix is upstream: there
    #: is only ever one question outstanding.
    #:
    #: **Released when the question is answered, not held for the rest of the node execution.**
    #: The list is rebuilt by every ``build_tools`` — once per ``agent_core`` execution — and a
    #: resume re-executes that node, so the ``append`` below never *accumulates* across passes
    #: (measured: two distinct lists, one entry each). What it did do was stay occupied. The call
    #: being resumed has no ``ToolMessage`` yet — that is precisely *why* its ``Send`` re-runs — so
    #: it re-takes the latch on the resume pass, and nothing gave the latch back. Measured: after
    #: "which year?" came back answered, the model's next ``ask_user`` was told "this turn already
    #: has one" outstanding, with the answer to that one sitting in the transcript directly above
    #: the refusal. The ``append`` still has to happen *before* ``interrupt()``, because that is
    #: the only point at which it can stop two concurrent ``Send``s; the release is the fix.
    pending_clarification: list[str] = []

    @tool
    async def ask_user(question: str, runtime: ToolRuntime, why: str = "") -> Command:
        """Pause and ask the human a clarifying question (HITL interrupt)."""
        if pending_clarification:
            # Refused, not queued, and no interrupt: the model gets a tool reply it can act on
            # while the first question is still outstanding. Checked and set with no `await`
            # between, so two concurrent `Send`s cannot both pass.
            return _reply(
                runtime,
                "Only one clarifying question may be outstanding at a time, and this turn "
                "already has one. Ask the single question whose answer most changes the SQL; "
                "if more are needed, ask them after this one is answered.",
            )
        digest = hashlib.sha256(f"{turn_id}\x1f{question}".encode()).hexdigest()[:12]
        clarification_id = f"clar-{turn_id}-{digest}"
        pending_clarification.append(clarification_id)
        started = {"clarification_id": clarification_id}
        # **This ``start`` is emitted before ``interrupt()`` and therefore again on every resume,
        # and that is the wire contract rather than a leak** (ADR 0010, "``id`` is keyed on …
        # ``tool_call_id`` … stable across a resume replay … the ``tools`` node re-executes on
        # resume, so ``start`` is emitted twice"). Two requirements meet here and only a
        # pre-``interrupt()`` emit satisfies both: the row must be *open* for the whole time a
        # human is looking at the question, and a resume is a **second stream** — a client that
        # reloaded while the question was pending connects only to that one, so the replayed
        # ``start`` is the only one it ever sees, and it is what gives the resolve below a row to
        # land in. Both events carry the same ``event_id``, so the client folds them into one row
        # (``ui/lib/steps.ts::reduceSteps`` merges on ``id``; a repeated ``start`` re-asserts
        # ``running``, which the row already holds).
        #
        # Measured for one call: ``start`` (pausing pass), ``start`` (resume pass), then exactly
        # one resolve. A resolved row is never followed by a ``start`` — a resume refused below is
        # terminal, and a satisfied call's ``Send`` is filtered out of every later pass by its
        # ``ToolMessage`` — so the repeat cannot regress a settled status. Do not "fix" it by
        # moving this line after ``interrupt()``: that deletes the open row for exactly the
        # interval it exists to describe.
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
        # **The resume identity gate (ADR 0006 B9), on the first instruction that can hold it.**
        # ``interrupt()`` raises on the initial pass, so everything below runs only on a resume —
        # and only here do both halves exist in one frame: ``state`` is the *paused* turn's
        # checkpoint, ``config`` belongs to the run applying the resume. The streamed transport
        # posts ``{"command": {"resume": …}}`` straight into the graph, so nothing in ``api/`` is
        # consulted; ``api/auth.py`` can read that payload but not the thread it targets
        # (``serve/resume.py``'s module docstring measures why), and it cannot refuse
        # ``command.resume`` wholesale without deleting the paused-turn protocol.
        #
        # Before the line, not after: ``_clarification_answer`` is where an unauthorised caller's
        # text would first become something the model is handed and the record keeps.
        try:
            authorise_resume(state, config)
        except ResumeRejected:
            # The ``start`` row above is already on the live stream, and on *this* run's stream
            # too, since the replay re-emitted it; a raise alone never closes it, so the operator
            # would watch a clarification spin forever and find only ``error_type:
            # "ResumeRejected"`` in the record. ``detail`` stays ``started``: the clarification
            # id, never the answer, which is the thing this refusal is withholding.
            emit(
                kind="tool",
                step="ask_user",
                status="refused",
                event_id=tool_event_id("ask_user", _call_id(runtime)),
                detail=started,
            )
            raise
        # **The latch is given back here**, after the gate and before the answer is read: an
        # authorised resume means this question is no longer outstanding, so the next one the
        # model asks in this same ``agent_core`` execution is a genuinely new question and must be
        # allowed to pause. Kept *after* ``authorise_resume`` so that call stays the first
        # instruction ``interrupt()`` returns onto (ADR 0006 B9) — a caller who was not asked
        # neither answers the question nor frees it. ``remove`` cannot raise: this frame appended
        # the entry, the check above admits exactly one, and nothing between the two lines
        # ``await``s, so no other frame can have taken it.
        pending_clarification.remove(clarification_id)
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
