"""``reflect`` — a post-hoc observer: did the SQL this turn produced answer the question?

**An instrument, not a feature, and that distinction is the whole design.** The question it
exists to answer is whether a model can tell a right answer from a wrong one on this task at
better than the base rate. If it cannot, the retry loop that would sit on top of it re-rolls a
draw after seeing it — which is precisely what ``n_re_served``'s refusing gate was written to
catch, arriving through the front door. So this node writes a verdict and **changes no control
flow at all**: it never sets ``path_kind``, never sets ``terminal_reason``, never writes
``answer``. Whether it earns a loop is decided by ``tools/score_reflector.py``, offline, over
rows that already carry a gold verdict.

**Off by default, and off in a way that leaves no trace.** ``reflect_enabled`` ships ``False``
and no production path wires a ``reflect_model``, so the arm every current number was measured
on runs the code it ran before: the node returns ``{}`` before reading anything but the knob.
It is registered in ``graph.py`` with ``stream=False`` for the same reason — ``wrap_node``
emits a rail row on entry and exit for every node it wraps, so going through ``rail()`` would
have added two timeline rows per turn to a disabled observer. The node emits its own single
row, when it actually judged something.

**It never sees gold.** Not by convention: :func:`reflect_signals` and :func:`reflect_brief`
read a fixed list of keys off the turn state, and ``ServeState`` declares no gold channel of
any kind — the benchmark's gold SQL and gold fingerprints live in ``eval/``, on the question
dict, and never enter the graph. ``tests/serve/test_reflect_observer.py`` asserts both halves,
because a reflector that has seen the answer measures nothing.

**Grounded signals, because a bare "does this look right" judge is known-weak.** Every signal
below is a recorded reason a turn's answer may be wrong that the model reading the SQL cannot
otherwise know: a licensed table evicted for space, a question whose words are not in the
corpus vocabulary, a licensed table the statement never touched, and a ledger that says the
attempt cap ended the turn. They are what makes this worth measuring at all.

**A failure here is not a failed turn.** The answer, the SQL and the ledger are already
computed and correct; losing the verdict costs a reader an opinion. Every exception is caught
and recorded as *unmeasured with a reason*, and the reason is the exception's **class name**
— never ``str(exc)``, because driver and provider error text echoes the statement and its
literals (ADR 0006 §11).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig

from governed_bi.register.prompts import prompt_text
from governed_bi.serve.events import emit, rail_event_id
from governed_bi.serve.ledger import answering_attempts, attempt_field
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

#: The closed vocabulary a verdict may take. ``unsure`` is a first-class value and not a
#: failure: a judge forced to choose between two labels it cannot distinguish produces a
#: coin flip wearing a confident label, and the offline score would then measure the coin.
REFLECT_VERDICTS: frozenset[str] = frozenset({"answered", "wrong", "unsure"})

#: How much of the result set the judge is shown. It is deciding whether the shape and the
#: values are plausibly an answer to the question, not auditing the dataset.
_REFLECT_ROWS_SHOWN = 10

#: How much of one cell it is shown. A single wide text column would otherwise be most of
#: the prompt.
_CELL_CHARS = 200

#: The judge's system prompt, resolved from the registry at import like every other prompt
#: this engine sends (``tools.py``'s ``SYSTEM_PROMPT = prompt_text("analyst")``).
#:
#: **Registered, and the trade was made knowingly.** This node writes no control-flow key, so
#: its wording cannot change what any turn produces — which is the argument for keeping it out
#: of a ``Tier.treatment`` digest, and it is a real argument: the entry moves
#: ``prompt_set_hash`` for edits that move no answer. It loses to the other side. A prompt
#: outside the registry is one the run's own hash does not reach, and :func:`prompt_text`'s
#: KeyError names that case exactly — *a treatment the run's own prompt_set_hash does not
#: cover*. For an instrument that is the worse failure: this judge exists to be scored, so two
#: scores computed under two wordings must not be able to read as one series, and the registry
#: is the mechanism that makes that impossible rather than merely unlikely.
REFLECT_PROMPT = prompt_text("reflect")


def _prompt_digest() -> str:
    """Digest of the judge's own prompt, carried on every verdict.

    Kept alongside the registry entry rather than replaced by it, because the two answer
    different questions at different granularities. ``prompt_set_hash`` says which prompt *set*
    a run used and is one value for the whole run; this says which judge produced **this
    verdict**, which is what a scorer joining rows from more than one run needs — including
    ``tools/score_reflector.py``, which calls :func:`reflect_on` outside the graph and so has no
    run-level hash of its own to quote.
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

    **A substring test over the bare table name, and its limits are why it is a signal and not
    a check.** It over-reports a table whose name is a substring of another (``order`` inside
    ``orders``) and under-reports one reached through an alias or a view; it does not parse.
    A parse would be the right instrument for a *rule*, and this is not one — it is a hint to a
    model that the turn had licensed material it did not touch, which is a recorded correlate of
    a wrong answer (the join the question needed was available and the statement skipped it).
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
    reader of this ledger: a ``sample`` row's ``SELECT DISTINCT`` is not an attempt to answer,
    and counting it would tell the judge the turn tried harder than it did.
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

    Read off the turn state by name, from this list and no other — which is the mechanism that
    makes gold unreachable rather than merely absent. Keys with nothing to say are omitted, so
    a clean turn's brief is short and the presence of a key is itself the signal.
    """
    signals: dict[str, Any] = {}

    result = _result_shape(state.get("result_table"))
    if result:
        signals["result_shape"] = result

    delivery = state.get("delivery")
    if isinstance(delivery, Mapping) and delivery.get("evicted"):
        # The budget bit: a licensed table was dropped from the rendered context for space, so
        # the model wrote its statement without seeing something the turn had licensed.
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

    Deliberately not the delivered context, the retrieved assets or the transcript. A judge
    handed the material the agent reasoned over will re-derive the agent's own reasoning, which
    is the one opinion it cannot usefully hold.
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

    Lenient about layout and strict about vocabulary: a reply that invents a label is not a
    verdict this repository can count, and mapping it onto the nearest declared one would be
    the instrument inventing its own readings.
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

    The single entry point, shared by :func:`reflect_node` and ``tools/score_reflector.py`` on
    purpose: an offline score is only evidence about the live reflector if it *is* the live
    reflector, and two copies of "what the judge sees" is two judges, one of which is scored
    and the other deployed.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.serve.usage import usage_row

    signals: dict[str, Any] = {}
    try:
        # Inside the try with the call, because building the signals reads six state keys and a
        # malformed one there would raise out of an observer and crash a turn that had answered.
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

    The first line is the terminal guard every node downstream of a fan-in carries. It is not
    boilerplate: ``route_node``'s docstring records the turn where the one missing copy of it
    let a crashed turn proceed through retrieval, assembly and a **full billed model call**
    before ``stamp`` recorded the crash that had already happened. This node is the newest
    place that could repeat it.

    Returns ``{}`` — never ``{"reflect_verdict": None}`` — on every path where the judge did not
    run, so the channel keeps its reset value and the record's null means *reflection did not
    happen* rather than *reflection had nothing to say*. Those are different facts and the
    register's ``Absence.not_measured`` names the first one.

    **Everything after the guard is inside a catch, including reading the knob.** ``wrap_node``
    turns any exception a node raises into ``path_kind: "crashed"``, so without this an
    observer could end a turn that had already answered — a malformed ``reflect_enabled`` in
    ``knobs_resolved`` would be enough. :func:`reflect_on` catches the model call; this catches
    the rest, and both record the exception's class rather than its text.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}
    try:
        return await _reflect(state, config)
    except Exception as exc:  # noqa: BLE001 — an observer must not be able to fail a turn
        return {"reflect_verdict": _unmeasured(type(exc).__name__, {})}


async def _reflect(state: dict, config: RunnableConfig) -> dict:
    """The body of :func:`reflect_node`, which owns the guard and the catch."""
    if not bool_knob(state, "reflect_enabled"):
        return {}
    # No SQL is not an unfavourable verdict; it is a turn with nothing for this node to judge.
    # The model answered from the delivered context, or the stub did, and "did the statement
    # answer the question" has no subject.
    if not state.get("generated_sql"):
        return {}

    cfg = configurable(config)
    # ``utility_model`` as the fallback, because that is the tier this call belongs to — a
    # short classification over a bounded input, on the same latency and cost class as the
    # scope gate and the narrator. An explicit ``reflect_model`` overrides it so the judge can
    # be a *different* model from the one that wrote the SQL, which is the arm worth measuring:
    # a model grading its own work is the weakest version of this instrument.
    model = cfg.get("reflect_model") or cfg.get("utility_model")
    if model is None:
        return {}

    verdict, spent = await reflect_on(model, state)
    emit(
        kind="rail",
        step="reflect",
        # ``error`` and not a new status word: the client validates statuses against a closed
        # union and drops what it does not recognise, so an invented one is a row that never
        # appears. A judge that could not decide is the only failure this row reports.
        status="ok" if verdict.get("verdict") else "error",
        event_id=rail_event_id("reflect", state),
        detail={"verdict": verdict.get("verdict") or verdict.get("why_unmeasured")},
    )
    update: dict[str, Any] = {"reflect_verdict": verdict}
    if spent is not None:
        # A model call the ledger does not know about is a turn priced below what it spent.
        # This is the only key besides the verdict, and it is not a decision about the turn.
        update["usage"] = [spent]
    return update
