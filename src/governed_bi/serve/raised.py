"""Reader-raised notes on a finished turn (ADR 0014).

One accumulating channel, two kinds: a refused card files ``from_refusal``, any
delivered card files ``wrong_answer``. A finished refusal is not an interrupt —
faking one would break resume. The pending queue unions open rows here with
live ``ask_user`` interrupts.

``raise_note`` is on the compiled graph so ``aupdate_state(as_node="raise_note")``
can append the channel. It has no edge from ``START`` and never runs during a
turn: ``aupdate_state(as_node=None)`` only writes ``messages``.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from governed_bi.serve.state import ServeState

__all__ = [
    "RAISED_FROM_REFUSAL",
    "RAISED_WRONG_ANSWER",
    "RAISED_KINDS",
    "RAISED_NOTE_MAX_CHARS",
    "PENDING_SOURCE_INTERRUPT",
    "mint_report_id",
    "raised_row",
    "raise_note",
]

RAISED_FROM_REFUSAL = "from_refusal"
RAISED_WRONG_ANSWER = "wrong_answer"
RAISED_KINDS: frozenset[str] = frozenset({RAISED_FROM_REFUSAL, RAISED_WRONG_ANSWER})

#: Cap on a note's length, enforced here rather than only at the HTTP edge.
#:
#: 4,000 characters is roughly 600 words — several times what a person types about one
#: wrong answer, and small enough that the worst case is still boring. The number is a
#: judgement about a *human-written* note, not a protocol limit; raise it if a real note
#: ever hits it, but do not remove it.
#:
#: A cap is needed at all because of where the note lands. ``raised`` is an accumulating
#: channel (``operator.add``): the row is written into the thread's checkpoint and then
#: re-serialised into every later checkpoint of that thread, so an oversized note is paid
#: for on every subsequent turn rather than once. And nothing reclaims it — the inmem
#: runtime's ``Threads.sweep_ttl`` is ``return (0, 0)``, so ``langgraph.json``'s 90-day
#: TTL is inert and the store grows monotonically (see ``pyproject.toml``, ADR 0014 §4).
#: Combined with an unauthenticated ``POST /turns/{id}/raised`` (audit A7 is open), an
#: unbounded ``note`` is a way for anyone reaching the port to grow the store without
#: limit. The cap belongs on this function so that path is closed for an in-process
#: caller too, not just for the route.
RAISED_NOTE_MAX_CHARS = 4000

PENDING_SOURCE_INTERRUPT = "interrupt"


def mint_report_id(turn_id: str) -> str:
    """``rpt-{turn_id}-{12hex}``, the pending/trace join key for a raised row."""
    return f"rpt-{turn_id}-{secrets.token_hex(6)}"


def raised_row(
    *,
    kind: str,
    turn_id: str,
    thread_id: str,
    note: str = "",
    report_id: str | None = None,
    reported_at: str | None = None,
) -> dict[str, Any]:
    """One open ``ServeState.raised`` row. ``open: true`` until a later closer exists.

    ``note`` is stripped — a whitespace-only note is the same as no note, and the caller
    should not be able to spend the cap on padding — and then bounded by
    :data:`RAISED_NOTE_MAX_CHARS`. Both checks raise ``ValueError`` here so there is no
    unbounded path into the checkpoint even for an in-process caller; the HTTP route
    turns the same conditions into 422 before it reads a turn.
    """
    if kind not in RAISED_KINDS:
        raise ValueError(
            f"raised kind must be one of {sorted(RAISED_KINDS)}, not {kind!r}"
        )
    note = str(note or "").strip()
    if len(note) > RAISED_NOTE_MAX_CHARS:
        raise ValueError(
            f"raised note must be at most {RAISED_NOTE_MAX_CHARS} characters, not {len(note)}"
        )
    return {
        "kind": kind,
        "report_id": report_id or mint_report_id(turn_id),
        "turn_id": turn_id,
        "thread_id": thread_id,
        "reported_at": reported_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
        "open": True,
    }


def raise_note(state: ServeState) -> dict[str, Any]:
    """Named writer for ``raised``. Unattached: ``aupdate_state`` supplies the row.

    Returning ``{"raised": []}`` is a no-op under ``operator.add``. The node exists
    so a server-side append has an ``as_node`` target; it is not on the serve DAG.
    """
    return {"raised": []}
