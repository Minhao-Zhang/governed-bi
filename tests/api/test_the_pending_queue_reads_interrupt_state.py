"""The queue has no store of its own: an unanswered question exists only as interrupt state.

``_FakeThreads`` honours ``status`` and ``select``. A fake that ignored either would let a broken query pass.
Ids use the real ``clar-{turn_id}-{digest}`` shape, because a made-up shape pins nothing.
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from typing import Any

import httpx
import pytest
from langgraph_sdk._shared.utilities import _registered_transports

from governed_bi.api.thread_turns import (
    PENDING_FIELDS,
    InProcessServerRequired,
    PendingClarifications,
    turn_of_clarification,
)

TURN_A = "a1b2c3d4e5f60718"
TURN_B = "0f1e2d3c4b5a6978"


def _clar(turn_id: str, digest: str = "0123456789ab") -> str:
    return f"clar-{turn_id}-{digest}"


def _interrupt(
    turn_id: str,
    question: str,
    why: str = "ambiguous",
    basis: str = "data_definition",
    digest: str = "0123456789ab",
) -> dict[str, Any]:
    return {
        "id": f"int-{turn_id}",
        "value": {
            "kind": "clarification",
            "clarification_id": _clar(turn_id, digest),
            "question": question,
            "why": why,
            "basis": basis,
        },
    }


def _thread(
    thread_id: str,
    updated_at: str,
    interrupts: dict[str, Any] | None,
    status: str = "interrupted",
) -> dict[str, Any]:
    # `turns` is present so a reader that forgot to project would be caught by the size of what it
    # got back rather than by a missing key. There is no second population: the queue is
    # interrupt state only.
    values: dict[str, Any] = {"turns": [{"question": "unrelated"}]}
    return {
        "thread_id": thread_id,
        "updated_at": updated_at,
        "status": status,
        "metadata": {"graph_id": "serve"},
        "interrupts": interrupts if interrupts is not None else {},
        "values": values,
    }


class _FakeThreads:
    """``status`` and ``select`` are enforced. Ignoring either would let a broken query pass."""

    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self._threads = threads
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        *,
        status: Any = None,
        limit: int = 10,
        offset: int = 0,
        sort_by: Any = None,
        sort_order: Any = None,
        select: Any = None,
        extract: Any = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "status": status,
                "limit": limit,
                "offset": offset,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "select": list(select) if select else None,
                "extract": dict(extract) if extract else None,
            }
        )
        rows = list(self._threads)
        if status is not None:
            rows = [t for t in rows if t.get("status") == status]
        if sort_by == "updated_at":
            rows.sort(key=lambda t: t["updated_at"], reverse=(sort_order == "desc"))
        out = []
        for thread in rows[offset : offset + limit]:
            projected = {k: v for k, v in thread.items() if select is None or k in select}
            if extract:
                current: dict[str, Any] = {}
                for alias, path in extract.items():
                    value: Any = thread
                    for part in str(path).split("."):
                        value = value.get(part) if isinstance(value, dict) else None
                    current[alias] = value
                projected["extracted"] = current
            out.append(projected)
        return out


class _FakeClient:
    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self.threads = _FakeThreads(threads)


def _queue(threads: list[dict[str, Any]]) -> tuple[PendingClarifications, _FakeClient]:
    client = _FakeClient(threads)
    return PendingClarifications(client_factory=lambda: client), client


def test_the_queue_asks_for_interrupted_threads_and_selects_the_interrupts() -> None:
    """Asserted on the call. A lenient fake would still return rows if ``status`` or ``select`` were dropped."""
    queue, client = _queue([_thread("t-1", "2026-08-19T10:00:00Z", {"task-1": [_interrupt(TURN_A, "which rating?")]})])
    queue.pending()

    assert client.threads.calls, "the reader never called the store"
    call = client.threads.calls[0]
    assert call["status"] == "interrupted", (
        "the paused-question walk must ask the store for interrupted threads; without it every idle "
        "conversation is reported as an open question"
    )
    assert "interrupts" in (call["select"] or []), (
        "`interrupts` must be selected -- it is a first-class ThreadSelectField, and without it "
        "every row comes back empty"
    )
    assert not call["extract"], (
        "the paused walk projects no channel. It used to matter because a second walk read "
        "`values.raised` off the same threads; that walk is gone with the channel, "
        "and what is left is that an unprojected thread carries the whole of `ServeState` -- "
        "2.42 MB for sixteen threads, measured -- so `select` is the whole read."
    )


def test_the_queue_is_oldest_first_because_it_is_a_work_queue() -> None:
    queue, client = _queue(
        [
            _thread("t-new", "2026-08-19T12:00:00Z", {"k": [_interrupt(TURN_B, "newer")]}),
            _thread("t-old", "2026-08-19T09:00:00Z", {"k": [_interrupt(TURN_A, "older")]}),
        ]
    )
    page = queue.pending()

    assert client.threads.calls[0]["sort_order"] == "asc"
    assert [row["question"] for row in page.rows] == ["older", "newer"]


def test_a_row_carries_the_question_and_links_back_to_its_turn() -> None:
    """The turn link falls out of the id ``serve/tools.py`` mints, so there is no separate field."""
    queue, _ = _queue(
        [
            _thread(
                "t-1",
                "2026-08-19T10:00:00Z",
                {"task-7": [_interrupt(TURN_A, "Which listing?", why="two tables carry a rating")]},
            )
        ]
    )
    (row,) = queue.pending().rows

    assert row["question"] == "Which listing?"
    assert row["why"] == "two tables carry a rating"
    assert row["clarification_id"] == _clar(TURN_A)
    assert row["turn_id"] == TURN_A, "the row does not link back to the turn that asked"
    assert row["thread_id"] == "t-1"
    assert row["asked_at"] == "2026-08-19T10:00:00Z"
    assert row["interrupt_id"] == f"int-{TURN_A}"
    assert row["task_id"] == "task-7"
    assert row["source"] == "interrupt"
    assert row["basis"] == "data_definition"
    assert set(PENDING_FIELDS) <= set(row), "a declared column is missing from the row"


def test_two_open_questions_on_one_thread_are_two_rows() -> None:
    """The window is counted in rows, not threads -- ``interrupts`` is a mapping of lists."""
    queue, _ = _queue(
        [
            _thread(
                "t-1",
                "2026-08-19T10:00:00Z",
                {"task-1": [_interrupt(TURN_A, "first")], "task-2": [_interrupt(TURN_B, "second")]},
            )
        ]
    )
    page = queue.pending()
    assert len(page.rows) == 2
    assert page.threads_scanned == 1


def test_an_idle_thread_is_not_in_the_queue() -> None:
    queue, _ = _queue(
        [
            _thread("t-idle", "2026-08-19T10:00:00Z", {"k": [_interrupt(TURN_A, "answered")]}, status="idle"),
            _thread("t-open", "2026-08-19T11:00:00Z", {"k": [_interrupt(TURN_B, "open")]}),
        ]
    )
    page = queue.pending()
    assert [row["question"] for row in page.rows] == ["open"]


def test_an_interrupt_of_another_kind_is_not_reported_as_a_question() -> None:
    """An unknown shape rendered as a clarification is a payload the client's literal cannot parse."""
    queue, _ = _queue(
        [
            _thread(
                "t-1",
                "2026-08-19T10:00:00Z",
                {"k": [{"id": "int-x", "value": {"kind": "approval", "question": "ship it?"}}]},
            )
        ]
    )
    assert queue.pending().rows == []


@pytest.mark.parametrize(
    "interrupts",
    [
        {},
        {"k": []},
        {"k": [{"id": "int-x"}]},  # no `value`
        {"k": [{"id": "int-x", "value": None}]},
        {"k": "not-a-list"},
        None,
    ],
    ids=["empty", "no-raised", "no-value", "null-value", "not-a-list", "absent"],
)
def test_a_malformed_or_empty_interrupt_map_yields_no_rows(interrupts: Any) -> None:
    """Shapes the store could hand back. None of them may raise, and none may invent a row."""
    queue, _ = _queue([_thread("t-1", "2026-08-19T10:00:00Z", interrupts)])
    assert queue.pending().rows == []


def test_a_short_page_says_it_is_short() -> None:
    """A truncated queue that does not say so reads as nobody waiting."""
    threads = [_thread(f"t-{i}", f"2026-08-19T{i:02d}:00:00Z", {"k": [_interrupt(TURN_A, f"q{i}")]}) for i in range(5)]
    queue, _ = _queue(threads)

    page = queue.pending(limit=2)
    assert len(page.rows) == 2
    assert page.truncated is True, "two of five rows were returned and the page did not say so"

    whole = queue.pending(limit=50)
    assert len(whole.rows) == 5
    assert whole.truncated is False


def test_offset_pages_through_without_claiming_truncation_at_the_end() -> None:
    threads = [_thread(f"t-{i}", f"2026-08-19T{i:02d}:00:00Z", {"k": [_interrupt(TURN_A, f"q{i}")]}) for i in range(3)]
    queue, _ = _queue(threads)

    assert [r["question"] for r in queue.pending(limit=2, offset=0).rows] == ["q0", "q1"]
    last = queue.pending(limit=2, offset=2)
    assert [r["question"] for r in last.rows] == ["q2"]
    assert last.truncated is False, "the final page is not truncated"


@pytest.mark.parametrize(
    ("clarification_id", "expected"),
    [
        (_clar(TURN_A), TURN_A),
        (f"clar-{TURN_A}-with-dashes", f"{TURN_A}-with"),  # partition on the LAST dash
        ("clar-onlyone", None),  # no digest segment
        ("clar--abc", None),  # empty turn_id
        ("something-else", None),  # not our prefix
        ("", None),
    ],
    ids=["real", "dashed-turn-id", "no-digest", "empty-turn", "foreign-prefix", "empty"],
)
def test_the_turn_id_is_parsed_or_refused_never_guessed(clarification_id: str, expected: str | None) -> None:
    """A mangled ``turn_id`` links nowhere, which is worse than an unlinked row."""
    assert turn_of_clarification(clarification_id) == expected


def test_a_foreign_clarification_id_leaves_the_row_unlinked_rather_than_dropping_it() -> None:
    """The question still matters even if its id came from somewhere we do not recognise."""
    queue, _ = _queue(
        [
            _thread(
                "t-1",
                "2026-08-19T10:00:00Z",
                {
                    "k": [
                        {
                            "id": "int-x",
                            "value": {
                                "kind": "clarification",
                                "clarification_id": "not-our-shape",
                                "question": "still a real question",
                                "why": "why",
                            },
                        }
                    ]
                },
            )
        ]
    )
    (row,) = queue.pending().rows
    assert row["question"] == "still a real question"
    assert row["turn_id"] is None
    assert row["clarification_id"] == "not-our-shape"


def _app_client(threads: list[dict[str, Any]]) -> Any:
    """A stub reader would let a route that dropped ``truncated`` still pass."""
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app

    queue, _ = _queue(threads)
    return TestClient(make_app(object(), queue))


def test_the_route_serves_the_queue_with_its_columns() -> None:
    client = _app_client([_thread("t-1", "2026-08-19T10:00:00Z", {"k": [_interrupt(TURN_A, "which rating?")]})])
    body = client.get("/clarifications/pending").json()

    assert body["meta"]["n"] == 1
    assert body["meta"]["columns"] == list(PENDING_FIELDS)
    assert body["meta"]["threads_scanned"] == 1
    (row,) = body["rows"]
    assert row["question"] == "which rating?"
    assert row["turn_id"] == TURN_A


def test_the_route_puts_truncation_on_the_wire() -> None:
    """Without the flag, one waiting and five waiting are the same response shape."""
    threads = [_thread(f"t-{i}", f"2026-08-19T{i:02d}:00:00Z", {"k": [_interrupt(TURN_A, f"q{i}")]}) for i in range(5)]
    client = _app_client(threads)

    short = client.get("/clarifications/pending?limit=2").json()
    assert short["meta"]["truncated"] is True
    assert short["meta"]["limit"] == 2
    assert len(short["rows"]) == 2

    whole = client.get("/clarifications/pending?limit=50").json()
    assert whole["meta"]["truncated"] is False
    assert len(whole["rows"]) == 5


def test_the_route_refuses_a_nonsense_window_rather_than_clamping_silently() -> None:
    """``Query(ge=1, le=500)`` answers 422. Clamping would page the store without bound."""
    client = _app_client([])
    assert client.get("/clarifications/pending?limit=0").status_code == 422
    assert client.get("/clarifications/pending?limit=9999").status_code == 422
    assert client.get("/clarifications/pending?offset=-1").status_code == 422


def test_a_ranking_interrupt_carries_its_basis() -> None:
    queue, _ = _queue(
        [
            _thread(
                "t-1",
                "2026-08-19T10:00:00Z",
                {"k": [_interrupt(TURN_A, "which ranking?", basis="ranking_ambiguity")]},
            )
        ]
    )
    (row,) = queue.pending().rows
    assert row["basis"] == "ranking_ambiguity"
    assert row["source"] == "interrupt"


def test_an_unanswered_definition_interrupt_stays_pending() -> None:
    """Definition cancel is UI-only: without a resume the interrupt remains on the queue."""
    queue, _ = _queue(
        [
            _thread(
                "t-1",
                "2026-08-19T10:00:00Z",
                {"k": [_interrupt(TURN_A, "which year?", basis="data_definition")]},
            )
        ]
    )
    (row,) = queue.pending().rows
    assert row["source"] == "interrupt"
    assert row["basis"] == "data_definition"


def test_the_route_returns_every_interrupt_once_when_they_exceed_one_page() -> None:
    """``_THREAD_PAGE`` is 50, so 51 threads force a second search. Each id comes back once."""
    n = 51
    threads = [
        _thread(
            f"t-{i}",
            f"2026-08-{(i // 24) + 1:02d}T{i % 24:02d}:00:00Z",
            {"k": [_interrupt(TURN_A, f"q{i:02d}", digest=f"{i:012x}")]},
        )
        for i in range(n)
    ]
    client = _app_client(threads)
    limit = 20
    got: list[str] = []
    offset = 0
    for _ in range(10):
        body = client.get("/clarifications/pending", params={"limit": limit, "offset": offset}).json()
        rows = body["rows"]
        got.extend(row["clarification_id"] for row in rows)
        offset += limit
        if len(rows) < limit:
            assert body["meta"]["truncated"] is False
            break
        assert body["meta"]["truncated"] is True
    else:
        raise AssertionError("paging did not finish")
    assert got == [f"clar-{TURN_A}-{i:012x}" for i in range(n)]
    assert len(got) == len(set(got))


def test_every_declared_column_is_present_on_an_interrupt_row() -> None:
    queue, _ = _queue([_thread("t-1", "2026-08-19T10:00:00Z", {"k": [_interrupt(TURN_A, "which?")]})])
    (row,) = queue.pending().rows
    assert set(PENDING_FIELDS) <= set(row), (
        f"absent: {sorted(set(PENDING_FIELDS) - set(row))}"
    )
    assert row["source"] == "interrupt"
    assert row["interrupt_id"] and row["task_id"], "a resume needs both, so both are carried"

def test_with_no_server_module_the_reader_refuses_by_name_and_registers_no_transport(
    monkeypatch: Any,
) -> None:
    """The exception alone would pass on the leaky version, so the transport list's length is asserted."""
    monkeypatch.setitem(sys.modules, "langgraph_api.server", None)
    before = len(_registered_transports)
    reader = PendingClarifications()

    with pytest.raises(InProcessServerRequired) as first:
        reader.pending(limit=5)
    assert "Agent server" in str(first.value)
    with pytest.raises(InProcessServerRequired):
        reader.pending(limit=5)

    assert len(_registered_transports) == before


def test_the_import_window_is_refused_too_rather_than_deferred(monkeypatch: Any) -> None:
    """``get_client`` registers a deferred transport on this flag ahead of any import attempt."""
    monkeypatch.setenv("__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT", "true")
    before = len(_registered_transports)
    with pytest.raises(InProcessServerRequired) as raised:
        PendingClarifications().pending(limit=5)
    assert "still importing" in str(raised.value)
    assert len(_registered_transports) == before


def test_an_imported_server_that_is_not_running_is_refused_as_well(monkeypatch: Any) -> None:
    """Only a started server sets ``_MAIN_LOOP``. The module is stubbed: a real import needs runtime configuration."""
    import langgraph_api.asyncio as server_loop

    stub = ModuleType("langgraph_api.server")
    stub.app = object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langgraph_api.server", stub)
    monkeypatch.setattr(server_loop, "_MAIN_LOOP", None)
    before = len(_registered_transports)
    with pytest.raises(InProcessServerRequired) as raised:
        PendingClarifications().pending(limit=5)
    assert "no server is running" in str(raised.value)
    assert len(_registered_transports) == before


def test_the_client_is_built_once_for_the_life_of_the_reader() -> None:
    """One ``get_client()`` per call would be one unclosed ``httpx.AsyncClient`` per call."""
    built: list[Any] = []
    client = _FakeClient([])

    def factory() -> Any:
        built.append(client)
        return client

    reader = PendingClarifications(client_factory=factory)
    reader.pending(limit=5)
    reader.pending(limit=5, offset=1)
    assert len(built) == 1


def test_a_cached_client_survives_the_next_fresh_event_loop() -> None:
    """Each call opens a new loop, so the client is used from a loop that did not exist when it was built.

    A fake would pass either way.
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
