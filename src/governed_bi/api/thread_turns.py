"""The audit surface's reader, sourced from **thread state** instead of the JSONL log.

It fills ``make_app``'s ``turn_log`` seam, which is now **readers only** -- ``list_turns``,
``get_turn``, ``clarifications_of``, ``summarise_turn``, ``SUMMARY_FIELDS``, ``TURN_LOG_DIR``.
There is no ``append_turn``
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
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any, NamedTuple

from ..register.quantity import Measured

__all__ = [
    "ThreadTurnLog",
    "PendingClarifications",
    "PendingPage",
    "InProcessServerRequired",
    "SUMMARY_FIELDS",
    "PENDING_FIELDS",
    "summarise_turn",
]

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
        if isinstance(value, Measured) or (isinstance(value, Mapping) and "why" in value and "state" in value):
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
#: list into an unbounded read. On `/audit/turns` **nothing reports when it bites**, which is a
#: known gap and not a design: that route has no truncation field on the wire (ADR 0009 D2 argues
#: one is owed), and adding one means changing the shape this swap deliberately keeps identical.
#: The pending queue *does* report it -- :attr:`PendingPage.truncated` -- and :func:`_pending_async`
#: is where what that flag can and cannot tell a reader is written down.
_MAX_THREADS = 1000

#: The list read's projection. ``extract`` rather than ``select=["values"]`` for the reason
#: :func:`_threads` gives at length -- an unprojected thread carries the whole of ``ServeState``,
#: measured at 2.42 MB for sixteen threads.
_EXTRACT: dict[str, str] = {"turns": "values.turns"}

#: :meth:`ThreadTurnLog.clarifications_of`'s projection. A separate constant and not a second path
#: on :data:`_EXTRACT`, because the two reads want different things: the list read pays for this
#: channel on **every** thread it pages through and never looks at it, while one turn's trace wants
#: it for one thread it can already name. Widening the paged read to serve the narrow one is how a
#: 2.42 MB projection got measured in the first place.
_CLARIFICATION_EXTRACT: dict[str, str] = {"clarifications": "values.clarifications"}

#: The ``raised`` channel's projection, for two reads with opposite economics.
#: :meth:`ThreadTurnLog.raised_of` wants it for one thread it can already name; the pending queue's
#: note walk wants it off **every** thread in the store and -- unlike the case
#: :data:`_CLARIFICATION_EXTRACT` argues against -- does look at what it pays for, because an open
#: note *is* the row. :func:`_pending_async` carries the cost of that walk.
_RAISED_EXTRACT: dict[str, str] = {"raised": "values.raised"}

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

    def __init__(self, client_factory: Any | None = None, state_writer: Any | None = None) -> None:
        self._client_factory = client_factory
        self._client: Any | None = None
        #: In-process append of ``raised``. Tests inject a callable
        #: ``(thread_id, row) -> None``; production hops onto the server loop and
        #: files through ``api/raised_write.py`` (checkpointer + thread-row copy).
        self._state_writer = state_writer

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
        out = [self.summarise(entry) for entry in self._entries(limit=wanted, thread_id=thread_id)]
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

    def clarifications_of(self, thread_id: str, turn_id: str) -> list[dict[str, Any]]:
        """What this turn asked its reader mid-flight, and what they answered.

        The other half of the pair :class:`PendingClarifications` reads: that reader takes the
        questions still sitting in interrupt state — the ones nobody answered — and this one takes
        the ones somebody did, which ``serve/tools.py`` writes into the ``clarifications`` channel
        on the far side of ``interrupt()``. Between them they cover every clarification the engine
        has ever asked, and neither needs a store of its own.

        **Read-side only, and one round trip.** Both channels are already in thread state; the
        join needs no new field because ``ask_user`` puts ``turn_id`` on the row. ``thread_id`` is
        a parameter rather than something looked up because every caller is holding the turn's
        record, which carries it — so this is ``ids=[thread_id]``, not a scan.

        Returns ``[]`` for a turn that asked nothing. That is a real answer and not a shrug: a
        clarification either happened or did not, and this reader has read the channel either way.
        """
        client = self._client_once()
        threads = _blocking(
            client.threads.search(
                ids=[str(thread_id)],
                limit=1,
                select=["thread_id", "metadata"],
                extract=_CLARIFICATION_EXTRACT,
            )
        )
        for thread in threads or ():
            return _answered_clarifications_of(thread).get(str(turn_id)) or []
        return []

    def raised_of(self, thread_id: str, turn_id: str) -> list[dict[str, Any]]:
        """Reader-filed notes on this turn, from the ``raised`` channel.

        Clone of :meth:`clarifications_of`: one keyed read, ``[]`` when the turn has none.
        """
        client = self._client_once()
        threads = _blocking(
            client.threads.search(
                ids=[str(thread_id)],
                limit=1,
                select=["thread_id", "metadata"],
                extract=_RAISED_EXTRACT,
            )
        )
        for thread in threads or ():
            return [
                dict(row)
                for row in _channel_of(thread, "raised")
                if isinstance(row, Mapping) and str(row.get("turn_id") or "") == str(turn_id)
            ]
        return []

    def append_raised(self, thread_id: str, row: Mapping[str, Any]) -> None:
        """Append one ``raised`` row via in-process ``aupdate_state(as_node="raise_note")``.

        Not ``threads.update`` and not ``command.update``: both are client-writable surfaces
        ``api/auth.py`` denies. The unattached ``raise_note`` node is the only legal writer.
        Production does not use the saver-less Pregel ``make_graph`` compiled: that graph
        has no checkpointer, and a checkpoint write that never copies the thread row is
        invisible to pending and trace. See ``api/raised_write.py``.
        """
        payload = dict(row)
        if self._state_writer is not None:
            self._state_writer(str(thread_id), payload)
            return
        from governed_bi.api.raised_write import append_raised_on_thread

        append_raised_on_thread(str(thread_id), payload)

    # ── the store ────────────────────────────────────────────────────────────

    def _entries(
        self, *, limit: int | None, thread_id: str | None, turn_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        for entry in _collect(limit=limit, thread_id=thread_id, turn_id=turn_id, client=self._client_once()):
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


def _collect(*, limit: int | None, thread_id: str | None, turn_id: str | None, client: Any) -> list[dict[str, Any]]:
    return _blocking(_collect_async(limit=limit, thread_id=thread_id, turn_id=turn_id, client=client))


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
            extract=_EXTRACT,
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
            extract=_EXTRACT,
        )
        if not page:
            return
        for thread in page:
            yield thread
        offset += len(page)
        if len(page) < _THREAD_PAGE:
            return


def _channel_of(thread: Any, name: str) -> list[Any]:
    """One list-valued ``ServeState`` channel, from wherever the client put it.

    ``extract`` lands values under ``extracted``; a thread selected *with* ``values`` carries
    them under ``values``. Reading both means a caller that changes its projection does not
    silently start seeing zero rows.
    """
    if isinstance(thread, Mapping):
        extracted = thread.get("extracted")
        if isinstance(extracted, Mapping) and isinstance(extracted.get(name), list):
            return list(extracted[name])
        values = thread.get("values")
        if isinstance(values, Mapping) and isinstance(values.get(name), list):
            return list(values[name])
    return []


def _turns_of(thread: Any) -> list[Any]:
    """This thread's ``turns`` rows — the channel ``graph_app``'s record node appends to."""
    return _channel_of(thread, "turns")


def _answered_clarifications_of(thread: Any) -> dict[str, list[dict[str, Any]]]:
    """This thread's **answered** clarifications, grouped by the ``turn_id`` that asked.

    The other half of the pair :class:`PendingClarifications` reads. That reader takes the
    questions still sitting in interrupt state — the ones nobody answered — and this one takes the
    ones somebody did, which ``serve/tools.py`` writes into the ``clarifications`` channel on the
    far side of ``interrupt()``. Together they are every clarification the engine has ever asked.

    Read-side only: nothing new is stored. Both channels are already in the thread state this
    reader was going to fetch anyway, and the join needs no new field because ``ask_user`` puts
    ``turn_id`` on the row itself.

    Grouped rather than returned flat because a turn may ask more than once: ``ask_user`` is a
    tool the agent loop can call repeatedly, and ``clarifications_by_call`` is keyed per call.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in _channel_of(thread, "clarifications"):
        if not isinstance(row, Mapping):
            continue
        # `turn_id` off the row, with the id as the fallback for a row written before `ask_user`
        # carried one — `clarification_id` has always contained it (:func:`turn_of_clarification`).
        turn_id = str(row.get("turn_id") or "") or turn_of_clarification(str(row.get("clarification_id") or ""))
        if not turn_id:
            continue
        grouped.setdefault(turn_id, []).append(dict(row))
    return grouped


# ── pending clarifications ───────────────────────────────────────────────────────────────────
#
# A second reader, in this file rather than its own, for one reason: `_in_process_client` is the
# thing that must not have a second implementation. `get_client(url=None)` leaks an `app=None`
# transport into an SDK module global on failure (see `InProcessServerRequired`), and that leak
# was fixed once already. A reader that acquired its own client would reintroduce it.

#: List-view columns for the pending queue, in display order. Same role as `SUMMARY_FIELDS`:
#: `meta.columns` is on the wire and the client renders from it.
#:
#: **Some of these are null on half the rows, which is the shape and not a defect.** The queue
#: unions two populations -- an unanswered interrupt and an open ``raised`` note -- so
#: `clarification_id`/`basis` are null on every note and `report_id` on every interrupt. The rule
#: is that a *declared* column is present and null where it does not apply, never absent: a client
#: forced to tell "no value" from "no such key" ends up guessing which kind of row it holds.
#: `source` says which, and is the one column never null.
PENDING_FIELDS: tuple[str, ...] = (
    "asked_at",
    "question",
    "why",
    "clarification_id",
    "turn_id",
    "thread_id",
    "source",
    "basis",
    # Declared rather than carried, unlike `interrupt_id`/`task_id`, because the client
    # *consumes* it: `ui/components/clarifications/pending-queue.tsx` keys a note's card on it,
    # and a note has no `clarification_id` to key on instead. A column the client renders from
    # and `meta.columns` does not name is a contract that happens to work.
    "report_id",
)

#: Prefix ``serve/tools.py`` builds a clarification id with: ``f"clar-{turn_id}-{digest}"``.
_CLARIFICATION_PREFIX = "clar-"


class PendingPage(NamedTuple):
    """One page of the queue, plus what it could not show.

    ``truncated`` is not cosmetic. ADR 0009 D2/D9 exist because a silently short list reads as
    "this is everything" -- and here the consequence is a reader whose question is waiting and an
    operator who cannot see it. The route puts this on the wire.

    Three different losses raise it and the flag does not say which: the window (``offset+limit``
    landed short of the rows built), and either walk stopping on :data:`_MAX_THREADS`.
    :func:`_pending_async` records what each one costs a reader. One flag for three causes is
    deliberate -- all three mean "this is not everything", which is the only thing the caller can
    act on -- but it is the reason ``threads_scanned`` travels beside it.

    ``threads_scanned`` counts **distinct threads read**, not reads: a paused thread is fetched by
    both walks and counted once.
    """

    rows: list[dict[str, Any]]
    truncated: bool
    threads_scanned: int


def turn_of_clarification(clarification_id: str) -> str | None:
    """The ``turn_id`` inside a clarification id, or ``None`` when the shape is not one we mint.

    ``serve/tools.py`` builds ``f"clar-{turn_id}-{digest}"``, where ``turn_id`` is
    ``session._digest(...)`` -- a sha256 hex prefix, so it never contains ``-`` -- and the digest
    is likewise hex. Partitioning on the *last* ``-`` is therefore exact rather than merely
    usually right, and it stays exact if ``turn_id`` ever gains a ``-``.

    **Returns ``None`` rather than guessing.** An id from some other producer would otherwise be
    silently mangled into a ``turn_id`` that links nowhere, which is worse than an unlinked row.
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

    **Why this can be a pure read with no store of its own.** A clarification that *was* answered
    lands in the ``clarifications`` channel, because ``serve/tools.py`` writes it on the far side
    of ``interrupt()``. One that was *not* answered -- the reader closed the tab -- writes nothing
    at all, so the queue is exactly the half that state does not record. Its truth lives in the
    platform's own interrupt state, which ADR 0014's durable checkpointer made survive a restart;
    before that this reader could not have existed.

    So that half of the queue is one ``threads.search(status="interrupted", ...)``. No ledger file,
    no new table, no write path. ``clarification_id`` carries the ``turn_id``
    (:func:`turn_of_clarification`), which is the join back to the turn that asked -- no separate
    link field is needed.

    ``interrupts`` is *selected* rather than ``extract``ed: it is a first-class
    ``ThreadSelectField``, so none of ``extract``'s ten-path budget is spent on it.

    **The other half is not read-free.** A note a reader files on a finished card lands in
    ``ServeState.raised``, which is state rather than interrupt state, and finding it means paging
    the store unfiltered. :func:`_pending_async` is where that walk's cost, its bound, and what the
    bound hides are written down; this class is only the client.

    **Read-only, deliberately.** Answering from here would mean resuming another caller's thread,
    and ``serve/resume.py::authorise_resume`` refuses that by design (ADR 0006 B9). The owner's
    2026-08-19 decision was that an operator's answer feeds the semantic layer instead, and that
    path is gated on a provenance check this repository does not have -- ``session.py``'s
    ``_visible`` filters ``governance.excluded`` alone, so a ``proposed`` asset already reaches
    the model's context. Until that gate exists, this surface only shows.
    """

    #: Read off the instance by the route to build ``meta.columns``.
    PENDING_FIELDS = PENDING_FIELDS

    def __init__(self, client_factory: Any | None = None) -> None:
        self._client_factory = client_factory
        self._client: Any | None = None

    def pending(self, *, limit: int = 50, offset: int = 0) -> PendingPage:
        """Oldest question first -- a queue, not a feed.

        Sorted ascending on the thread's ``updated_at``, which for an interrupted thread is when
        it paused, because the row that has waited longest is the one that most needs answering.
        That is the opposite of ``list_turns`` and deliberately so: an audit log is read
        newest-first, a work queue oldest-first.
        """
        wanted = max(1, int(limit))
        start = max(0, int(offset))
        return _pending(limit=wanted, offset=start, client=self._client_once())

    def _client_once(self) -> Any:
        """The one client this reader uses.

        See ``ThreadTurnLog._client_once`` for why it is one, why it is built lazily, and why it
        is safe to reuse across ``asyncio.run`` calls.
        """
        if self._client is None:
            factory = self._client_factory or _in_process_client
            self._client = factory()
        return self._client


def _pending(*, limit: int, offset: int, client: Any) -> PendingPage:
    return _blocking(_pending_async(limit=limit, offset=offset, client=client))


async def _pending_async(*, limit: int, offset: int, client: Any) -> PendingPage:
    """One row per thing waiting, unioned from interrupt state and the ``raised`` channel.

    ``interrupts`` is ``{task_id: [Interrupt, ...]}`` and one thread can in principle hold more
    than one, so the window is taken over **rows** rather than threads -- the same reason
    ``_collect_async`` counts turns rather than threads. Only ``kind == "clarification"`` is
    reported from interrupt state; an open ``raised`` note joins the same queue with ``source`` of
    ``from_refusal`` / ``wrong_answer``, and a finished refusal is never rewritten as an interrupt.

    **Two walks, because the populations want opposite queries.** Paused questions are
    ``status="interrupted"`` with ``interrupts`` selected and no ``raised`` path: that population is
    a handful at any volume, so this half stays complete and cannot be pushed past
    :data:`_MAX_THREADS` by a store that grew for unrelated reasons, and reading ``raised`` here too
    would report every note on a paused thread twice.

    **Notes take no status filter at all, and that is the coverage decision.** A note is filed on a
    *finished* turn, and a thread's status when an operator opens the queue is whatever its last run
    left: ``idle`` normally, ``error`` when it crashed -- exactly the turn a reader reaches for the
    flag button on -- ``busy`` while a later question runs. Asking only for ``idle`` hid a filed
    note for as long as its thread sat elsewhere, and *permanently* under ``error``, since nothing
    moves a thread out of it. Enumerating all four is the same read with a hole left for the status
    the platform adds next, so the filter is dropped; :func:`_open_raised_of`'s ``kind``/``open``
    check is what narrows.

    **What that costs, unwritten until now.** The note walk projects ``values.raised`` off *every*
    thread in the store, so the queue's price is proportional to the store rather than to the paused
    handful: ``ceil(threads / _THREAD_PAGE)`` round trips, capped at ``_MAX_THREADS/_THREAD_PAGE``
    = 20 per walk and so 40 for one request, uncached, every time.
    ``extract`` is what holds a row to one channel instead of the whole of ``ServeState`` -- the
    2.42 MB :func:`_threads` measured.

    **So :data:`_MAX_THREADS` can now actually bite**, which it could not when only interrupted
    threads were paged, and the direction of the loss is the unhelpful one: both walks sort
    ``updated_at`` ascending, so the bound keeps the least recently touched threads and drops the
    most recently touched. Past :data:`_MAX_THREADS` threads a note filed today is the first one
    missing. Sorting the other way does not fix it -- a thread's ``updated_at`` tracks its latest
    turn, not its note's age -- so the loss is reported instead, and ``truncated`` is the whole of
    that report. **No status is excluded, so no note is structurally invisible; the notes this
    reader can fail to show are the ones past the bound, and ``truncated`` is true exactly then.**

    ``threads_scanned`` counts **distinct** threads read, keyed on ``thread_id`` (in every ``select``
    here). Summing the walks would double-count each paused thread and make the number mean "reads",
    which answers nothing a caller asks. It still answers what it was added for -- "was the store
    read at all", separating an empty queue from an unread store -- and now says it about the whole
    store rather than the interrupted slice.
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

        Only one of the three exits is a short list: an empty page and a short page both mean the
        store is exhausted, while falling out of the ``while`` means :data:`_MAX_THREADS` stopped a
        walk the data had not. A store exhausted at *exactly* the bound also reports ``True`` --
        indistinguishable without another round trip, and over-reporting "you may not have
        everything" is the safe direction for this flag.
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

    paused_bound = await _walk(
        status="interrupted",
        select=["thread_id", "updated_at", "interrupts", "metadata"],
        extract=None,
        rows_of=_open_questions_of,
    )
    notes_bound = await _walk(
        status=None,
        select=["thread_id", "updated_at", "metadata"],
        extract=_RAISED_EXTRACT,
        rows_of=_open_raised_of,
    )
    truncated = paused_bound or notes_bound

    rows.sort(key=lambda row: str(row.get("asked_at") or ""))
    window = rows[offset : offset + limit]
    if offset + limit < len(rows):
        truncated = True
    return PendingPage(rows=window, truncated=truncated, threads_scanned=len(seen))


def _open_questions_of(thread: Any) -> list[dict[str, Any]]:
    """One row per open clarification on ``thread``, or ``[]``.

    ``asked_at`` is the thread's ``updated_at``. For an interrupted thread that is the moment it
    paused, because the in-memory runtime stamps it in the same update that writes ``interrupts``
    and ``status``, and nothing has run on the thread since. It is named ``asked_at`` so it
    matches the turn summaries' column, and this line is the note that it is a proxy rather than
    a field the engine stamped.
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
                    "source": "interrupt",
                    "basis": value.get("basis"),
                    # Null because an interrupt is not a report, not because it went missing --
                    # see :data:`PENDING_FIELDS` on why a declared column is never absent.
                    "report_id": None,
                    # ``interrupt_id`` and ``task_id`` are what a resume would have to name.
                    # Carried because withholding them would make this surface un-actionable the
                    # day the provenance gate lands, and they identify nothing a reader could not
                    # already see on their own thread.
                    "interrupt_id": item.get("id"),
                    "task_id": task_id,
                }
            )
    return out


def _open_raised_of(thread: Any) -> list[dict[str, Any]]:
    """Open ``raised`` rows on ``thread``, shaped for the pending queue.

    Not an interrupt: a finished refusal cannot be resumed, so these rows carry ``source``
    of ``from_refusal`` / ``wrong_answer`` and a ``report_id`` rather than a fake
    ``clarification_id``. ``thread`` arrives from an **unfiltered** walk, so it may be in any
    status; this function is the only narrowing, and it narrows on the row (``open``, ``kind``)
    rather than on the conversation's state.

    ``interrupt_id`` and ``task_id`` are absent rather than null, unlike the columns
    :data:`PENDING_FIELDS` declares: they name a resume, and there is nothing here to resume.
    """
    if not isinstance(thread, Mapping):
        return []
    thread_id = thread.get("thread_id")
    out: list[dict[str, Any]] = []
    for row in _channel_of(thread, "raised"):
        if not isinstance(row, Mapping) or row.get("open") is False:
            continue
        kind = str(row.get("kind") or "")
        if kind not in {"from_refusal", "wrong_answer"}:
            continue
        note = str(row.get("note") or "")
        question = note or (
            "A reader flagged this refusal." if kind == "from_refusal" else "A reader flagged this answer as wrong."
        )
        out.append(
            {
                "asked_at": row.get("reported_at") or thread.get("updated_at"),
                "question": question,
                "why": note or None,
                "clarification_id": None,
                "turn_id": row.get("turn_id"),
                "thread_id": thread_id,
                "source": kind,
                "basis": None,
                "report_id": row.get("report_id"),
            }
        )
    return out
