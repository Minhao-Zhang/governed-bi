"""The pending-clarification queue, against a fake client.

**What is being pinned, and why it needs a test at all.** The queue's whole claim is that it needs
no store of its own: a question nobody answered writes nothing to state (``serve/tools.py`` writes
``clarifications_by_call`` on the far side of ``interrupt()``), so the only record of it is the
platform's interrupt state. That makes the reader's correctness entirely a matter of *how it asks* —
``status="interrupted"`` and ``select=["...", "interrupts", ...]`` — and a fake that ignored either
one would let a broken query pass. So :class:`_FakeThreads` below honours both, and one test asserts
the query itself rather than only its result.

**The queue is two walks and they ask opposite questions**, so the query tests come in pairs. Open
``raised`` notes are filed on *finished* turns, which live under any thread status the platform has
— ``idle``, ``error`` after a crashed run, ``busy`` while a later question runs — so the note walk
sends **no** ``status`` at all, and a test that only ever fed the fake ``idle`` threads would not
notice a filter creeping back in. That is the decision
:func:`governed_bi.api.thread_turns._pending_async` records, and the tests under "coverage by
status" are what hold it.

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


def _interrupt(turn_id: str, question: str, why: str = "ambiguous", basis: str = "data_definition") -> dict[str, Any]:
    """One raised interrupt, in the shape ``_patch_interrupt`` yields: ``{"id", "value"}``."""
    return {
        "id": f"int-{turn_id}",
        "value": {
            "kind": "clarification",
            "clarification_id": _clar(turn_id),
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
    raised: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # `turns` is present so a reader that forgot to project would be caught by the size of what it
    # got back rather than by a missing key. `raised` is only set when a test asks for it, so a walk
    # that stopped extracting the channel fails on the note tests rather than on all of them.
    values: dict[str, Any] = {"turns": [{"question": "unrelated"}]}
    if raised is not None:
        values["raised"] = raised
    return {
        "thread_id": thread_id,
        "updated_at": updated_at,
        "status": status,
        "metadata": {"graph_id": "serve"},
        "interrupts": interrupts if interrupts is not None else {},
        "values": values,
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
        "the paused-question walk must ask the store for interrupted threads; without it every idle "
        "conversation is reported as an open question"
    )
    assert "interrupts" in (call["select"] or []), (
        "`interrupts` must be selected -- it is a first-class ThreadSelectField, and without it "
        "every row comes back empty"
    )
    assert not call["extract"], (
        "the paused walk must not also project `values.raised`: the note walk reads that channel "
        "off the same threads, so paying for it twice reports every note on a paused thread twice"
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


# ── the route ────────────────────────────────────────────────────────────────────────────────


def _app_client(threads: list[dict[str, Any]]) -> Any:
    """The real app over a real reader over a fake store.

    Not a fake reader: the point of these two tests is the wire envelope, and a stub reader would
    let a route that dropped `truncated` still pass.
    """
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app

    class _TurnLog:
        TURN_LOG_DIR = "/nowhere"
        SUMMARY_FIELDS: tuple[str, ...] = ("turn_id",)

        def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[Any]:
            return []

        def get_turn(self, turn_id: str) -> None:
            return None

        def clarifications_of(self, thread_id: str, turn_id: str) -> list[Any]:
            return []

    queue, _ = _queue(threads)
    return TestClient(make_app(object(), _TurnLog(), queue))


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
    """ADR 0009 D2/D9: the count a caller did not get travels beside the ones it did.

    Without this the queue reports "one person is waiting" when five are, and it reports it in
    exactly the same shape as the truthful answer.
    """
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
    """``limit`` is bounded by ``Query(ge=1, le=500)``, so FastAPI answers 422 rather than
    letting an unbounded page reach a reader that pages the store."""
    client = _app_client([])
    assert client.get("/clarifications/pending?limit=0").status_code == 422
    assert client.get("/clarifications/pending?limit=9999").status_code == 422
    assert client.get("/clarifications/pending?offset=-1").status_code == 422


def _note(
    kind: str = "from_refusal",
    *,
    turn_id: str = TURN_A,
    thread_id: str = "t-idle",
    note: str = "this refusal is wrong",
    open_: bool = True,
    report_id: str = "rpt-abc-0123456789ab",
) -> dict[str, Any]:
    """One ``raised`` row in the shape ``serve/raised.raised_row`` mints."""
    return {
        "kind": kind,
        "report_id": report_id,
        "turn_id": turn_id,
        "thread_id": thread_id,
        "reported_at": "2026-08-19T09:00:00Z",
        "note": note,
        "open": open_,
    }


def test_an_open_raised_row_on_an_idle_thread_joins_the_queue() -> None:
    """A finished refusal is not an interrupt; it still belongs on the pending list."""
    queue, client = _queue([_thread("t-idle", "2026-08-19T10:00:00Z", {}, status="idle", raised=[_note()])])
    page = queue.pending()
    assert any(call["status"] is None for call in client.threads.calls), (
        "the note walk must send no status filter -- see the coverage tests below"
    )
    (row,) = page.rows
    assert row["source"] == "from_refusal"
    assert row["basis"] is None
    assert row["turn_id"] == TURN_A
    assert row["clarification_id"] is None
    assert row["report_id"] == "rpt-abc-0123456789ab"
    assert "refusal" in (row["question"] or "").lower() or "wrong" in (row["question"] or "")


def test_a_closed_raised_row_is_not_pending() -> None:
    queue, _ = _queue(
        [
            _thread(
                "t-idle",
                "2026-08-19T10:00:00Z",
                {},
                status="idle",
                raised=[_note("wrong_answer", note="", open_=False)],
            )
        ]
    )
    assert queue.pending().rows == []


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


# ── coverage by status ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["idle", "error", "busy", "interrupted"])
def test_a_note_is_pending_whatever_status_its_thread_is_in(status: str) -> None:
    """The coverage decision, pinned per status rather than described.

    A note is filed on a *finished* turn, and the thread it hangs off is in whatever state its last
    run left: ``idle`` normally, ``error`` when that run crashed — the very turn a reader flags —
    ``busy`` while a later question on the same conversation runs, ``interrupted`` when that later
    question stopped to ask something. The walk that finds notes therefore filters on no status at
    all. ``error`` is the case that made this non-negotiable: nothing moves a thread out of it, so a
    filter naming only ``idle`` hid that note **permanently**.
    """
    queue, client = _queue([_thread("t-x", "2026-08-19T10:00:00Z", {}, status=status, raised=[_note(thread_id="t-x")])])
    page = queue.pending()

    assert [row["source"] for row in page.rows] == ["from_refusal"], (
        f"a note on a {status!r} thread never reached the queue"
    )
    note_walks = [c for c in client.threads.calls if c["status"] is None]
    assert note_walks, "no walk read the store unfiltered, so some status is now hidden"
    assert note_walks[0]["extract"] == {"raised": "values.raised"}, (
        "the unfiltered walk must project `values.raised`; without it the rows come back empty"
    )


def test_a_note_on_a_paused_thread_is_one_row_not_two() -> None:
    """The two walks overlap on interrupted threads, and only one of them reads ``raised``.

    Both the paused-question walk and the unfiltered note walk see this thread. If the first one
    also projected the channel, the note would be reported once per walk and the queue would claim
    two people are waiting where one is.
    """
    queue, _ = _queue(
        [
            _thread(
                "t-1",
                "2026-08-19T10:00:00Z",
                {"k": [_interrupt(TURN_A, "which rating?")]},
                status="interrupted",
                raised=[
                    _note(turn_id=TURN_B, thread_id="t-1"),
                ],
            )
        ]
    )
    page = queue.pending()
    assert sorted(row["source"] for row in page.rows) == ["from_refusal", "interrupt"]
    assert page.threads_scanned == 1, (
        "`threads_scanned` counts distinct threads read, not reads: both walks fetch this thread"
    )


def test_threads_scanned_is_distinct_threads_across_both_walks() -> None:
    """Two conversations, three thread fetches (the paused one is read by both walks), and the
    number a caller reads is two."""
    queue, client = _queue(
        [
            _thread("t-open", "2026-08-19T10:00:00Z", {"k": [_interrupt(TURN_A, "open")]}),
            _thread("t-idle", "2026-08-19T11:00:00Z", {}, status="idle", raised=[_note(thread_id="t-idle")]),
        ]
    )
    page = queue.pending()
    assert page.threads_scanned == 2
    assert len(client.threads.calls) >= 2, "one walk cannot cover both populations"


# ── the declared row ─────────────────────────────────────────────────────────────────────────


def test_report_id_is_declared_and_present_on_both_kinds_of_row() -> None:
    """A column the client renders from is in ``meta.columns``, and null rather than absent.

    ``pending-queue.tsx`` keys a note's card on ``report_id`` — a note has no ``clarification_id``
    to key on — so it is a declared column and not a carried one like ``interrupt_id``/``task_id``.
    Declared means present on **every** row: an interrupt is not a report, so it carries the key
    with ``None``, the way ``clarification_id`` and ``basis`` are null on a note.
    """
    assert "report_id" in PENDING_FIELDS
    queue, _ = _queue(
        [
            _thread("t-1", "2026-08-19T10:00:00Z", {"k": [_interrupt(TURN_A, "which rating?")]}),
            _thread("t-2", "2026-08-19T11:00:00Z", {}, status="idle", raised=[_note(thread_id="t-2")]),
        ]
    )
    rows = {row["source"]: row for row in queue.pending().rows}
    interrupt_row, note_row = rows["interrupt"], rows["from_refusal"]

    for row in (interrupt_row, note_row):
        assert set(PENDING_FIELDS) <= set(row), "a declared column is absent from a row"
    assert interrupt_row["report_id"] is None
    assert note_row["report_id"] == "rpt-abc-0123456789ab"
    assert note_row["clarification_id"] is None
    assert "interrupt_id" not in note_row, "there is nothing on a filed note to resume"


# ── the bound on how much store one request reads ────────────────────────────────────────────


def test_a_store_larger_than_the_walk_says_the_list_is_short() -> None:
    """``_MAX_THREADS`` can bite now that a walk reads every thread, and it is reported.

    It could not before: only interrupted threads were paged and that population is a handful.
    The note walk reads the whole store, so past the bound notes go missing — and the direction is
    the unhelpful one, because both walks sort ``updated_at`` **ascending**: the threads dropped are
    the most recently touched, so the newest note is the first one lost. That is exactly what is
    built here, and the only thing standing between an operator and a silently empty queue is
    ``truncated``.
    """
    threads = [
        _thread(f"t-{i:04d}", f"2026-08-19T10:{i // 60:02d}:{i % 60:02d}Z", {}, status="idle") for i in range(1000)
    ]
    threads.append(_thread("t-newest", "2026-08-19T23:00:00Z", {}, status="idle", raised=[_note(thread_id="t-newest")]))
    queue, _ = _queue(threads)

    page = queue.pending()
    assert page.rows == [], "the fixture's only note is on the thread the bound drops"
    assert page.truncated is True, (
        "the walk stopped on _MAX_THREADS with threads still arriving and the page did not say so"
    )
    assert page.threads_scanned == 1000
