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
from typing import Any, Literal

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
from governed_bi.serve.runtime import bool_knob, configurable, prompt_variants
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


def _log_live_clarification(
    corpus_root: Any,
    *,
    clarification_id: str,
    question: str,
    choices: list[dict[str, str]] | None,
    allow_freeform: bool,
    basis: str,
) -> None:
    """Durably log an unanswered ``ask_user`` question, before ``interrupt`` pauses the turn
    (UtkuAI, ported from v1's ``analyst/tools.py::_log_live_clarification``).

    So the question survives even if the live turn is abandoned and nobody ever resumes it --
    it still shows up in the admin's ``/clarifications`` ledger as homework, regardless of what
    happens to the live turn. No-op when this session has no ``corpus_root`` (eval/offline
    callers), the same precondition ``api/routes.py``'s own ``/clarifications`` write route
    already gates on.

    **Idempotent on ``clarification_id``.** ``interrupt()`` re-runs this function from the top
    on every resume within one turn (LangGraph replays the task that was suspended on the
    pending interrupt), so this runs again on every resume of the same question -- a record
    already logged for this id is left alone rather than duplicated.

    ``basis`` is ``ask_user``'s own ``basis`` argument, carried onto the ledger row (gap fix,
    this initiative) so an offline answer through ``POST /clarifications/{id}/answer`` can
    gate identically to a live one (``curator/clarification.py::fold_ledger_answer_into_corpus``)
    -- the original Phase 1b cut of this function left the record's ``basis`` field unset.
    """
    if corpus_root is None:
        return
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        load_clarifications,
        write_clarifications,
    )

    records = load_clarifications(corpus_root)
    if any(r.id == clarification_id for r in records):
        return
    records.append(
        ClarificationRecord(
            id=clarification_id,
            scope=f"live_chat:{clarification_id}",
            question=question,
            source="live_chat",
            choices=tuple(choices) if choices else None,
            allow_freeform=allow_freeform,
            basis=basis,
        )
    )
    write_clarifications(corpus_root, records)


def _close_live_clarification(
    corpus_root: Any,
    *,
    clarification_id: str,
    answer: str | None,
    choice_id: str | None,
    deferred: bool,
) -> None:
    """Record what became of the question :func:`_log_live_clarification` opened.

    That function logged the row ``open`` before ``interrupt``; nothing closed it, so the
    admin's queue reported "never seen", "answered in chat" and "deferred to you" as one state
    for the corpus's whole life. Runs after ``interrupt`` returns, which is the first moment the
    outcome exists.

    Same no-op-without-a-``corpus_root`` precondition as its opening half, for the same
    eval/offline callers. Swallows nothing else: the ledger function it calls returns ``None``
    on a missing row rather than raising, because a bookkeeping miss must not kill a turn that
    has already produced its answer.
    """
    if corpus_root is None:
        return
    from governed_bi.curator.clarifications import close_live_clarification

    close_live_clarification(
        corpus_root,
        clarification_id,
        answer=answer,
        choice_id=choice_id,
        deferred=deferred,
    )


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
    # UtkuAI, ported (Phase 1b): the offline Clarifications ledger's own precondition --
    # ``session.corpus_root is not None`` -- matching every write route in ``api/routes.py``.
    corpus_root = cfg.get("corpus_root")
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
    #: It resets on every ``build_tools``, which is once per node execution. That is correct: on
    #: resume the satisfied call's ``Send`` is filtered out by its existing ``ToolMessage``, so a
    #: second question after a resume is a genuinely new one.
    pending_clarification: list[str] = []

    @tool
    async def ask_user(
        question: str,
        runtime: ToolRuntime,
        basis: Literal["data_definition", "ranking_ambiguity"],
        why: str = "",
        choices: list[dict[str, str]] | None = None,
        allow_freeform: bool = True,
    ) -> Command:
        """Pause and ask the human a clarifying question (HITL interrupt).

        ``question``/``why`` reach a business user, never an engineer — write them in
        plain language, with no table/column names, no dotted `table.column` paths, and
        no snake_case or camelCase identifiers. A leaked identifier is rejected before
        this pauses the turn; rephrase and call ``ask_user`` again. Write both in the same
        language the user's own question was asked in, never the corpus's or schema's
        language.

        ``basis`` states which of two reasons made you call this tool, so the answer can
        be routed correctly afterwards:

        - ``"data_definition"`` — a missing fact about the schema or a business rule that
          has one right answer for everyone (e.g. how a column encodes a value, what
          counts toward a metric). The answer is a durable fact worth remembering for
          every future question, not just this one.
        - ``"ranking_ambiguity"`` — the question turns on a ranking or superlative
          ("best", "top", "most valuable") that more than one metric could reasonably
          mean. The answer is a choice for this turn only; a different user, or the same
          user on another day, may mean something else by the same word.

        ``choices`` offers 2-4 concrete, clickable candidate answers, each shaped
        ``{"id": ..., "label": ...}``. Pass it only when you can name candidates you have
        actually **grounded** — in columns or values you inspected via ``inspect_schema``
        or ``sample_rows``, or in the schema's own structure — never invented ones. Leave
        it ``None`` when nothing is genuinely grounded; free text alone is correct then.
        A malformed entry (missing or empty ``id``/``label``) is rejected the same way a
        schema-identifier leak is, before this pauses the turn.

        ``allow_freeform`` defaults to ``True`` and should almost always stay ``True`` even
        when ``choices`` is given, so a real answer outside the offered list still reaches
        you rather than being boxed out.

        Only one question may be outstanding per turn; a second call is refused with a reply
        rather than queued.
        """
        if pending_clarification:
            # Refused, not queued, and no interrupt: the model gets a tool reply it can act on
            # while the first question is still outstanding. Checked and set with no `await`
            # between, so two concurrent `Send`s cannot both pass.
            #
            # Ahead of this fork's own validation below, because "a question is already
            # outstanding" is true of the turn regardless of how well-formed this second
            # question is -- validating first would tell the model to fix wording on a call
            # that was never going to pause the turn.
            return _reply(
                runtime,
                "Only one clarifying question may be outstanding at a time, and this turn "
                "already has one. Ask the single question whose answer most changes the SQL; "
                "if more are needed, ask them after this one is answered.",
            )
        leak = find_schema_leak(question, why)
        if leak is not None:
            return _reply(
                runtime,
                f"ask_user rejected: {leak!r} looks like a raw schema identifier, not "
                "plain business language. Rephrase question/why without table.column "
                "paths, snake_case, or camelCase identifiers, then call ask_user again.",
            )
        if choices:
            bad_choice = next(
                (
                    c
                    for c in choices
                    if not isinstance(c, Mapping)
                    or not str(c.get("id") or "").strip()
                    or not str(c.get("label") or "").strip()
                ),
                None,
            )
            if bad_choice is not None:
                return _reply(
                    runtime,
                    f"ask_user rejected: choices entry {bad_choice!r} needs a non-empty "
                    "'id' and 'label'. Fix it -- or drop choices entirely for a "
                    "free-text-only question -- then call ask_user again.",
                )
        digest = hashlib.sha256(f"{turn_id}\x1f{question}".encode()).hexdigest()[:12]
        clarification_id = f"clar-{turn_id}-{digest}"
        pending_clarification.append(clarification_id)
        started = {"clarification_id": clarification_id}
        emit(
            kind="tool",
            step="ask_user",
            status="start",
            event_id=tool_event_id("ask_user", _call_id(runtime)),
            detail=started,
        )
        interrupt_payload: dict[str, Any] = {
            "kind": "clarification",
            "clarification_id": clarification_id,
            "question": question,
            "why": why or "The question is ambiguous and the answer depends on which reading is meant.",
            # Phase 6 of this initiative (governed-bi-analysis): withheld from this payload
            # deliberately in Phase 1 as "a backend-only routing signal" -- that reasoning held
            # while nothing downstream of the UI needed it. It no longer holds: the product
            # owner wants ranking_ambiguity questions (per-user judgment calls) to never offer a
            # "defer to admin" button, which data_definition questions (objective schema/rule
            # facts) may. Enforcing that is a UI button-visibility decision, and the UI cannot
            # make it without knowing basis.
            "basis": basis,
            "allow_freeform": allow_freeform,
        }
        if choices:
            interrupt_payload["choices"] = choices
        # UtkuAI, ported (Phase 1b): survives an abandoned turn nobody ever resumes -- the
        # question becomes admin homework regardless. Must run before ``interrupt`` and is
        # itself idempotent, because ``interrupt`` re-runs this function from the top on every
        # resume (see the helper's own docstring).
        #
        # ``asyncio.to_thread``, not a direct call: this is disk I/O inside an *async* tool
        # body, on the event loop -- the same class of pitfall ``serve/events.py``'s own
        # "function-level import trips blockbuster's os.getcwd" comment already flags -- and
        # matches every other disk/network-touching call in this module (``_fetch``,
        # ``run_query``), none of which call their blocking work directly either.
        await asyncio.to_thread(
            _log_live_clarification,
            corpus_root,
            clarification_id=clarification_id,
            question=question,
            choices=choices,
            allow_freeform=allow_freeform,
            basis=basis,
        )
        answer = interrupt(interrupt_payload)
        text = _clarification_answer(answer)
        resume_reply = answer if isinstance(answer, Mapping) else {}
        # Phase 1b (this initiative): `declined` and `deferred` used to collapse onto one
        # `declined` flag (`defer` was an alias -- see `_clarification_answer`'s prior
        # docstring). They no longer do: a decline still fails the turn closed exactly as
        # before (unchanged), but a defer now lets the turn continue on the agent's own best
        # judgment with a downgraded-reliability caveat (`serve/nodes/stamp.py::_reliability`),
        # which needs its own, distinct signal on the ledger row rather than reusing `declined`.
        declined = bool(resume_reply.get("declined"))
        deferred = bool(resume_reply.get("defer"))
        # The closing half of the log above. Skipped on a decline, deliberately: a declined
        # question is still unanswered homework, so `open` states it correctly and there is no
        # `declined` status to write (see `ClarificationRecordStatus.deferred`'s own note).
        # `answer` is the user's own text and nothing else -- a bare-string resume is that
        # string, a `{"choice_id": ...}` reply carries no text at all and stores the id instead
        # (matching how the offline route stores a choice answer), and a defer's `text` is an
        # instruction addressed to the model, which must never appear in the column an admin
        # reads as "what the user said".
        if not declined:
            await asyncio.to_thread(
                _close_live_clarification,
                corpus_root,
                clarification_id=clarification_id,
                answer=(
                    str(resume_reply["answer"])
                    if resume_reply.get("answer")
                    else (text or None if not isinstance(answer, Mapping) else None)
                ),
                choice_id=(
                    str(resume_reply["choice_id"]) if resume_reply.get("choice_id") else None
                ),
                deferred=deferred,
            )
        emit(
            kind="tool",
            step="ask_user",
            status="declined" if declined else "deferred" if deferred else "ok",
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
                    "basis": basis,
                    # UtkuAI, ported: whether the human declined rather than answered.
                    # `answer` above is `text` -- always a sentence, even on decline
                    # ("The user declined to answer this clarification.") -- so a reader of
                    # `ServeState.clarifications` (e.g. `serve/nodes/mine_corpus.py`) cannot
                    # tell a decline from a real answer by inspecting `answer` alone. This is
                    # the same `declined` value already computed above for the emitted
                    # status; plumbing it through is cheaper than re-deriving it from prose.
                    "declined": declined,
                    # Phase 1b: distinct from `declined` -- a defer, unlike a decline, does not
                    # skip mining (`mine_corpus.py`'s own gate checks both flags) and does feed
                    # `stamp.py`'s per-turn reliability downgrade.
                    "deferred": deferred,
                }
            },
        )

    @tool
    async def state_assumption(text: str, runtime: ToolRuntime) -> Command:
        """Record one plain-language assumption you made answering this question.

        Call this — never as a substitute for ``ask_user`` when a question is genuinely
        ambiguous — whenever you resolved an ambiguity, applied a filter, or picked one
        of several plausible readings **without** asking, so the person reading the
        answer sees what you decided rather than an unexplained number. Examples:
        "Excluded cancelled orders from the total." / "Assumed 'active' means a purchase
        in the last 30 days." One call per assumption; call it as many times as you have
        assumptions to state. Never a substitute for asking when you are genuinely
        unsure — state an assumption only when you judged the question answerable without
        asking.

        Same plain-language requirement as ``ask_user``: no table/column names, no
        dotted paths, no snake_case or camelCase identifiers. A leaked identifier is
        rejected; rephrase and call again.
        """
        leak = find_schema_leak(text)
        if leak is not None:
            return _reply(
                runtime,
                f"state_assumption rejected: {leak!r} looks like a raw schema identifier, "
                "not plain business language. Rephrase without table.column paths, "
                "snake_case, or camelCase identifiers, then call state_assumption again.",
            )
        return _reply(
            runtime,
            "noted",
            assumptions_by_call={_call_id(runtime): text},
        )

    return [read_body, inspect_schema, sample_rows, run_query, ask_user, state_assumption]


#: Unchanged from before Phase 1b -- decline keeps failing the turn closed on this exact
#: sentence (no code forces a refusal on it; the model reads it and stops on its own, and that
#: emergent behavior is the product decision left untouched here).
_CLARIFY_DECLINED_TEXT = "The user declined to answer this clarification."

#: UtkuAI, ported from v1's ``CLARIFY_DEFERRED`` sentinel (``analyst/tools.py``): unlike the
#: decline sentence above, this is the actual instruction the model is meant to act on, not a
#: defensive fallback -- it is what makes the turn continue on the agent's own judgment instead
#: of stopping (Phase 1b, this initiative).
_CLARIFY_DEFERRED_TEXT = (
    "The user could not answer this now and deferred it to admin review. Proceed using your "
    "own best judgment for this specific point, and explicitly say in your final answer that "
    "this assumption is unconfirmed and pending admin review."
)


def _clarification_answer(resume: Any) -> str:
    """Human answer from a bare string or structured ``{answer|choice_id|declined|defer}`` reply.

    ``declined`` and ``defer`` no longer collapse onto one sentinel (Phase 1b, this
    initiative): a decline still stops the turn on the same sentence as before, unchanged --
    but a defer now hands the model an instruction to keep going on its own best judgment,
    flagged unconfirmed, rather than a second spelling of "stop." See ``ask_user``'s own
    ``deferred`` flag for the ledger-facing half of this distinction.
    """
    if isinstance(resume, Mapping):
        if resume.get("declined"):
            return _CLARIFY_DECLINED_TEXT
        if resume.get("defer"):
            return _CLARIFY_DEFERRED_TEXT
        for key in ("answer", "choice_id", "text"):
            value = resume.get(key)
            if value:
                return str(value)
        return ""
    return str(resume)
