"""The audit surface's reader, sourced from **thread state** instead of the JSONL log.

It fills ``make_app``'s ``turn_log`` seam, which is now **readers only** -- ``list_turns``,
``get_turn``, ``summarise_turn``, ``SUMMARY_FIELDS``, ``TURN_LOG_DIR``. There is no ``append_turn``
because there is no second sink: ``api/graph_app.record_node`` returns the turn onto
``ServeState.turns`` and the checkpointer persists it. `api/trace_store.py` and
``runs/serve/*.jsonl`` are deleted.

The wire contract is byte-identical to what the JSONL-backed reader served, which is what lets
`npm run check:api` act as the regression test for a change of *store*.

**Why thread state can now answer this at all.** A turn's record used to exist only in the log,
because ``PER_TURN_RESET`` clears the per-turn channels at the top of every turn and a checkpoint
therefore described only the newest one. ``ServeState.turns`` accumulates instead, so the thread
holds every turn of its own conversation.

**The reads go through the in-process client, which is an authentication bypass by
construction.** ``get_client(url=None)`` mounts the server's own ASGI app at
``root_path="/noauth"``, and ``langgraph_api.auth.middleware.ConditionalAuthenticationMiddleware``
short-circuits on exactly that prefix -- "disable auth for requests originating from SDK ASGI
transport" (``auth/middleware.py:46``). Upstream names this the auth-bypass primitive behind
GHSA-q3v5-r5ch-p57j. So "no credential to hold" is not a convenience worth advertising: this
reader runs *inside* the door, holding **no principal**, and every thread in the store is visible
to it. Reaching the platform's store any other way would mean either a second checkpointer handle
(a second answer to what a thread contains) or a loopback HTTP request to ourselves, so the bypass
is kept and written down rather than worked around.

What that costs a fork which puts a credential back in front of ``/audit/turns``
(``docs/enterprise-fork.md`` is where that trigger is recorded): the credential gates the *route*
and does nothing to the reader behind it. Per-caller filtering therefore has to happen in
:func:`_collect_async`, on the record's ``identity`` and the thread's ``metadata`` -- both are
projected there already, and the comment at that line is the hook. **Nothing filters today and
nothing here should**: ADR 0012 and ``api/auth.py`` record one principal and no way to tell two
callers apart, so an access-control system written now would be a second answer to a question
this repository has not yet asked. A second principal is the change that makes the hook
load-bearing.

**And it only runs inside the Agent server.** Outside it the in-process transport cannot resolve,
and :class:`InProcessServerRequired` says so -- see that class for why the SDK's own failure mode
could not be left in place.

"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from ..register.quantity import Measured

__all__ = ["ThreadTurnLog", "InProcessServerRequired", "SUMMARY_FIELDS", "summarise_turn"]

#: List-view columns (a subset of record field names), in display order.
SUMMARY_FIELDS: tuple[str, ...] = (
    "turn_id",
    "run_id",
    "thread_id",
    "question_id",
    "db_id",
    "outcome",
    "terminal_reason",
    "schemas",
    "generated_sql",
    "latency_sec",
    # The attempt ledger. A transcript rebuilt from a record has to show the same governance
    # badge the live turn showed; without it a turn renders "no SQL attempted" above its own
    # SQL panel.
    "execution",
)


def summarise_turn(entry: Mapping[str, Any]) -> dict[str, Any]:
    """One envelope projected to a list row.

    ``missing_required`` is computed here rather than stored, so a turn recorded before a register
    row existed is judged by today's register -- the column asks "is this turn quotable", which is
    a question about the current declaration and not about the date it was written.
    """
    from ..register.record import missing_required

    record = entry.get("record") or {}
    summary = {name: record.get(name) for name in SUMMARY_FIELDS}
    # An absent quantity reaches the wire as **null**, not as its own object. `latency_sec` is a
    # `Measured` when no wrapped node stamped `turn_started_at` -- rare, but reachable on a turn
    # that crashed in `accept`. The JSONL log used to flatten it with `json.dumps(default=str)`
    # and the schema admitted a string for exactly that reason; state keeps the object, and the
    # client validates this field as `z.number().nullable()`, so one such row would fail the parse
    # for **every** row in the response. Null is also the honest reading: unmeasured is not a
    # number. This is not formatting -- `Measured.render` still owns that.
    for name, value in list(summary.items()):
        if isinstance(value, Measured) or (
            isinstance(value, Mapping) and "why" in value and "state" in value
        ):
            summary[name] = None
    summary["asked_at"] = entry.get("asked_at")
    summary["question"] = entry.get("question")
    summary["answer_text"] = entry.get("answer_text")
    summary["outcome"] = entry.get("outcome") or record.get("outcome")
    summary["licensed_count"] = len(record.get("licensed") or ())
    execution = record.get("execution") or {}
    attempts = execution.get("attempts") or ()
    summary["attempts"] = len(attempts)
    summary["attempts_passed"] = sum(1 for a in attempts if (a or {}).get("passed"))
    summary["incomplete_fields"] = len(missing_required(record))
    return summary

#: Threads fetched per ``threads.search`` page. A page is a round trip and every caller's budget
#: is counted in *turns*, so this trades one for the other; 50 covers the whole store at this
#: repository's volume in a single call. Pages are pulled on demand (:func:`_threads` is a
#: generator), so a request the first page satisfies costs exactly one round trip.
_THREAD_PAGE = 50

#: Hard bound on threads scanned for one request, so a store that grows cannot turn the audit
#: list into an unbounded read. **Nothing reports when it bites**, which is a known gap and
#: not a design: `/audit/turns` has no truncation field on the wire (ADR 0009 D2 argues one
#: is owed), and adding one means changing the shape this swap deliberately keeps identical.
_MAX_THREADS = 1000

#: Stands for "this server exposes no such attribute", which is not the same as ``None``. Used
#: once, by :func:`_in_process_client`.
_UNCHECKED = object()


class InProcessServerRequired(RuntimeError):
    """This reader was used outside the Agent server process, where it cannot work.

    Named, because the SDK's own failure here is neither named nor free. ``get_client(url=None)``
    tries ``from langgraph_api.server import app``; when that fails it *swallows* the exception,
    builds ``ASGITransport(app=None, root_path="/noauth")`` and **appends it** to the module global
    ``langgraph_sdk._shared.utilities._registered_transports`` -- a list drained only by
    ``configure_loopback_transports(app)``, which the server calls at startup and nothing else
    ever calls. Outside the server that list therefore grows by one entry per call and the call
    still fails, later and elsewhere, with a bare ``TypeError: 'NoneType' object is not callable``
    naming neither the reader nor the reason. Measured before this class existed: two
    ``list_turns()`` calls, two ``TypeError``s, two leaked transports.

    :func:`_in_process_client` raises this *instead of* calling ``get_client``, so nothing is
    registered and nothing leaks.
    """


class ThreadTurnLog:
    """``turn_log`` over LangGraph thread state.

    ``client_factory`` exists so this is testable without a server: the production default is
    :func:`_in_process_client`, whose transport only resolves inside the Agent server, and a
    reader whose ordering and thread-isolation rules can only be exercised by booting a server is
    a reader whose rules go untested.
    """

    def __init__(self, client_factory: Any | None = None) -> None:
        self._client_factory = client_factory
        self._client: Any | None = None

    summarise = staticmethod(summarise_turn)
    #: Read off the instance by ``routes.turns_page`` to build ``meta.columns``.
    SUMMARY_FIELDS = SUMMARY_FIELDS

    @property
    def TURN_LOG_DIR(self) -> Any:  # noqa: N802 -- the seam's name, kept so the wire key is
        """Where the rows this reader returns actually come from, for the audit footer.

        **Not the checkpoint database.** ``threads.search`` reads the *thread row's* ``values``,
        and under ``langgraph dev`` the in-memory runtime copies the checkpoint into that row at
        run completion and persists it to ``.langgraph_api/.langgraph_ops.pckl`` — so these rows
        come out of a pickle, while ``runs/conversations.sqlite`` holds the checkpoints nothing
        here reads. Naming the SQLite file was wrong and pointed an auditor at the wrong artifact.

        That pickle is treated as a disposable cache by its owner: a ``ModuleNotFoundError`` while
        loading it logs "Removing invalid cache data" and **deletes the file**. Since thread
        ``values`` holds live Python objects, renaming or moving a module whose instances reach
        state destroys the audit history while the checkpoints survive unread. See ADR 0014.

        The attribute keeps its name because ``meta.log_dir`` is on the wire and the client's
        schema and footer both read that key.
        """
        from governed_bi.paths import REPO_ROOT

        return REPO_ROOT / ".langgraph_api" / ".langgraph_ops.pckl"

    def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[dict[str, Any]]:
        """Newest turns first, as summaries. ``thread_id`` narrows to one conversation.

        Ordering is ``updated_at`` descending **per thread**, and newest-first *within* a thread
        by reversing its ``turns``. That is not the same as a global sort by ``asked_at``, so the
        rows are sorted once more at the end: two conversations interleave in time, and a reader
        who sorts by "when was this asked" must not see one thread's older turn above another's
        newer one.
        """
        wanted = max(1, int(limit))
        out = [
            self.summarise(entry)
            for entry in self._entries(limit=wanted, thread_id=thread_id)
        ]
        out.sort(key=lambda row: str(row.get("asked_at") or ""), reverse=True)
        return out[:wanted]

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        """One turn's full envelope, or ``None``.

        A scan, as the log's was, but a **short-circuiting** one: the wanted ``turn_id`` goes down
        into :func:`_collect_async`, which tests each envelope as its thread arrives and returns
        on the first match. Threads are paged on demand, so a turn in the newest thread costs one
        round trip and materialises one envelope. Only a *miss* pays for the whole scan, and a
        miss is the only case that has to, bounded by ``_MAX_THREADS`` like every other read
        here (no ``limit``: with a ``turn_id`` the answer is one row or none, so a turn budget
        would be a second stopping rule that can never fire).

        It stays a scan because an index over one developer's traffic would be a second source of
        truth for a millisecond lookup, and because the store cannot answer the question either:
        the ``values`` filter ``threads.search`` accepts is JSONB *containment* over the top-level
        ``values`` dict, and the in-memory runtime this repository runs implements it as
        ``is_jsonb_contained`` (``langgraph_runtime_inmem/ops.py:729``), which for a list-valued
        key falls through to ``superset[key] != value`` -- equality against the whole ``turns``
        list. There is no way to ask it for a thread containing *one* matching element.
        """
        for entry in self._entries(limit=None, thread_id=None, turn_id=str(turn_id)):
            return dict(entry)
        return None

    # ── the store ────────────────────────────────────────────────────────────

    def _entries(
        self, *, limit: int | None, thread_id: str | None, turn_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        for entry in _collect(
            limit=limit, thread_id=thread_id, turn_id=turn_id, client=self._client_once()
        ):
            yield entry

    def _client_once(self) -> Any:
        """The one client this reader uses, built on first read.

        **One, not one per call.** Each ``get_client()`` builds an ``httpx.AsyncClient`` that
        nothing ever ``aclose()``s, so a client per request was a per-request object leak on top
        of the transport leak :class:`InProcessServerRequired` describes. One client over an ASGI
        transport holds no sockets, so there is nothing for a shutdown hook to close and no hook
        is added.

        **Built lazily**, because ``routes._build_app`` constructs this class during
        ``load_custom_app``, which is the one window where ``langgraph_api.server`` is
        half-imported by construction and the in-process transport genuinely cannot resolve.

        **Safe to reuse across loops**, which is the part not to assume: ``_blocking`` opens a
        fresh ``asyncio.run`` per call, so a cached client outlives the loop that first used it.
        It survives that because nothing in the path binds to a loop at construction --
        ``httpx.AsyncClient`` over an ASGI transport has no connection pool, and
        ``langgraph_api.asgi_transport.ASGITransport`` calls ``asyncio.get_running_loop()`` and
        creates its futures *per request*, handing the app coroutine to the server's own
        ``_MAIN_LOOP`` (``call_soon_in_main_loop``) precisely because the caller is expected to be
        on some other loop. ``tests/api/test_the_audit_surface_reads_thread_state.py`` drives a
        real ``httpx.AsyncClient`` over an ASGI app through two separate ``asyncio.run`` calls to
        keep that a measured claim rather than a read one.
        """
        if self._client is None:
            factory = self._client_factory or _in_process_client
            self._client = factory()
        return self._client


def _in_process_client() -> Any:
    """``langgraph_sdk``'s in-process client, or a named refusal -- never a leak.

    The import is attempted here, and its failure raised, rather than left to ``get_client``:
    :class:`InProcessServerRequired` records what ``get_client`` does with the same failure.
    """
    reason: str | None = None
    if os.environ.get("__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT") == "true":
        # The window in which ``langgraph_api.api.load_custom_app`` imports *this* application.
        # ``get_client`` honours this flag ahead of any import attempt and registers a deferred
        # ``app=None`` transport, so it leaks on this branch too. No request can arrive during
        # the window, so refusing is not a behaviour anything depends on.
        reason = "the server is still importing this application"
    else:
        try:
            from langgraph_api.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001 -- `get_client` catches Exception here as well
            reason = f"{type(exc).__name__}: {exc}"
        else:
            # Importable is not the same as *running*, and a test process is where the two come
            # apart: anything that has configured the runtime can import the module. The
            # transport dispatches the app onto ``langgraph_api.asyncio._MAIN_LOOP``, which only
            # ``langgraph_runtime_inmem.lifespan`` sets and only at server startup, so without it
            # the very next thing is an unnamed ``RuntimeError: No event loop set`` at request
            # time -- the failure this class exists to stop being anonymous. Checked through
            # ``getattr`` with a non-``None`` default: older servers used plain
            # ``httpx.ASGITransport``, which needs no such loop, and their absence of the
            # attribute is not an absence of a server.
            from langgraph_api import asyncio as server_loop

            if getattr(server_loop, "_MAIN_LOOP", _UNCHECKED) is None:
                reason = "the server's modules are imported but no server is running"
    if reason is not None:
        raise InProcessServerRequired(
            "the audit turn reader only works inside the LangGraph Agent server: it reads thread "
            "state over langgraph_sdk's in-process ASGI transport, which resolves only while "
            "`langgraph_api.server.app` exists in this process "
            f"({reason}). Outside the server there is no store to read -- run `uv run langgraph "
            "dev` and call `/audit/turns` on that, or pass a `client_factory`, which is what the "
            "tests do."
        )

    from langgraph_sdk import get_client

    return get_client()


def _blocking(coro: Any) -> Any:
    """Drive an async client call from a sync route handler.

    Starlette runs a sync ``def`` handler in a worker thread with no running loop, so
    ``asyncio.run`` is safe here — the same reasoning the deleted ``routes._chat_graph`` recorded
    for the graph facade. If this is ever called from an async handler it must be awaited instead.
    """
    return asyncio.run(coro)


def _collect(
    *, limit: int | None, thread_id: str | None, turn_id: str | None, client: Any
) -> list[dict[str, Any]]:
    return _blocking(
        _collect_async(limit=limit, thread_id=thread_id, turn_id=turn_id, client=client)
    )


async def _collect_async(
    *, limit: int | None, thread_id: str | None, turn_id: str | None, client: Any
) -> list[dict[str, Any]]:
    """Envelopes from thread state, newest thread first, newest turn first within a thread.

    Three narrowings, and each is the only thing between a route and the whole store:
    ``thread_id`` asks the store for one thread, ``turn_id`` returns on the first match, and
    ``limit`` stops pulling pages once it is met. Threads arrive one at a time from
    :func:`_threads` so that stopping stops the *paging* and not merely the filtering.
    """
    wanted_turn = None if turn_id is None else str(turn_id)
    out: list[dict[str, Any]] = []
    async for thread in _threads(client, thread_id=thread_id):
        for entry in reversed(_turns_of(thread)):
            if not isinstance(entry, Mapping):
                continue
            record = entry.get("record") or {}
            # Belt for the `ids=` filter in `_threads`, and the only filter at all on the paged
            # path: a thread whose state was migrated or hand-edited could carry a row for
            # another thread, and an audit view showing one conversation inside another is worse
            # than an empty one. `tests/api/test_http_contract.py` pins the no-leak property.
            if thread_id is not None and record.get("thread_id") not in (None, thread_id):
                continue
            if wanted_turn is not None and str(record.get("turn_id") or "") != wanted_turn:
                continue
            # ── the authorization hook ───────────────────────────────────────────────────
            # A per-caller filter goes on the line above this comment, and the module docstring
            # says why it can go nowhere else: this reader sits behind `/noauth`, so it holds no
            # principal and a credential on the route cannot reach it. `record["identity"]` is
            # the caller `serve/accept.py` recorded, and `thread["metadata"]` is projected beside
            # it. There is one principal today (ADR 0012), so a filter here would compare every
            # caller against itself; a second principal is what makes this line do work.
            out.append(dict(entry))
            if wanted_turn is not None:
                return out
        if limit is not None and len(out) >= limit:
            break
    return out


async def _threads(client: Any, *, thread_id: str | None) -> AsyncIterator[Any]:
    """Threads to read, newest-updated first, **one at a time and one page at a time**.

    A generator rather than a list because the caller's stopping conditions are counted in turns
    while the store's paging is counted in threads: fifty threads holding one turn each satisfy a
    budget of fifty, one thread holding fifty satisfies it alone, and a ``get_turn`` hit in the
    newest thread satisfies itself. Only the consumer knows which, so a page is fetched when the
    consumer asks for the thread after the last one it was handed. Collecting first is how
    ``get_turn`` came to page to ``_MAX_THREADS`` and materialise every envelope in the store in
    order to find one id.

    ``extract`` rather than ``select=["values"]``: an unprojected thread carries the whole of
    ``ServeState``, measured at 2.42 MB for sixteen threads, because ``values`` holds the
    delivered context. Extracting one path reads the same rows for kilobytes. The path root must
    be one of ``values``/``metadata``/``config``/``interrupts``, and ``values.turns`` is the
    channel ``record_node`` appends to. ``metadata`` is selected on both paths because it is half
    of what the authorization hook in :func:`_collect_async` would filter on.
    """
    if thread_id is not None:
        for thread in await client.threads.search(
            ids=[thread_id],
            limit=1,
            select=["thread_id", "metadata"],
            extract={"turns": "values.turns"},
        ):
            yield thread
        return

    offset = 0
    while offset < _MAX_THREADS:
        page = await client.threads.search(
            limit=_THREAD_PAGE,
            offset=offset,
            sort_by="updated_at",
            sort_order="desc",
            select=["thread_id", "updated_at", "metadata"],
            extract={"turns": "values.turns"},
        )
        if not page:
            return
        for thread in page:
            yield thread
        offset += len(page)
        if len(page) < _THREAD_PAGE:
            return


def _turns_of(thread: Any) -> list[Any]:
    """This thread's ``turns`` rows, from wherever the client put them.

    ``extract`` lands values under ``extracted``; a thread selected *with* ``values`` carries
    them under ``values``. Reading both means a caller that changes its projection does not
    silently start seeing zero turns.
    """
    if isinstance(thread, Mapping):
        extracted = thread.get("extracted")
        if isinstance(extracted, Mapping) and isinstance(extracted.get("turns"), list):
            return list(extracted["turns"])
        values = thread.get("values")
        if isinstance(values, Mapping) and isinstance(values.get("turns"), list):
            return list(values["turns"])
    return []
