"""Thread state via ``get_client(url=None)``, which LangGraph mounts at ``/noauth``.
Outside the server, raise rather than register a transport nothing drains.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from typing import Any, Literal, NamedTuple, get_args

from governed_bi.registry.stages import Outcome

__all__ = [
    "ThreadReader",
    "PendingPage",
    "ConversationPage",
    "Ending",
    "ENDINGS",
    "ending_of",
    "InProcessServerRequired",
    "PENDING_FIELDS",
    "PENDING_SOURCE_INTERRUPT",
]

#: Threads fetched per ``threads.search`` page; 50 covers the store at this volume in one call.
_THREAD_PAGE = 50

#: Hard bound on threads scanned for one request. :attr:`PendingPage.truncated` reports it.
_MAX_THREADS = 1000

#: Stands for "this server exposes no such attribute", which is not the same as ``None``.
_UNCHECKED = object()

PENDING_FIELDS: tuple[str, ...] = (
    "asked_at",
    "question",
    "why",
    "clarification_id",
    "turn_id",
    "thread_id",
    "source",
    "basis",
)

PENDING_SOURCE_INTERRUPT = "interrupt"

_CLARIFICATION_PREFIX = "clar-"

#: How a conversation ended, from its last turn's ``Outcome``. ``no_sql`` is an answer from context.
Ending = Literal["answered", "not_answered", "waiting", "stopped", "failed"]
ENDINGS: tuple[str, ...] = get_args(Ending)

_ENDING_OF_OUTCOME: dict[str, str] = {
    Outcome.answered.value: "answered",
    Outcome.no_sql.value: "answered",
    Outcome.refused.value: "not_answered",
    Outcome.capped.value: "not_answered",
    # A recorded clarification is a decline: a pause writes no turn.
    Outcome.clarification.value: "stopped",
    Outcome.crashed.value: "failed",
}

#: Only what the list shows. ``values.turns`` is extracted whole, since a path cannot fan out.
_CONVERSATION_EXTRACT: dict[str, str] = {"turns": "values.turns", "question": "values.question"}


class InProcessServerRequired(RuntimeError):
    """Raised instead of ``get_client``: that call appends an ``app=None`` transport only server startup drains."""


class PendingPage(NamedTuple):
    """``truncated`` when the window or the walk stops early: a short list would read as nobody waiting."""

    rows: list[dict[str, Any]]
    truncated: bool
    threads_scanned: int


def turn_of_clarification(clarification_id: str) -> str | None:
    """``turn_id`` is a hex digest with no ``-``, so the last ``-`` splits exactly. A foreign id stays unlinked."""
    text = str(clarification_id or "")
    if not text.startswith(_CLARIFICATION_PREFIX):
        return None
    body = text[len(_CLARIFICATION_PREFIX) :]
    turn_id, sep, _tail = body.rpartition("-")
    if not sep or not turn_id:
        return None
    return turn_id


class ConversationPage(NamedTuple):
    """``total`` counts the rows that match the filter before the window."""

    rows: list[dict[str, Any]]
    total: int
    truncated: bool
    threads_scanned: int


class ThreadReader:
    """Unanswered clarifications write nothing, so the queue is ``threads.search(status="interrupted")``."""

    PENDING_FIELDS = PENDING_FIELDS

    def __init__(self, client_factory: Any | None = None) -> None:
        self._client_factory = client_factory
        self._client: Any | None = None

    def pending(self, *, limit: int = 50, offset: int = 0) -> PendingPage:
        wanted = max(1, int(limit))
        start = max(0, int(offset))
        return _pending(limit=wanted, offset=start, client=self._client_once())

    def conversations(
        self, *, limit: int = 50, offset: int = 0, ending: str | None = None
    ) -> ConversationPage:
        """Newest first. ``ending`` filters on :func:`ending_of`."""
        return _blocking(
            _conversations_async(
                limit=max(1, int(limit)),
                offset=max(0, int(offset)),
                ending=ending,
                client=self._client_once(),
            )
        )

    def _client_once(self) -> Any:
        """One client: each ``get_client()`` opens an ``httpx.AsyncClient`` nothing closes.
        Lazy while ``langgraph_api.server`` is half-imported; an ASGI transport binds no loop."""
        if self._client is None:
            factory = self._client_factory or _in_process_client
            self._client = factory()
        return self._client


def _in_process_client() -> Any:
    reason: str | None = None
    # LangGraph sets this while importing the app, before a request can arrive.
    if os.environ.get("__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT") == "true":
        reason = "the server is still importing this application"
    else:
        try:
            from langgraph_api.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001 -- `get_client` catches Exception here as well
            reason = f"{type(exc).__name__}: {exc}"
        else:
            # Importable is not running. Only server startup sets `_MAIN_LOOP`. Older servers lack it.
            from langgraph_api import asyncio as server_loop

            if getattr(server_loop, "_MAIN_LOOP", _UNCHECKED) is None:
                reason = "the server's modules are imported but no server is running"
    if reason is not None:
        raise InProcessServerRequired(
            "the pending-clarification reader only works inside the LangGraph Agent server: it "
            "reads thread state over langgraph_sdk's in-process ASGI transport, which resolves "
            "only while `langgraph_api.server.app` exists in this process "
            f"({reason}). Outside the server there is no store to read -- run `uv run langgraph "
            "dev` and call `/clarifications/pending` on that, or pass a `client_factory`, which "
            "is what the tests do."
        )

    from langgraph_sdk import get_client

    return get_client()


def _blocking(coro: Any) -> Any:
    """Starlette runs a sync route in a worker thread with no loop, so ``asyncio.run`` is safe."""
    return asyncio.run(coro)


def _pending(*, limit: int, offset: int, client: Any) -> PendingPage:
    return _blocking(_pending_async(limit=limit, offset=offset, client=client))


async def _walk(
    client: Any,
    *,
    status: str | None,
    sort_order: str,
    select: list[str],
    extract: dict[str, str] | None,
    rows_of: Callable[[Any], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], bool, int]:
    """``(rows, truncated, threads)``. Truncated when the walk hits the bound, including a store that ends on it."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_offset = 0
    while page_offset < _MAX_THREADS:
        page = await client.threads.search(
            status=status,
            limit=_THREAD_PAGE,
            offset=page_offset,
            sort_by="updated_at",
            sort_order=sort_order,
            select=select,
            extract=extract,
        )
        if not page:
            return rows, False, len(seen)
        for thread in page:
            if isinstance(thread, Mapping):
                seen.add(str(thread.get("thread_id") or ""))
            rows.extend(rows_of(thread))
        page_offset += len(page)
        if len(page) < _THREAD_PAGE:
            return rows, False, len(seen)
    return rows, True, len(seen)


async def _pending_async(*, limit: int, offset: int, client: Any) -> PendingPage:
    """The window is over rows, not threads: one thread can hold more than one interrupt."""
    rows, truncated, scanned = await _walk(
        client,
        status="interrupted",
        sort_order="asc",
        select=["thread_id", "updated_at", "interrupts", "metadata"],
        extract=None,
        rows_of=_open_questions_of,
    )

    rows.sort(key=lambda row: str(row.get("asked_at") or ""))
    window = rows[offset : offset + limit]
    if offset + limit < len(rows):
        truncated = True
    return PendingPage(rows=window, truncated=truncated, threads_scanned=scanned)


async def _conversations_async(
    *, limit: int, offset: int, ending: str | None, client: Any
) -> ConversationPage:
    rows, truncated, scanned = await _walk(
        client,
        status=None,
        sort_order="desc",
        select=["thread_id", "updated_at", "status", "interrupts"],
        extract=_CONVERSATION_EXTRACT,
        rows_of=_conversation_of,
    )
    if ending is not None:
        rows = [row for row in rows if row["ending"] == ending]
    window = rows[offset : offset + limit]
    return ConversationPage(
        rows=window,
        total=len(rows),
        truncated=truncated or offset + limit < len(rows),
        threads_scanned=scanned,
    )


def ending_of(*, waiting: bool, status: Any, last_outcome: Any) -> str | None:
    """``None`` for a conversation with no finished turn and nothing failed."""
    if waiting:
        return "waiting"
    if status == "error":
        return "failed"
    if last_outcome is None:
        return None
    return _ENDING_OF_OUTCOME.get(str(last_outcome), "failed")


def _conversation_of(thread: Any) -> list[dict[str, Any]]:
    if not isinstance(thread, Mapping):
        return []
    extracted = thread.get("extracted")
    extracted = extracted if isinstance(extracted, Mapping) else {}
    entries = [e for e in (extracted.get("turns") or ()) if isinstance(e, Mapping)]
    turns = [
        {
            "asked_at": entry.get("asked_at"),
            "question": entry.get("question"),
            "outcome": entry.get("outcome"),
        }
        for entry in entries
    ]
    waiting = bool(_open_questions_of(thread))
    question = extracted.get("question")
    return [
        {
            "thread_id": thread.get("thread_id"),
            "updated_at": thread.get("updated_at"),
            "status": thread.get("status"),
            "waiting": waiting,
            "ending": ending_of(
                waiting=waiting,
                status=thread.get("status"),
                last_outcome=turns[-1]["outcome"] if turns else None,
            ),
            "question": question if isinstance(question, str) and question.strip() else None,
            "elided_turns": int(entries[0].get("elided_turns") or 0) if entries else 0,
            "turns": turns,
        }
    ]


def _open_questions_of(thread: Any) -> list[dict[str, Any]]:
    """``asked_at`` is the thread ``updated_at``: the runtime writes ``interrupts`` in that same update."""
    if not isinstance(thread, Mapping):
        return []
    interrupts = thread.get("interrupts")
    if not isinstance(interrupts, Mapping):
        return []
    thread_id = thread.get("thread_id")
    asked_at = thread.get("updated_at")
    out: list[dict[str, Any]] = []
    for task_id, items in interrupts.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            value = item.get("value")
            if not isinstance(value, Mapping) or value.get("kind") != "clarification":
                continue
            clarification_id = str(value.get("clarification_id") or "")
            out.append(
                {
                    "asked_at": asked_at,
                    "question": value.get("question"),
                    "why": value.get("why"),
                    "clarification_id": clarification_id or None,
                    "turn_id": turn_of_clarification(clarification_id),
                    "thread_id": thread_id,
                    "source": PENDING_SOURCE_INTERRUPT,
                    "basis": value.get("basis"),
                    "interrupt_id": item.get("id"),
                    "task_id": task_id,
                }
            )
    return out
