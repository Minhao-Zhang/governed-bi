"""``reflect`` — a post-hoc observer: did the SQL this turn produced answer the question?

**An instrument, not a feature.** It writes a verdict and **changes no control flow at all**:
never ``path_kind``, never ``terminal_reason``, never ``answer``. A retry loop built on a judge
that cannot beat the base rate re-rolls a draw after seeing it, which is what ``n_re_served``'s
gate exists to catch; whether this earns a loop is decided offline by
``tools/score_reflector.py``.

**Off by default.** ``reflect_enabled`` ships ``False`` and no production path wires a
``reflect_model``, so the node returns ``{}`` before reading anything but the knob. Registered
in ``graph.py`` with ``stream=False``, so a disabled observer adds no timeline rows; it emits
its own single row when it judged something.

**It never sees gold**, structurally: :func:`reflect_signals` and :func:`reflect_brief` read a
fixed list of keys and ``ServeState`` declares no gold channel — the benchmark's gold SQL and
fingerprints live in ``eval/`` and never enter the graph
(``tests/serve/test_reflect_observer.py`` asserts both halves).

**A failure here is not a failed turn.** Every exception is caught and recorded as *unmeasured
with a reason*, and the reason is the exception's **class name** — never ``str(exc)``, because
driver and provider error text echoes the statement and its literals (ADR 0006 §11).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp

from governed_bi.register.prompts import prompt_text
from governed_bi.serve.events import emit, rail_event_id
from governed_bi.serve.ledger import (
    answering_attempts,
    attempt_field,
    ledger_ended_without_answer,
)
from governed_bi.serve.runtime import bool_knob, configurable, model_id
from governed_bi.serve.state import TERMINAL_PATH_KINDS

__all__ = [
    "REFLECT_PROMPT",
    "REFLECT_VERDICTS",
    "reflect_node",
    "reflect_signals",
    "reflect_brief",
    "reflect_on",
]

#: The closed vocabulary a verdict may take. ``unsure`` is a first-class value, not a failure:
#: a judge forced to choose between two labels it cannot distinguish returns a coin flip.
REFLECT_VERDICTS: frozenset[str] = frozenset({"answered", "wrong", "unsure"})

#: How much of the result set the judge is shown. It decides whether the shape and values are
#: plausibly an answer, not whether the dataset is right.
_REFLECT_ROWS_SHOWN = 10

#: How much of one cell it is shown. A wide text column would otherwise be most of the prompt.
_CELL_CHARS = 200

#: The judge's system prompt, resolved from the registry at import like every other prompt this
#: engine sends. Registered knowingly at the cost of moving ``prompt_set_hash`` for edits that
#: move no answer: this judge exists to be scored, so two scores computed under two wordings
#: must not be able to read as one series.
REFLECT_PROMPT = prompt_text("reflect")


def _prompt_digest() -> str:
    """Digest of the judge's own prompt, carried on every verdict.

    Not replaced by ``prompt_set_hash``, which is one value for a whole run: this says which
    judge produced **this verdict**, which is what a scorer joining rows across runs needs —
    including ``tools/score_reflector.py``, which calls :func:`reflect_on` outside the graph and
    has no run-level hash to quote.
    """
    return hashlib.sha256(REFLECT_PROMPT.encode("utf-8")).hexdigest()[:16]


def _result_shape(result: Any) -> dict[str, Any]:
    """``{row_count, n_columns, columns}`` of the turn's result table, or an empty mapping."""
    if not isinstance(result, Mapping):
        return {}
    columns = list(result.get("columns") or ())
    rows = list(result.get("rows") or ())
    return {
        "row_count": result.get("row_count") if result.get("row_count") is not None else len(rows),
        "n_columns": len(columns),
        "columns": [str(c) for c in columns],
        "truncated": bool(result.get("truncated")),
    }


def _unreferenced_licensed(licensed: Sequence[Any], sql: str) -> list[str]:
    """Licensed tables whose name does not occur in the statement.

    A substring test, not a parse: it over-reports a name that is a substring of another
    (``order`` inside ``orders``) and under-reports one reached through an alias or a view. That
    is tolerable because this is a hint to a model, not a rule — do not promote it to one
    without parsing.
    """
    lowered = sql.lower()
    out: list[str] = []
    for table_id in licensed:
        name = str(table_id).rsplit(".", 1)[-1].strip('"').lower()
        if name and name not in lowered:
            out.append(str(table_id))
    return out


def _attempt_summary(execution: Any) -> dict[str, Any]:
    """``{n_attempts, n_passed, terminal}`` from the turn's answering ledger rows.

    Filtered through :func:`~governed_bi.serve.ledger.answering_attempts` like every other
    reader of this ledger: a ``sample`` row's ``SELECT DISTINCT`` is not an attempt to answer.
    """
    if not isinstance(execution, Mapping):
        return {}
    attempts = answering_attempts(list(execution.get("attempts") or ()))
    return {
        "n_attempts": len(attempts),
        "n_passed": sum(1 for a in attempts if attempt_field(a, "passed") is True),
        "terminal": execution.get("terminal"),
    }


def reflect_signals(state: Mapping[str, Any]) -> dict[str, Any]:
    """What the engine already knows that bears on whether this answer is right.

    Read off the turn state by name, from this list and no other — the mechanism that makes gold
    unreachable rather than merely absent. Keys with nothing to say are omitted, so the presence
    of a key is itself the signal.
    """
    signals: dict[str, Any] = {}

    result = _result_shape(state.get("result_table"))
    if result:
        signals["result_shape"] = result

    delivery = state.get("delivery")
    if isinstance(delivery, Mapping) and delivery.get("evicted"):
        # A licensed table was dropped from the rendered context for space, so the model wrote
        # its statement without seeing something the turn had licensed.
        signals["evicted"] = dict(delivery["evicted"])

    retrieved = state.get("retrieved")
    if isinstance(retrieved, Mapping) and retrieved.get("lexical_coverage") is not None:
        signals["lexical_coverage"] = retrieved.get("lexical_coverage")

    licensed = state.get("licensed") or ()
    sql = str(state.get("generated_sql") or "")
    if licensed and sql:
        unreferenced = _unreferenced_licensed(list(licensed), sql)
        signals["n_licensed"] = len(list(licensed))
        if unreferenced:
            signals["licensed_but_unreferenced"] = unreferenced

    attempts = _attempt_summary(state.get("execution"))
    if attempts:
        signals["attempts"] = attempts

    return signals


def reflect_brief(state: Mapping[str, Any], signals: Mapping[str, Any]) -> str:
    """The judge's entire input: question, statement, a truncated result, and the signals.

    Deliberately not the delivered context, the retrieved assets or the transcript: a judge
    handed the material the agent reasoned over re-derives the agent's own reasoning.
    """
    result = state.get("result_table")
    rows: list[Any] = []
    if isinstance(result, Mapping):
        rows = [
            [_cell(v) for v in (row or ())]
            for row in list(result.get("rows") or ())[:_REFLECT_ROWS_SHOWN]
        ]
    payload: dict[str, Any] = {"rows": rows}
    total = signals.get("result_shape", {}).get("row_count")
    if isinstance(total, int) and total > len(rows):
        payload["note"] = f"showing the first {len(rows)} of {total} returned rows"
    return (
        f"Question: {state.get('question') or ''}\n\n"
        f"Statement:\n{state.get('generated_sql') or '(none)'}\n\n"
        f"Result:\n{json.dumps(payload, default=str)}\n\n"
        f"Engine signals:\n{json.dumps(dict(signals), default=str)}"
    )


def _cell(value: Any) -> Any:
    """One result cell, bounded. A single wide text column is otherwise most of the prompt."""
    if isinstance(value, str) and len(value) > _CELL_CHARS:
        return value[:_CELL_CHARS] + "..."
    return value


def _read_verdict(text: str) -> tuple[str | None, str | None]:
    """``(verdict, reason)`` from the reply, or ``(None, None)`` if it named no declared verdict.

    Lenient about layout and strict about vocabulary: mapping an invented label onto the nearest
    declared one would be the instrument inventing its own readings.
    """
    verdict: str | None = None
    reason: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if verdict is None and lowered.startswith("verdict:"):
            word = lowered.split(":", 1)[1].strip().strip(".`*").split()
            if word and word[0] in REFLECT_VERDICTS:
                verdict = word[0]
        elif reason is None and lowered.startswith("reason:"):
            reason = stripped.split(":", 1)[1].strip() or None
    return verdict, reason


def _unmeasured(why: str, signals: Mapping[str, Any]) -> dict[str, Any]:
    """A verdict row for a judge that ran and could not decide. ``verdict`` is null, not 'fine'."""
    return {
        "verdict": None,
        "reason": None,
        "why_unmeasured": why,
        "prompt_sha256": _prompt_digest(),
        "signals": dict(signals),
    }


async def reflect_on(
    model: Any, state: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Judge one turn. Returns ``(verdict, usage row or None)``. Never raises.

    The single entry point, shared by :func:`reflect_node` and ``tools/score_reflector.py``: an
    offline score is only evidence about the live reflector if it *is* the live reflector.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.serve.usage import usage_row

    signals: dict[str, Any] = {}
    try:
        # Inside the try with the call: building the signals reads six state keys, and a
        # malformed one would raise out of an observer and crash a turn that had answered.
        signals = reflect_signals(state)
        reply = await model.ainvoke(
            [SystemMessage(REFLECT_PROMPT), HumanMessage(reflect_brief(state, signals))],
            config={"run_name": "reflect"},
        )
        text = str(getattr(reply, "text", "") or "")
    except Exception as exc:  # noqa: BLE001 — see the module docstring: the class, never the text
        return {**_unmeasured(type(exc).__name__, signals), "model": model_id(model)}, None

    verdict, reason = _read_verdict(text)
    spent = usage_row(
        stage="reflect", model=model, messages=reply, turn_index=state.get("turn_index", 1)
    )
    if verdict is None:
        row = _unmeasured("reply named no declared verdict", signals)
    else:
        row = {
            "verdict": verdict,
            "reason": reason,
            "why_unmeasured": None,
            "prompt_sha256": _prompt_digest(),
            "signals": dict(signals),
        }
    return {**row, "model": model_id(model)}, spent


async def reflect_node(state: dict, config: RunnableConfig) -> dict:
    """Write ``reflect_verdict``. Reads the turn; decides nothing about it.

    The first line is the terminal guard every node downstream of a fan-in carries — see
    ``route_node``, where the one missing copy let a crashed turn reach a full billed model call.

    Returns ``{}`` and never ``{"reflect_verdict": None}`` on every path where the judge did not
    run, so the record's null means *reflection did not happen* (``Absence.not_measured``)
    rather than *reflection had nothing to say*.

    **Everything after the guard is inside a catch, including reading the knob.** ``wrap_node``
    turns any exception a node raises into ``path_kind: "crashed"``, so an observer could
    otherwise end a turn that had already answered — a malformed ``reflect_enabled`` in
    ``knobs_resolved`` is enough.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}
    if ledger_ended_without_answer(state):
        return {}
    try:
        return await _reflect(state, config)
    except GraphBubbleUp:
        # A pause is not a verdict. ``GraphInterrupt`` is an ``Exception``, so the clause below
        # would swallow the very thing ``wrap_node`` re-raises to let the checkpointer resume.
        raise
    except Exception as exc:  # noqa: BLE001 — an observer must not be able to fail a turn
        return {"reflect_verdict": _unmeasured(type(exc).__name__, {})}


async def _reflect(state: dict, config: RunnableConfig) -> dict:
    """The body of :func:`reflect_node`, which owns the guard and the catch."""
    if not bool_knob(state, "reflect_enabled"):
        return {}
    # No SQL is not an unfavourable verdict; it is a turn with nothing to judge — the model
    # answered from the delivered context, so "did the statement answer" has no subject.
    if not state.get("generated_sql"):
        return {}

    cfg = configurable(config)
    # ``utility_model`` as the fallback — a short classification over a bounded input, the same
    # tier as the scope gate and the narrator. An explicit ``reflect_model`` overrides it so the
    # judge can be a *different* model from the one that wrote the SQL, which is the arm worth
    # measuring: a model grading its own work is the weakest version of this instrument.
    model = cfg.get("reflect_model") or cfg.get("utility_model")
    if model is None:
        return {}

    verdict, spent = await reflect_on(model, state)
    emit(
        kind="rail",
        step="reflect",
        # ``error`` and not a new status word: the client validates statuses against a closed
        # union and drops what it does not recognise, so an invented one never appears.
        status="ok" if verdict.get("verdict") else "error",
        event_id=rail_event_id("reflect", state),
        detail={"verdict": verdict.get("verdict") or verdict.get("why_unmeasured")},
    )
    update: dict[str, Any] = {"reflect_verdict": verdict}
    if spent is not None:
        # A model call the ledger does not know about is a turn priced below what it spent.
        update["usage"] = [spent]
    return update
