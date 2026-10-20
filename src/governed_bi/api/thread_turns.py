"""The pending-clarification reader, over the platform's interrupt state.

Reads go through the in-process client (``get_client(url=None)``), which LangGraph mounts at
``/noauth`` and so sees every thread with no credential. That avoids a second checkpointer handle
or a loopback HTTP call to ourselves. It only resolves inside the Agent server; outside it,
:class:`InProcessServerRequired` is raised.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

__all__ = [
    "PendingClarifications",
    "PendingPage",
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

#: List-view columns for the pending queue, in display order. ``meta.columns`` names them.
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

#: The only ``source`` this queue writes: a live ``ask_user`` interrupt.
PENDING_SOURCE_INTERRUPT = "interrupt"

#: Prefix ``serve/tools.py`` builds a clarification id with: ``f"clar-{turn_id}-{digest}"``.
_CLARIFICATION_PREFIX = "clar-"


class InProcessServerRequired(RuntimeError):
    """This reader was used outside the Agent server process, where it cannot work.

    Raised instead of calling ``get_client``, which on the same failure appends an ``app=None``
    transport to an SDK module global that only server startup drains, then fails later with a
    bare ``TypeError``.
    """


class PendingPage(NamedTuple):
    """One page of the queue, plus what it could not show.

    ``truncated`` is set when the window or the ``_MAX_THREADS`` walk cut rows off, because a
    silently short list reads as "nobody is waiting". ``threads_scanned`` counts threads read.
    """

    rows: list[dict[str, Any]]
    truncated: bool
    threads_scanned: int


def turn_of_clarification(clarification_id: str) -> str | None:
    """The ``turn_id`` inside a clarification id, or ``None`` when the shape is not one we mint.

    ``turn_id`` is a hex digest with no ``-``, so partitioning on the last ``-`` is exact. Returns
    ``None`` rather than guessing, so a foreign id leaves the row unlinked instead of mislinked.
    """
    text = str(clarification_id or "")
    if not text.startswith(_CLARIFICATION_PREFIX):
        return None
    body = text[len(_CLARIFICATION_PREFIX) :]
    turn_id, sep, _tail = body.rpartition("-")
    if not sep or not turn_id:
        return None
    return turn_id


class PendingClarifications:
    """Questions the engine asked and nobody has answered, across every conversation.

    An unanswered clarification writes nothing until it is resumed, so the queue is the
    platform's interrupt state: ``threads.search(status="interrupted")``.
    """

    #: Read off the instance by the route to build ``meta.columns``.
    PENDING_FIELDS = PENDING_FIELDS

    def __init__(self, client_factory: Any | None = None) -> None:
        self._client_factory = client_factory
        self._client: Any | None = None

    def pending(self, *, limit: int = 50, offset: int = 0) -> PendingPage:
        """Oldest question first: a work queue, so the row that waited longest comes first."""
        wanted = max(1, int(limit))
        start = max(0, int(offset))
        return _pending(limit=wanted, offset=start, client=self._client_once())

    def _client_once(self) -> Any:
        """The one client this reader uses, built on first read.

        One, because each ``get_client()`` builds an ``httpx.AsyncClient`` nothing closes. Lazy,
        because the app is constructed while ``langgraph_api.server`` is half-imported. Safe across
        ``asyncio.run`` calls because an ASGI transport binds no loop at construction.
        """
        if self._client is None:
            factory = self._client_factory or _in_process_client
            self._client = factory()
        return self._client


def _in_process_client() -> Any:
    """``langgraph_sdk``'s in-process client, or a named refusal -- never a leak."""
    reason: str | None = None
    if os.environ.get("__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT") == "true":
        # `get_client` registers a deferred `app=None` transport on this flag ahead of any import.
        # It is set only while the server imports this app, when no request can arrive.
        reason = "the server is still importing this application"
    else:
        try:
            from langgraph_api.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001 -- `get_client` catches Exception here as well
            reason = f"{type(exc).__name__}: {exc}"
        else:
            # Importable is not running: only server startup sets `_MAIN_LOOP`, which the
            # transport dispatches onto. Older servers lack the attribute and need no such loop.
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
    """Drive an async client call from a sync route handler.

    Starlette runs a sync handler in a worker thread with no running loop, so ``asyncio.run`` is
    safe. From an async handler it must be awaited instead.
    """
    return asyncio.run(coro)


def _pending(*, limit: int, offset: int, client: Any) -> PendingPage:
    return _blocking(_pending_async(limit=limit, offset=offset, client=client))


async def _pending_async(*, limit: int, offset: int, client: Any) -> PendingPage:
    """One row per unanswered clarification, from interrupted threads only.

    The window is over rows, not threads, because one thread can hold more than one interrupt.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    async def _walk(
        *,
        status: str | None,
        select: list[str],
        extract: dict[str, str] | None,
        rows_of: Callable[[Any], list[dict[str, Any]]],
    ) -> bool:
        """Page one population into ``rows``. ``True`` iff the walk stopped on the bound.

        A store exhausted at exactly the bound also reports ``True``: over-reporting "you may not
        have everything" is the safe direction for this flag.
        """
        page_offset = 0
        while page_offset < _MAX_THREADS:
            page = await client.threads.search(
                status=status,
                limit=_THREAD_PAGE,
                offset=page_offset,
                sort_by="updated_at",
                sort_order="asc",
                select=select,
                extract=extract,
            )
            if not page:
                return False
            for thread in page:
                if isinstance(thread, Mapping):
                    seen.add(str(thread.get("thread_id") or ""))
                rows.extend(rows_of(thread))
            page_offset += len(page)
            if len(page) < _THREAD_PAGE:
                return False
        return True

    truncated = await _walk(
        status="interrupted",
        select=["thread_id", "updated_at", "interrupts", "metadata"],
        extract=None,
        rows_of=_open_questions_of,
    )

    rows.sort(key=lambda row: str(row.get("asked_at") or ""))
    window = rows[offset : offset + limit]
    if offset + limit < len(rows):
        truncated = True
    return PendingPage(rows=window, truncated=truncated, threads_scanned=len(seen))


def _open_questions_of(thread: Any) -> list[dict[str, Any]]:
    """One row per open clarification on ``thread``, or ``[]``.

    ``asked_at`` is the thread's ``updated_at``, a proxy: for an interrupted thread the runtime
    stamps it in the same update that writes ``interrupts``, and nothing has run since.
    """
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
                    # What a resume would have to name.
                    "interrupt_id": item.get("id"),
                    "task_id": task_id,
                }
            )
    return out
