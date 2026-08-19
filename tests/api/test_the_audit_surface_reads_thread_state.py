"""The audit list and trace, read out of thread state rather than the JSONL log.

``ThreadTurnLog`` replaced the deleted JSONL reader behind ``make_app``'s ``turn_log`` seam once
``ServeState.turns`` began accumulating a turn's record across ``PER_TURN_RESET``. The rules it
adds over the log's are all about *many threads*, and none of them are reachable by booting a
server and looking, so they are exercised here against a fake client.

The rule that would otherwise go wrong silently: the log was one time-ordered file, while thread
state is per conversation. Reading threads newest-updated-first and concatenating their turns
produces rows that look sorted and are not, because two conversations interleave in time.

Two more rules are here because they are *invisible* rather than merely unreachable, and both were
review findings. A ``get_turn`` that reads the whole store to find one id answers correctly, so
only a tripwire and a page count can tell it from one that stops. And a reader used outside the
Agent server used to leak an SDK module global per call before failing with a bare ``TypeError``,
which no assertion about the return value can see either.
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from typing import Any

import httpx
import pytest
from langgraph_sdk._shared.utilities import _registered_transports

from governed_bi.api.thread_turns import InProcessServerRequired, ThreadTurnLog


def _entry(turn_id: str, thread_id: str, asked_at: str, *, question: str = "q") -> dict[str, Any]:
    """One envelope in the shape ``record_node`` appends."""
    return {
        "asked_at": asked_at,
        "question": question,
        "answer_text": "a",
        "outcome": "answered",
        "record": {
            "turn_id": turn_id,
            "thread_id": thread_id,
            "outcome": "answered",
            "licensed": ["sales.customers"],
            "execution": {"attempts": [{"passed": True}, {"passed": False}]},
        },
    }


def _dig(thread: dict[str, Any], path: str) -> Any:
    current: Any = thread
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class _FakeThreads:
    """Enough of ``ThreadsClient`` to exercise the reader: ids, paging, sort, extract."""

    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self._threads = threads
        self.pages = 0

    async def search(
        self,
        *,
        ids: Any = None,
        limit: int = 10,
        offset: int = 0,
        sort_by: Any = None,
        sort_order: Any = None,
        select: Any = None,
        extract: Any = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        self.pages += 1
        rows = list(self._threads)
        if sort_by == "updated_at":
            rows.sort(key=lambda t: t["updated_at"], reverse=(sort_order == "desc"))
        if ids is not None:
            rows = [t for t in rows if t["thread_id"] in set(ids)]
        out = []
        for thread in rows[offset : offset + limit]:
            projected: dict[str, Any] = {
                "thread_id": thread["thread_id"],
                "updated_at": thread["updated_at"],
            }
            if extract:
                projected["extracted"] = {a: _dig(thread, p) for a, p in extract.items()}
            out.append(projected)
        return out


class _FakeClient:
    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self.threads = _FakeThreads(threads)


class _Tripwire(list):
    """A ``turns`` list that fails the test if anything reads it.

    ``_turns_of`` accepts it (``isinstance(x, list)`` holds for a subclass) and then copies it
    with ``list(...)``, which goes through ``__iter__`` for a subclass. Placed on a thread that
    sorts *after* the one holding the wanted turn, it is the only way to distinguish a scan that
    stops at the first match from one that materialises the store and returns the same answer.
    """

    def __iter__(self) -> Any:
        raise AssertionError(
            "a thread past the match was read: `get_turn` materialised more of the store than "
            "the one envelope it was looking for"
        )


def _log(threads: list[dict[str, Any]]) -> tuple[ThreadTurnLog, _FakeClient]:
    client = _FakeClient(threads)
    return ThreadTurnLog(client_factory=lambda: client), client


#: Two conversations whose turns interleave in time. ``t-b`` was updated last, so a
#: thread-ordered read puts its 10:00 turn above ``t-a``'s 11:00 one.
_INTERLEAVED: list[dict[str, Any]] = [
    {
        "thread_id": "t-a",
        "updated_at": "2026-08-18T11:00:00",
        "values": {
            "turns": [
                _entry("a1", "t-a", "2026-08-18T09:00:00"),
                _entry("a2", "t-a", "2026-08-18T11:00:00"),
            ]
        },
    },
    {
        "thread_id": "t-b",
        "updated_at": "2026-08-18T12:00:00",
        "values": {"turns": [_entry("b1", "t-b", "2026-08-18T10:00:00")]},
    },
]


def test_rows_are_newest_first_across_threads_not_merely_within_one() -> None:
    log, _ = _log(_INTERLEAVED)
    rows = log.list_turns(limit=50)
    assert [r["turn_id"] for r in rows] == ["a2", "b1", "a1"], (
        "rows came back grouped by thread rather than ordered by when each turn was asked: "
        "`t-b` is the most recently updated thread, so concatenating threads puts its 10:00 "
        "turn above `t-a`'s 11:00 turn while the list still reads as though it were sorted"
    )


def test_one_conversation_can_be_asked_for_without_leaking_another() -> None:
    log, _ = _log(_INTERLEAVED)
    rows = log.list_turns(limit=50, thread_id="t-a")
    assert [r["turn_id"] for r in rows] == ["a2", "a1"]
    assert {r["thread_id"] for r in rows} == {"t-a"}


def test_a_thread_carrying_another_threads_row_does_not_leak_it() -> None:
    """The belt in ``_collect_async``: a migrated or hand-edited thread is how this happens, and
    an audit view showing one conversation inside another is worse than an empty one."""
    poisoned = [
        {
            "thread_id": "t-a",
            "updated_at": "2026-08-18T11:00:00",
            "values": {
                "turns": [
                    _entry("a1", "t-a", "2026-08-18T09:00:00"),
                    _entry("x1", "t-other", "2026-08-18T10:00:00"),
                ]
            },
        }
    ]
    log, _ = _log(poisoned)
    rows = log.list_turns(limit=50, thread_id="t-a")
    assert [r["turn_id"] for r in rows] == ["a1"]


def test_the_limit_counts_turns_and_pages_threads_until_it_is_met() -> None:
    """``threads.search``'s limit counts threads; the caller's counts turns. At one turn per
    thread a budget of 80 needs more than one page, and stopping at the first page would be
    indistinguishable from the end of the list."""
    threads = [
        {
            "thread_id": f"t-{i:03d}",
            "updated_at": f"2026-08-18T{i % 24:02d}:00:00",
            "values": {
                "turns": [
                    _entry(
                        f"turn-{i:03d}",
                        f"t-{i:03d}",
                        f"2026-08-{(i % 28) + 1:02d}T09:00:00",
                    )
                ]
            },
        }
        for i in range(120)
    ]
    log, client = _log(threads)
    rows = log.list_turns(limit=80)
    assert len(rows) == 80
    assert client.threads.pages > 1, "one page cannot hold 80 turns at one turn per thread"


def test_a_turn_is_found_in_full_by_its_id_and_a_missing_one_says_so() -> None:
    log, _ = _log(_INTERLEAVED)
    found = log.get_turn("b1")
    assert found is not None
    assert found["record"]["thread_id"] == "t-b"
    assert set(found) == {"asked_at", "question", "answer_text", "outcome", "record"}, (
        "the envelope must stay the five keys `record_node` builds, or the trace route "
        "route projects a different shape than the log did"
    )
    assert log.get_turn("nope") is None


def test_the_derived_columns_come_from_the_projection_the_log_used() -> None:
    """One projection, in one place: ``attempts_passed`` disagreeing with the ledger beside it is
    the defect class ADR 0009 D11 deleted a route over."""
    log, _ = _log(_INTERLEAVED)
    row = next(r for r in log.list_turns(limit=50) if r["turn_id"] == "a1")
    assert row["attempts"] == 2
    assert row["attempts_passed"] == 1
    assert row["licensed_count"] == 1
    assert row["question"] == "q"
    assert row["outcome"] == "answered"


def test_a_thread_with_no_turns_channel_yields_nothing_rather_than_raising() -> None:
    """An older thread, or one whose only turn paused for a clarification, carries no ``turns``."""
    log, _ = _log([{"thread_id": "t-empty", "updated_at": "2026-08-18T11:00:00", "values": {}}])
    assert log.list_turns(limit=50) == []


# ── what one `get_turn` is allowed to read ───────────────────────────────────────────────────


def test_get_turn_stops_at_the_first_match_rather_than_reading_past_it() -> None:
    """The match is in the newest thread, and the next thread is a tripwire.

    Both a stopping and a collecting reader return the same envelope, so the assertion that
    separates them cannot be about the return value.
    """
    threads = [
        {
            "thread_id": "t-hit",
            "updated_at": "2026-08-18T12:00:00",
            "values": {"turns": [_entry("target", "t-hit", "2026-08-18T12:00:00")]},
        },
        {
            "thread_id": "t-cold",
            "updated_at": "2026-08-18T11:00:00",
            "values": {"turns": _Tripwire([_entry("c1", "t-cold", "2026-08-18T11:00:00")])},
        },
    ]
    log, _ = _log(threads)
    found = log.get_turn("target")
    assert found is not None
    assert found["record"]["turn_id"] == "target"


def test_get_turn_pages_on_demand_so_a_hit_costs_one_round_trip_and_a_miss_costs_the_scan() -> None:
    """``get_turn`` used to ask ``_entries`` for an unbounded read: no turn budget meant no break,
    so it paged to ``_MAX_THREADS`` and built one list of every turn envelope of every thread
    before looking at the first id. A hit on the newest thread is one page; only a miss is a scan.
    """
    threads = [
        {
            "thread_id": f"t-{i:03d}",
            "updated_at": f"2026-08-18T{i // 60:02d}:{i % 60:02d}:00",
            "values": {
                "turns": [_entry("target" if i == 119 else f"turn-{i:03d}", f"t-{i:03d}",
                                 "2026-08-18T09:00:00")]
            },
        }
        for i in range(120)
    ]
    log, client = _log(threads)
    assert log.get_turn("target") is not None
    assert client.threads.pages == 1, (
        "the newest thread held the turn, so no page after the first one was needed"
    )

    client.threads.pages = 0
    assert log.get_turn("no-such-turn") is None
    assert client.threads.pages == 3, "a miss has to exhaust the store: 120 threads at 50 a page"


# ── used outside the Agent server ────────────────────────────────────────────────────────────


def test_with_no_server_module_the_reader_refuses_by_name_and_registers_no_transport(
    monkeypatch: Any,
) -> None:
    """The unnamed failure this replaces, and the module global it grew.

    ``get_client(url=None)`` swallows the failed ``from langgraph_api.server import app``, builds
    ``ASGITransport(app=None)`` and appends it to ``langgraph_sdk``'s ``_registered_transports``,
    which only ``configure_loopback_transports(app)`` ever drains — at server startup, so never
    here. Measured before the fix: two calls, two ``TypeError: 'NoneType' object is not callable``,
    two entries in that list. Asserting the exception alone would pass on the leaky version, so the
    length of that list is asserted too.

    ``sys.modules[...] = None`` rather than trusting the ambient process, because whether that
    import succeeds is **test-order dependent**: it fails in a bare interpreter (its config wants
    ``REDIS_URI``) and succeeds in a session where another test has configured the runtime. A
    ``None`` entry is the import system's own way of saying a module is unavailable, and it raises
    at the same line ``get_client`` would.
    """
    monkeypatch.setitem(sys.modules, "langgraph_api.server", None)
    before = len(_registered_transports)
    log = ThreadTurnLog()

    with pytest.raises(InProcessServerRequired) as first:
        log.list_turns(limit=5)
    assert "Agent server" in str(first.value)

    with pytest.raises(InProcessServerRequired):
        log.get_turn("anything")

    assert len(_registered_transports) == before, (
        "an in-process transport was registered on a call that could not use it; the list is "
        "drained only at server startup, so each one is permanent"
    )


def test_the_import_window_is_refused_too_rather_than_deferred(monkeypatch: Any) -> None:
    """``get_client`` honours ``__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT`` *ahead of* any import
    attempt and registers an ``app=None`` transport unconditionally, so the leak is on that branch
    as well. That flag is set only while ``load_custom_app`` imports this application, when no
    request can be in flight, so refusing costs nothing.
    """
    monkeypatch.setenv("__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT", "true")
    before = len(_registered_transports)
    with pytest.raises(InProcessServerRequired) as raised:
        ThreadTurnLog().list_turns(limit=5)
    assert "still importing" in str(raised.value)
    assert len(_registered_transports) == before


def test_an_imported_server_that_is_not_running_is_refused_as_well(monkeypatch: Any) -> None:
    """Importable is not running, and a test process is where the two come apart.

    Anything that has configured the runtime can import ``langgraph_api.server``; only a started
    server sets ``langgraph_api.asyncio._MAIN_LOOP``, which is the loop the in-process transport
    hands the app coroutine to. Without that check the reader would get a client and then fail with
    ``RuntimeError: No event loop set`` — unnamed at request time, which is the whole defect.

    The server module is stubbed rather than imported, for the same order-independence reason as
    the test above and one more: importing it for real would require the runtime configuration
    (``REDIS_URI``) that makes it unimportable in a bare interpreter.
    """
    import langgraph_api.asyncio as server_loop

    stub = ModuleType("langgraph_api.server")
    stub.app = object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langgraph_api.server", stub)
    monkeypatch.setattr(server_loop, "_MAIN_LOOP", None)
    before = len(_registered_transports)
    with pytest.raises(InProcessServerRequired) as raised:
        ThreadTurnLog().list_turns(limit=5)
    assert "no server is running" in str(raised.value)
    assert len(_registered_transports) == before


def test_the_client_is_built_once_for_the_life_of_the_reader() -> None:
    """One ``get_client()`` per call also meant one ``httpx.AsyncClient`` per call, and nothing
    ever ``aclose()``s one."""
    built: list[Any] = []
    client = _FakeClient(_INTERLEAVED)

    def factory() -> Any:
        built.append(client)
        return client

    log = ThreadTurnLog(client_factory=factory)
    log.list_turns(limit=50)
    log.get_turn("b1")
    log.list_turns(limit=50, thread_id="t-a")
    assert len(built) == 1


def test_a_cached_client_survives_the_next_fresh_event_loop() -> None:
    """The reason caching one client is safe at all, measured rather than assumed.

    ``_blocking`` opens a new ``asyncio.run`` per call, so a cached client is used from a loop
    that did not exist when it was built. Nothing in the production path binds at construction —
    an ASGI transport has no connection pool — and this pins that for the real objects, since a
    fake client would pass whether or not it were true.
    """

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"[]"})

    client = httpx.AsyncClient(
        base_url="http://api",
        transport=httpx.ASGITransport(app, root_path="/noauth"),
    )
    try:
        first = asyncio.run(client.post("/threads/search", json={}))
        second = asyncio.run(client.post("/threads/search", json={}))
    finally:
        asyncio.run(client.aclose())
    assert (first.status_code, second.status_code) == (200, 200)
