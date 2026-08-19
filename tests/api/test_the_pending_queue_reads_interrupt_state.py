"""The pending-clarification queue, against a fake client.

**What is being pinned, and why it needs a test at all.** The queue's whole claim is that it needs
no store of its own: a question nobody answered writes nothing to state (``serve/tools.py`` writes
``clarifications_by_call`` on the far side of ``interrupt()``), so the only record of it is the
platform's interrupt state. That makes the reader's correctness entirely a matter of *how it asks* —
``status="interrupted"`` and ``select=["...", "interrupts", ...]`` — and a fake that ignored either
one would let a broken query pass. So :class:`_FakeThreads` below honours both, and one test asserts
the query itself rather than only its result.

The fixtures use the real id shape (``clar-{turn_id}-{digest}``, both hex) because
``turn_of_clarification`` is the join back to the turn and a test on a made-up shape would pin
nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.api.thread_turns import (
    PENDING_FIELDS,
    PendingClarifications,
    turn_of_clarification,
)

TURN_A = "a1b2c3d4e5f60718"
TURN_B = "0f1e2d3c4b5a6978"


def _clar(turn_id: str, digest: str = "0123456789ab") -> str:
    return f"clar-{turn_id}-{digest}"


def _interrupt(turn_id: str, question: str, why: str = "ambiguous") -> dict[str, Any]:
    """One raised interrupt, in the shape ``_patch_interrupt`` yields: ``{"id", "value"}``."""
    return {
        "id": f"int-{turn_id}",
        "value": {
            "kind": "clarification",
            "clarification_id": _clar(turn_id),
            "question": question,
            "why": why,
        },
    }


def _thread(
    thread_id: str, updated_at: str, interrupts: dict[str, Any] | None, status: str = "interrupted"
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "updated_at": updated_at,
        "status": status,
        "metadata": {"graph_id": "serve"},
        "interrupts": interrupts if interrupts is not None else {},
        # Present so a reader that forgot to project would be caught by the size of what it got
        # back rather than by a missing key.
        "values": {"turns": [{"question": "unrelated"}]},
    }


class _FakeThreads:
    """Enough of ``ThreadsClient.search`` to hold the reader to its query.

    ``status`` and ``select`` are *enforced*, not accepted and ignored: they are the two things the
    queue's no-new-storage claim rests on.
    """

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
            }
        )
        rows = list(self._threads)
        if status is not None:
            rows = [t for t in rows if t.get("status") == status]
        if sort_by == "updated_at":
            rows.sort(key=lambda t: t["updated_at"], reverse=(sort_order == "desc"))
        out = []
        for thread in rows[offset : offset + limit]:
            out.append({k: v for k, v in thread.items() if select is None or k in select})
        return out


class _FakeClient:
    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self.threads = _FakeThreads(threads)


def _queue(threads: list[dict[str, Any]]) -> tuple[PendingClarifications, _FakeClient]:
    client = _FakeClient(threads)
    return PendingClarifications(client_factory=lambda: client), client


# ── the query itself ─────────────────────────────────────────────────────────────────────────


def test_the_queue_asks_for_interrupted_threads_and_selects_the_interrupts() -> None:
    """The two load-bearing arguments, asserted on the call rather than inferred from the result.

    Dropping ``status`` would page the whole store and call every idle thread pending; dropping
    ``interrupts`` from ``select`` would return rows with nothing in them. Both would still
    "work" against a lenient fake, which is why this asserts the request.
    """
    queue, client = _queue([_thread("t-1", "2026-08-19T10:00:00Z", {"task-1": [_interrupt(TURN_A, "which rating?")]})])
    queue.pending()

    assert client.threads.calls, "the reader never called the store"
    call = client.threads.calls[0]
    assert call["status"] == "interrupted", (
        "the queue must ask the store for interrupted threads; without it every idle "
        "conversation is reported as an open question"
    )
    assert "interrupts" in (call["select"] or []), (
        "`interrupts` must be selected -- it is a first-class ThreadSelectField, and without it "
        "every row comes back empty"
    )


def test_the_queue_is_oldest_first_because_it_is_a_work_queue() -> None:
    """Ascending, unlike ``list_turns``. The row that waited longest is the one to answer."""
    queue, client = _queue(
        [
            _thread("t-new", "2026-08-19T12:00:00Z", {"k": [_interrupt(TURN_B, "newer")]}),
            _thread("t-old", "2026-08-19T09:00:00Z", {"k": [_interrupt(TURN_A, "older")]}),
        ]
    )
    page = queue.pending()

    assert client.threads.calls[0]["sort_order"] == "asc"
    assert [row["question"] for row in page.rows] == ["older", "newer"]


# ── what a row carries ───────────────────────────────────────────────────────────────────────


def test_a_row_carries_the_question_and_links_back_to_its_turn() -> None:
    """``clarification_id`` embeds ``turn_id``, so the link needs no separate field.

    The fork that first built this surface had to add the turn link in a later commit; here it
    falls out of the id ``serve/tools.py`` already mints.
    """
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


# ── what it refuses to report ────────────────────────────────────────────────────────────────


def test_an_idle_thread_is_not_in_the_queue() -> None:
    """Belt for the ``status`` filter: an answered conversation is not an open question."""
    queue, _ = _queue(
        [
            _thread("t-idle", "2026-08-19T10:00:00Z", {"k": [_interrupt(TURN_A, "answered")]}, status="idle"),
            _thread("t-open", "2026-08-19T11:00:00Z", {"k": [_interrupt(TURN_B, "open")]}),
        ]
    )
    page = queue.pending()
    assert [row["question"] for row in page.rows] == ["open"]


def test_an_interrupt_of_another_kind_is_not_reported_as_a_question() -> None:
    """Nothing else calls ``interrupt()`` today; if something does, it is not a pending question.

    Rendering an unknown shape as a clarification would put a payload the client's
    ``z.literal("clarification")`` cannot parse into an operator's queue.
    """
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


# ── the truncation contract ──────────────────────────────────────────────────────────────────


def test_a_short_page_says_it_is_short() -> None:
    """ADR 0009 D2/D9. A silently truncated queue reads as "nobody is waiting"."""
    threads = [
        _thread(f"t-{i}", f"2026-08-19T{i:02d}:00:00Z", {"k": [_interrupt(TURN_A, f"q{i}")]})
        for i in range(5)
    ]
    queue, _ = _queue(threads)

    page = queue.pending(limit=2)
    assert len(page.rows) == 2
    assert page.truncated is True, "two of five rows were returned and the page did not say so"

    whole = queue.pending(limit=50)
    assert len(whole.rows) == 5
    assert whole.truncated is False


def test_offset_pages_through_without_claiming_truncation_at_the_end() -> None:
    threads = [
        _thread(f"t-{i}", f"2026-08-19T{i:02d}:00:00Z", {"k": [_interrupt(TURN_A, f"q{i}")]})
        for i in range(3)
    ]
    queue, _ = _queue(threads)

    assert [r["question"] for r in queue.pending(limit=2, offset=0).rows] == ["q0", "q1"]
    last = queue.pending(limit=2, offset=2)
    assert [r["question"] for r in last.rows] == ["q2"]
    assert last.truncated is False, "the final page is not truncated"


# ── the id parser ────────────────────────────────────────────────────────────────────────────


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
def test_the_turn_id_is_parsed_or_refused_never_guessed(
    clarification_id: str, expected: str | None
) -> None:
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


# ── the route ────────────────────────────────────────────────────────────────────────────────


def _app_client(threads: list[dict[str, Any]]) -> Any:
    """The real app over a real reader over a fake store.

    Not a fake reader: the point of these two tests is the wire envelope, and a stub reader would
    let a route that dropped `truncated` still pass.
    """
    from starlette.testclient import TestClient

    from governed_bi.api.routes import make_app

    class _TurnLog:
        TURN_LOG_DIR = "/nowhere"
        SUMMARY_FIELDS: tuple[str, ...] = ("turn_id",)

        def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[Any]:
            return []

        def get_turn(self, turn_id: str) -> None:
            return None

    queue, _ = _queue(threads)
    return TestClient(make_app(object(), _TurnLog(), queue))


def test_the_route_serves_the_queue_with_its_columns() -> None:
    client = _app_client(
        [_thread("t-1", "2026-08-19T10:00:00Z", {"k": [_interrupt(TURN_A, "which rating?")]})]
    )
    body = client.get("/clarifications/pending").json()

    assert body["meta"]["n"] == 1
    assert body["meta"]["columns"] == list(PENDING_FIELDS)
    assert body["meta"]["threads_scanned"] == 1
    (row,) = body["rows"]
    assert row["question"] == "which rating?"
    assert row["turn_id"] == TURN_A


def test_the_route_puts_truncation_on_the_wire() -> None:
    """ADR 0009 D2/D9: the count a caller did not get travels beside the ones it did.

    Without this the queue reports "one person is waiting" when five are, and it reports it in
    exactly the same shape as the truthful answer.
    """
    threads = [
        _thread(f"t-{i}", f"2026-08-19T{i:02d}:00:00Z", {"k": [_interrupt(TURN_A, f"q{i}")]})
        for i in range(5)
    ]
    client = _app_client(threads)

    short = client.get("/clarifications/pending?limit=2").json()
    assert short["meta"]["truncated"] is True
    assert short["meta"]["limit"] == 2
    assert len(short["rows"]) == 2

    whole = client.get("/clarifications/pending?limit=50").json()
    assert whole["meta"]["truncated"] is False
    assert len(whole["rows"]) == 5


def test_the_route_refuses_a_nonsense_window_rather_than_clamping_silently() -> None:
    """``limit`` is bounded by ``Query(ge=1, le=500)``, so FastAPI answers 422 rather than
    letting an unbounded page reach a reader that pages the store."""
    client = _app_client([])
    assert client.get("/clarifications/pending?limit=0").status_code == 422
    assert client.get("/clarifications/pending?limit=9999").status_code == 422
    assert client.get("/clarifications/pending?offset=-1").status_code == 422
