"""``/clarifications/pending`` merges two stores, and the merge has to paginate as one list.

``test_the_pending_queue_reads_interrupt_state.py`` covers the interrupt half and says in a
comment that "``test_the_pending_queue_unions_two_stores.py`` is about the union itself". That
file did not exist. The union had no test at all, which is why this shipped:

    page = pending.pending(limit=limit, offset=offset)          # honoured the caller
    stored = store.queue(states=[open], limit=limit, offset=0)  # hard-coded zero
    rows = sorted(interrupt_rows + note_rows, key=asked_at)[:limit]

Paginating a union means paginating the *merged* order. Neither store can do it, because
neither knows the other's rows — so pushing ``offset`` into one of them is not a partial fix,
it is a different query. With interleaved timestamps, 60 rows in each half and ``limit=50``,
three pages served **150 rows for 120 real ones: 50 duplicated across pages, 45 unreachable,
and the order wrong** — while ``meta.offset`` echoed the requested value, so nothing on the
wire said so. An operator working the queue top to bottom would see the same notes again on
page two and never see the interrupts in the middle.

The fix reads ``offset + limit`` from **both** halves and slices after the merge, which is why
``offset`` is now bounded: the cost of a correct union slice is linear in its depth. See
``MAX_UNION_OFFSET``.
"""

from __future__ import annotations

import tempfile
from typing import Any

from fastapi.testclient import TestClient

from governed_bi.api.feedback_routes import MAX_UNION_OFFSET
from governed_bi.api.routes import make_app
from governed_bi.api.thread_turns import PendingPage
from governed_bi.feedback.events import Kind, Observation, ObservationState, Source
from governed_bi.feedback.store import FeedbackStore, mint_observation_id


def _minute(n: int) -> str:
    """An ISO timestamp ``n`` minutes after 10:00. The two halves interleave on these.

    Rolls into the hour rather than writing ``10:119:00Z``: the rows are compared as strings
    (the route sorts on ``asked_at`` text), and ``"10:119"`` sorts before ``"10:12"``, which
    made the first draft of these tests fail against correct code.
    """
    return f"2026-08-19T{10 + n // 60:02d}:{n % 60:02d}:00Z"


class _Queue:
    """The interrupt half, as a list the route pages. Honours ``limit`` and ``offset``.

    A fake that ignored ``offset`` would let the old code pass, since the old code's only use
    of ``offset`` was on this half.
    """

    PENDING_FIELDS: tuple[str, ...] = (
        "asked_at", "question", "why", "clarification_id", "turn_id", "thread_id", "source",
    )

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def pending(self, *, limit: int = 50, offset: int = 0) -> PendingPage:
        window = self._rows[offset : offset + limit]
        return PendingPage(
            rows=window,
            truncated=offset + limit < len(self._rows),
            threads_scanned=len(self._rows),
        )


class _TurnLog:
    TURN_LOG_DIR = "/nowhere"
    SUMMARY_FIELDS: tuple[str, ...] = ("turn_id",)

    def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[Any]:
        return []

    def get_turn(self, turn_id: str) -> None:
        return None

    def clarifications_of(self, thread_id: str, turn_id: str) -> list[Any]:
        return []


def _client(*, interrupts: int, notes: int) -> Any:
    """Both halves populated, on interleaved timestamps — the case the bug needed.

    Even minutes are interrupts, odd minutes are notes. With the halves separated in time the
    merged order is just one half then the other, and a broken merge looks correct.
    """
    queue = _Queue(
        [
            {
                "asked_at": _minute(n * 2),
                "question": f"interrupt q{n}",
                "why": "ambiguous",
                "clarification_id": f"clar-{n:04d}",
                "turn_id": None,
                "thread_id": f"t-{n}",
                "source": "interrupt",
            }
            for n in range(interrupts)
        ]
    )
    store = FeedbackStore(tempfile.mkdtemp(prefix="union-") + "/feedback.sqlite")
    for n in range(notes):
        store.file(
            Observation(
                observation_id=mint_observation_id(),
                filed_at=_minute(n * 2 + 1),
                source=Source.reader,
                kind=Kind.wrong_answer,
                state=ObservationState.open,
                question=f"note q{n}",
                note=f"note {n}",
                # A reader-filed note is about a turn; `validate` refuses one without it.
                turn_id=f"{n:016x}",
                thread_id=f"t-note-{n}",
            )
        )
    return TestClient(make_app(object(), _TurnLog(), queue, store))


def _page(client: Any, *, limit: int, offset: int) -> list[str]:
    body = client.get(f"/clarifications/pending?limit={limit}&offset={offset}").json()
    assert body["meta"]["offset"] == offset
    return [row["question"] for row in body["rows"]]


def test_paging_the_union_serves_every_row_once_and_in_order() -> None:
    """The headline. 120 rows, three pages of 50, no duplicate and no gap.

    Asserted against the true merged order rather than only against set equality: the old code
    also got the *count* right on page one, and a union that serves the right rows in the wrong
    order is still a work queue that does not work oldest-first.
    """
    client = _client(interrupts=60, notes=60)
    expected = sorted(
        [f"interrupt q{n}" for n in range(60)] + [f"note q{n}" for n in range(60)],
        key=lambda q: _minute(
            (int(q.split("q")[1]) * 2) if q.startswith("interrupt") else (int(q.split("q")[1]) * 2 + 1)
        ),
    )

    served = (
        _page(client, limit=50, offset=0)
        + _page(client, limit=50, offset=50)
        + _page(client, limit=50, offset=100)
    )

    assert len(served) == 120, f"served {len(served)} rows for 120 real ones"
    assert len(set(served)) == 120, sorted({q for q in served if served.count(q) > 1})[:5]
    assert served == expected, "the merged order is not oldest-first across pages"


def test_the_second_page_does_not_repeat_the_first() -> None:
    """The symptom an operator would actually hit, stated on its own.

    Kept separate from the whole-union test because this is the one a reader will recognise:
    two pages, same notes on both, and the interrupts between them gone.
    """
    client = _client(interrupts=60, notes=60)

    first = _page(client, limit=50, offset=0)
    second = _page(client, limit=50, offset=50)

    assert not set(first) & set(second), sorted(set(first) & set(second))[:5]


def test_one_empty_half_still_pages() -> None:
    """The degenerate cases, since the merge is now doing arithmetic on both halves.

    A union where one side is empty is the common case in a fresh deployment, and an
    off-by-one in the slice shows up here before it shows up on a live queue.
    """
    notes_only = _client(interrupts=0, notes=5)
    assert _page(notes_only, limit=2, offset=0) == ["note q0", "note q1"]
    assert _page(notes_only, limit=2, offset=2) == ["note q2", "note q3"]
    assert _page(notes_only, limit=2, offset=4) == ["note q4"]
    assert _page(notes_only, limit=2, offset=6) == []

    interrupts_only = _client(interrupts=3, notes=0)
    assert _page(interrupts_only, limit=2, offset=2) == ["interrupt q2"]


def test_the_offset_is_bounded_because_a_union_slice_costs_its_depth() -> None:
    """A correct merged slice reads ``offset + limit`` from both stores.

    So an unbounded ``offset`` is an unbounded read of two stores — the cost the old code
    avoided by being wrong. Refused rather than clamped, like ``limit`` beside it: a clamp
    would answer a different question than the one asked and say nothing about it.
    """
    client = _client(interrupts=1, notes=1)

    assert client.get(f"/clarifications/pending?offset={MAX_UNION_OFFSET}").status_code == 200
    assert client.get(f"/clarifications/pending?offset={MAX_UNION_OFFSET + 1}").status_code == 422
    assert client.get("/clarifications/pending?offset=-1").status_code == 422
