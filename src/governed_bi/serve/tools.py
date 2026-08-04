"""Serve tools (ADR 0005 §3.5, ADR 0006 bounds).

Factory ``build_tools(state, config)`` closes over frozen
:class:`~governed_bi.govern.bounds.ToolBounds`. Out-of-scope and missing
identifiers share :data:`~governed_bi.govern.bounds.OUT_OF_SCOPE_MESSAGE`.
Tool exceptions become error strings — never refuse/decline. The one exception is
:class:`~governed_bi.govern.check.GovernanceUsageError`, which says the *caller* wired
the turn wrongly; it propagates, because a wiring failure recorded as a refusal is
indistinguishable from governance declining a statement.

**Every tool returns a** :class:`~langgraph.types.Command`, not a string. What a tool did —
a governed statement, a delivered payload, an answered clarification — is durable state, and
:mod:`governed_bi.serve.agent_state` records why keeping it in closures lost all three on
every resume. The string the model sees is the ``ToolMessage`` inside the update, so nothing
about the model's view changed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command, interrupt

from governed_bi.corpus.analyst import AnalystCorpus
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.layers import refuse
from governed_bi.govern.ledger import (
    AttemptRecord,
    attempt_record,
    execution_record,
    statement_sha256,
)
from governed_bi.govern.pipeline import prepare, spellings_for
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.prompts import prompt_text
from governed_bi.register.stages import ATTEMPT_CAP_REFUSED_BY
from governed_bi.serve.agent_state import AttemptBook
from governed_bi.serve.delivery import payload_digest, tool_bounds_from_state
from governed_bi.serve.events import emit, tool_event_id
from governed_bi.serve.runtime import configurable

__all__ = [
    "SYSTEM_PROMPT",
    "build_tools",
    "resolve_assets",
    "attempt_field",
    "execution_from_attempts",
    "tool_bounds_from_state",
]

#: The agent's standing instructions.
#:
#: The two naming rules are here because omitting them produced refusals that named the
#: wrong cause (ADR 0008 P8/D10). ``check()`` is given no ``default_schema``, so an
#: unqualified ``FROM customers`` keys as ``customers`` while ``licensed`` holds
#: ``beer_factory.customers`` and the table layer refuses ``r_table_not_licensed`` — *"the
#: model asked for a table it may not see"* — for what is really *"the model omitted a
#: schema nobody told it to write"*.
#:
#: Quoting is asked for even though ``canonicalise`` now fixes the spelling and adds the
#: quotes itself, because it cannot fix a statement that does not parse:
#: ``FROM airline.Air Carriers`` is a syntax error before any of this runs, and that table
#: is real.
#: **The text now lives in ``register/prompts.py``**, and this name is kept as the one thing it
#: still needs to be: the accessor every existing caller already imports. The move is what makes
#: ``prompt_set_hash`` honest — it digested this single constant, and the engine is gaining a
#: guard prompt, five facet rewriters and a narrator, at which point one prompt's digest would
#: have reported two runs with different guard prompts as identical.
#:
#: Read through the registry rather than copied, so there is one text. A second copy here is how
#: the hash and the sent prompt drift, which is the failure the registry exists to end.
SYSTEM_PROMPT = prompt_text("analyst")

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


def _call_id(runtime: Any) -> str:
    """This tool call's id. It keys every durable thing a tool writes."""
    return str(getattr(runtime, "tool_call_id", "") or "")


def _reply(runtime: Any, text: str, **updates: Any) -> Command:
    """The model's ``ToolMessage`` plus whatever the call recorded.

    The ``ToolMessage`` is constructed here rather than by the framework because a tool that
    returns a ``Command`` owns its own reply — LangGraph has no string to wrap once the
    return value is an update.
    """
    update: dict[str, Any] = {
        "messages": [ToolMessage(content=text, tool_call_id=_call_id(runtime))]
    }
    update.update(updates)
    return Command(update=update)


def _fetch(
    stage: str,
    runtime: Any,
    detail: dict[str, Any],
    work: Callable[[], tuple[str, bool]],
) -> Command:
    """The three read-only corpus tools, with their live stream events (ADR 0010).

    Factored because ``read_body``, ``inspect_schema`` and ``sample_rows`` had the identical
    body — call, catch, reply — and adding two emissions to each would have made it identical
    three times over. The tools that are *not* here are the ones whose outcome is not
    ``ok``-or-``error``: ``run_query`` reports a governance verdict and ``ask_user`` suspends.

    The reply text keeps the ``"{tool} error: {type}: {exc}"`` shape the model reads to repair
    itself. The **event** carries only ``error_type``, per ADR 0010 §4: the model needs the
    driver's words, the operator's timeline does not, and libpq puts the offending statement in
    them.

    **Three outcomes, not two, and the third one was a real defect.** The status used to be
    "``error`` if it raised, else ``ok``" — so a *governance refusal* reported as a successful
    read. These tools do not raise when bounds say no; they return
    :data:`~governed_bi.govern.bounds.OUT_OF_SCOPE_MESSAGE` with ``delivered=False``, and the
    timeline said "Inspected beer_factory.audit_log" for a table the turn was refused. A bounds
    refusal writes no ledger attempt and no ``tool_delivered`` entry, so the event is the **only**
    signal it happened anywhere — which made ``ok`` not a coarse label but the sole record of the
    turn, and wrong.

    The refusal is told from a wiring failure by comparing against the shared constant, **not**
    by ``delivered`` alone: ``sample_rows`` also returns ``delivered=False`` for "no connector
    configured", and this repository is explicit that a wiring failure presented as a governance
    refusal is the inverse defect — indistinguishable from governance declining a statement. So
    ``blocked`` means bounds said no and ``error`` means we are broken.
    """
    event_id = tool_event_id(stage, _call_id(runtime))
    emit(kind="tool", step=stage, status="start", event_id=event_id, detail=detail)
    try:
        payload, delivered = work()
    except Exception as exc:  # noqa: BLE001 — tool surface
        emit(
            kind="tool",
            step=stage,
            status="error",
            event_id=event_id,
            detail={**detail, "error_type": type(exc).__name__},
        )
        return _reply(runtime, f"{stage} error: {type(exc).__name__}: {exc}")
    if delivered:
        status = "ok"
    elif payload == OUT_OF_SCOPE_MESSAGE:
        status = "blocked"
    else:
        status = "error"
    emit(kind="tool", step=stage, status=status, event_id=event_id, detail=detail)
    return _reply(runtime, payload, **(_delivered(runtime, payload) if delivered else {}))


def _emit_attempt(runtime: Any, attempt: Mapping[str, Any], *, number: int, payload: str) -> None:
    """The ``check`` verdict and, when a statement reached the database, the ``execute`` row.

    Two events rather than one ``run_query`` row, and that is ``register/stages.py``'s call, not
    a presentation choice: :class:`~governed_bi.register.stages.Stage` deliberately has **no**
    ``run_query`` member — *"a passing query already emits the check + execute pair, and a third
    record would double-count an action the ledger and every rate already agree on"*. Emitting
    a ``run_query`` step would have been a second vocabulary for one action, which is the thing
    that module exists to prevent.

    It also reads better. "Governance blocked: r_table_not_licensed (table)" then "Executed,
    214 rows" is what the product does; "Ran query" is not.
    """
    call_id = _call_id(runtime)
    passed = bool(attempt.get("passed"))
    # ``layer`` is sent even when it is ``None``, which looks like it contradicts this stream's
    # rule that an absent fact is omitted rather than defaulted. It does not: ``AttemptRecord``
    # declares ``verdict_layer: str | None`` and a passing statement genuinely *has* no
    # refusing layer, so ``null`` here is the observation and not a missing one. Omitting it
    # would make "no layer refused this" indistinguishable from "the layer was not reported",
    # which is the distinction the rule exists to preserve.
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
        # Nothing reached the database, so there is no execution to report. A row here would
        # be an `execute` that never executed.
        return

    # **The status is read out of the payload, not out of ``passed``.** A statement can clear
    # every layer, be sent, and fail in the driver — ``_run_query`` returns the attempt anyway,
    # on purpose, because it *was* a governed statement. The success payload is JSON carrying
    # ``row_count``; a driver failure is a plain error string. Parsing for the field is an
    # observation; sniffing the string's prefix would be a guess.
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
    """The query's result as a structured table, or ``None`` if this payload is not one.

    **This is the table the answer was missing.** Measured on a live turn, the agent narrates
    perfectly well on its own — its final message reads *"The largest queryable table is
    `authors.PaperAuthor`, with 2,315,574 rows"* — but the rows themselves lived only inside this
    payload string, so a client could render an explanation and not the data it explains. The
    maintainer's ask was "the table alongside an explanation"; the explanation was the half that
    already existed.

    Read back out of the payload rather than threaded from ``_run_query``'s locals, so the table
    the client renders is byte-identical to the one the model was shown. Two derivations of "what
    the query returned" is how a UI and a model come to disagree about an answer.
    """
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
        # `rows` is already the 20-row preview `_run_query` built, so this flag means "the
        # database truncated", and the preview means the payload did. Both are true at once and
        # a client showing 20 of 2,315,574 needs to say which number it is showing.
        "truncated": bool(decoded.get("truncated")),
        "preview_rows": len(rows),
    }


def _delivered(runtime: Any, payload: str) -> dict[str, Any]:
    """The delivery row for a payload that actually reached the model.

    Keyed by the tool call id. It was a fresh ``uuid4()``, so ``tool_delivered``'s keys named
    nothing and a digest in the record could not be traced to the call that produced it.
    """
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
    read_cap = _read_body_cap(state, cfg)
    book = AttemptBook(policy.run_query_attempt_cap)
    turn_id = str(state.get("turn_id") or "")

    @tool
    def read_body(asset_ids: list[str], runtime: ToolRuntime) -> Command:
        """Return bodies for retrieved asset ids (hits ∪ pulled_in only)."""
        return _fetch(
            "read_body",
            runtime,
            {"n_asset_ids": len(asset_ids or ())},
            lambda: _read_body(asset_ids, bounds=bounds, assets=assets, max_chars=read_cap),
        )

    @tool
    def inspect_schema(table_id: str, runtime: ToolRuntime) -> Command:
        """List columns and physical types for a licensed table."""
        return _fetch(
            "inspect_schema",
            runtime,
            {"table_id": table_id},
            lambda: _inspect_schema(table_id, bounds=bounds, assets=assets),
        )

    @tool
    def sample_rows(column_id: str, runtime: ToolRuntime, limit: int = 5) -> Command:
        """Sample distinct values for a ColumnAsset id whose table is licensed."""
        return _fetch(
            "sample_rows",
            runtime,
            {"column_id": column_id, "limit": limit},
            lambda: _sample_rows(
                column_id, limit=limit, bounds=bounds, assets=assets, connector=connector
            ),
        )

    @tool
    def run_query(sql: str, runtime: ToolRuntime) -> Command:
        """Governed SQL execution against licensed tables only."""
        call_id = _call_id(runtime)
        committed = (getattr(runtime, "state", None) or {}).get("attempts_by_call")
        if not book.admit(committed, call_id):
            update: dict[str, Any] = {}
            if not book.cap_recorded:
                book.cap_recorded = True
                update["attempts_by_call"] = {f"cap:{call_id}": _cap_attempt()}
                # **Inside the guard, with the ledger row, and that is a fix.** Emitted outside
                # it, this fired once per post-cap call — measured: a cap of 1 against three
                # calls produced one ledger row and *two* timeline rows. `AttemptBook` already
                # states the rule the ledger follows — "One row, not one per post-cap call: the
                # cap is a terminal state, and a row per call would inflate the attempt count
                # with calls where nothing was attempted" — and a stream that counts differently
                # from the ledger is the two-projections-disagreeing defect ADR 0010 §4 forbids.
                #
                # No `start`. The cap is terminal by construction — the call is refused before
                # anything is attempted — so a `running` row resolving in the same breath would
                # be a status derived from intent (ADR 0010 §3).
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
            payload, attempt = _run_query(
                sql, bounds=bounds, corpus=corpus, connector=connector, policy=policy
            )
        except GovernanceUsageError:
            # The one exception this surface does not turn into a string. It means the
            # caller wired the turn wrongly, and an error string here would reach the
            # record as a refusal with ``guardrail_errors: 0`` — indistinguishable from
            # governance declining a statement. A wiring failure is a crash, so it
            # propagates to ``wrap.py``, which stamps ``crashed`` + ``agent_core``.
            #
            # The event is emitted **before** the re-raise. Without it the timeline would show
            # `check` running forever on the one failure that is our bug rather than the
            # model's, and `agent_core` would be the only row that resolved.
            emit(
                kind="tool",
                step="check",
                status="error",
                event_id=tool_event_id("check", call_id),
                detail={"attempt": attempt_number, "error_type": "GovernanceUsageError"},
            )
            raise
        except Exception as exc:  # noqa: BLE001
            # No ledger row was produced, so the slot goes back. Charging one for a
            # statement governance never saw would make the cap tighter than the record.
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
        return _reply(
            runtime,
            payload,
            attempts_by_call={call_id: attempt},
            **({"result_table": table} if table is not None else {}),
        )

    @tool
    def ask_user(question: str, runtime: ToolRuntime, why: str = "") -> Command:
        """Pause and ask the human a clarifying question (HITL interrupt).

        ``why`` is what makes the question answerable: *which* ambiguity it resolves. A
        clarification with no stated reason asks the human to guess what the model is unsure
        about, which is the same problem one step out.
        """
        # ADR 0007 §6. The payload was `{"type": "ask_user", "question": ...}` and the client
        # requires `kind: "clarification"` as a literal, plus an id and a reason — so it
        # **dropped the interrupt**, the prompt never mounted, and the turn deadlocked while
        # the interface looked idle: the graph waiting, `isLoading` false, and nothing on
        # screen wrong. That is the worst failure shape available here, and it is why the id
        # and the reason are part of the payload rather than nice-to-have.
        #
        # `clarification_id` is what makes an answer attributable to *this* question rather
        # than to whatever happens to be pending, so it has to be the same string on both
        # sides of a resume. It was `abs(hash(question))`, and `hash` on a str is **salted per
        # interpreter process** (PYTHONHASHSEED) — so the id a client was told was not the id
        # a restarted server would compute for the same question. sha256 is stable by
        # construction, which is the property the comment above it already claimed.
        digest = hashlib.sha256(f"{turn_id}\x1f{question}".encode()).hexdigest()[:12]
        clarification_id = f"clar-{turn_id}-{digest}"
        # **Emitted before the interrupt, and emitted again on the resume.** `interrupt()`
        # raises `GraphInterrupt` the first time through and *returns* on the replay, so this
        # line runs twice for one question — once in the stream that pauses and once in the
        # stream that answers. That is why the row id is keyed on `tool_call_id` (ADR 0010):
        # both `start`s carry the same id, the client merges them into the row that is already
        # on screen, and a `running` clarification does not appear twice.
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
        # The client may answer with a bare string, or with the structured reply its own
        # contract sends: `{clarification_id, answer | choice_id | declined}`. Both are read
        # rather than one assumed, because a resume that arrives in the unexpected shape would
        # otherwise be stringified into the transcript as a Python dict repr.
        text = _clarification_answer(answer)
        # `declined` is read from the reply the client actually sent, not from whether `text` is
        # empty: a human who declines and a human who answers with nothing are different facts,
        # and only one of them is the person choosing not to say.
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
    """The human's answer, from a bare string or the client's structured reply.

    The client's own union is ``{clarification_id, answer}`` / ``{..., choice_id}`` /
    ``{..., declined: true}``. A decline is **not** an empty answer: it means the human
    refused to disambiguate, and the model needs to know that rather than receive `""` and
    treat it as a blank reply.
    """
    if isinstance(resume, Mapping):
        if resume.get("declined"):
            return "The user declined to answer this clarification."
        for key in ("answer", "choice_id", "text"):
            value = resume.get(key)
            if value:
                return str(value)
        return ""
    return str(resume)


def attempt_field(attempt: Any, name: str) -> Any:
    """One field of a ledger row, whether it arrived as a mapping or an object."""
    if isinstance(attempt, Mapping):
        return attempt.get(name)
    return getattr(attempt, name, None)


def execution_from_attempts(attempts: Sequence[Any]) -> dict[str, Any]:
    """The turn's :class:`ExecutionRecord`, with ``terminal`` read off the **ledger**.

    Not from whether a SQL string exists. ``has_sql`` came from the tool-call
    *arguments*, so producing a string counted as producing an answer: a turn whose
    every attempt was refused recorded ``terminal: "answered"`` beside
    ``passed: false``, which is the crash-counted-as-refusal inversion that retired the
    pre-2026-07-25 numbers, pointing the other way.

    The vocabulary is ``govern.ledger.ExecutionRecord``'s. ``"graded"`` belongs to the
    graded-delivery path and is not written here.
    """
    rows = list(attempts)
    if not rows:
        return execution_record(rows, "no_sql")
    if any(attempt_field(a, "passed") is True for a in rows):
        return execution_record(rows, "answered")
    if any(attempt_field(a, "reason_code") == ATTEMPT_CAP_REFUSED_BY for a in rows):
        return execution_record(rows, "capped")
    return execution_record(rows, "refused")


def _cap_attempt() -> AttemptRecord:
    """The ledger row for a turn the attempt cap ended.

    ``_run_query`` returned on the cap *before* appending, so a capped turn carried an
    **empty** ledger while ``generated_sql`` was still read out of the tool arguments —
    and "no attempt passed" then held vacuously. ``ExecutionRecord`` declared
    ``"capped"`` and nothing ever wrote it.

    Built directly rather than through :func:`~governed_bi.govern.layers.refuse`: the cap
    is not a layer verdict (ADR 0006 §5 keeps ``capped`` distinct from ``refused``), and
    a rule id would attribute it to a governance layer that never ran. The reason code is
    :data:`~governed_bi.register.stages.ATTEMPT_CAP_REFUSED_BY`, which is the declared
    value ``classify_outcome`` reads to return ``Outcome.capped``.
    """
    return AttemptRecord(
        verdict_layer=None,
        passed=False,
        reason_code=ATTEMPT_CAP_REFUSED_BY,
        path="agent",
        # Stated, not omitted. A ``TypedDict`` tolerates a missing key at runtime, so a row
        # built without this one forces every consumer to ``.get()`` defensively -- and a
        # capped attempt sent nothing, which is a value.
        executed_sql=None,
    )


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
    return payload, True


def _inspect_schema(
    table_id: str,
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
) -> tuple[str, bool]:
    tid = str(table_id)
    if not bounds.may_inspect_schema(tid):
        return OUT_OF_SCOPE_MESSAGE, False
    table = assets.get(tid)
    if table is None or not _asset_is_table(table):
        return OUT_OF_SCOPE_MESSAGE, False
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
    return payload, True


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
) -> tuple[str, bool]:
    cid = str(column_id)
    if not bounds.may_sample(cid):
        return OUT_OF_SCOPE_MESSAGE, False
    col = assets.get(cid)
    if col is None:
        return OUT_OF_SCOPE_MESSAGE, False
    at = _asset_attr(col, "asset_type")
    is_col = isinstance(col, ColumnAsset) or str(getattr(at, "value", at) or "") == "column"
    if not is_col:
        return OUT_OF_SCOPE_MESSAGE, False
    if connector is None:
        return "sample_rows error: no connector configured", False
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
    return payload, True


def _run_query(
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
