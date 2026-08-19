"""The answered half of the clarification pair, and where it reaches a reader.

``PendingClarifications`` reads the questions still sitting in interrupt state — the ones nobody
answered. This is the other half: the ones somebody *did* answer, which ``serve/tools.py`` writes
into the ``clarifications`` channel on the far side of ``interrupt()``.

**Why it needs a reader at all.** Until 2026-08-19 that channel had two writers and no reader
outside ``state.py``, so a turn whose SQL was chosen *because* of a reader's answer showed no trace
of having asked: ``/audit/turns/{id}/trace`` carried the statement and not the sentence that
selected it. Observed end to end on a live turn — "Who are our top performers?" resumed after a
process restart with "employees by revenue, top 5", and its trace mentioned no clarification at all.
``tests/conformance/test_register_closure.py`` had the channel pinned in ``KNOWN_UNCONSUMED``, which
is what made closing it a deliberate act rather than a silent one.

**Two properties that are not about the result.** The reader must ask for *one* thread by id rather
than scanning, because the caller is already holding the record that names it; and it must leave the
envelope alone, because ``get_turn`` returning the five keys ``record_node`` stored is what keeps
the trace faithful to the store. A join that answered correctly and scanned the world, or that
answered correctly by mutating the envelope, would pass a result-only test.
"""

from __future__ import annotations

from typing import Any

from governed_bi.api.routes import trace_for
from governed_bi.api.thread_turns import ThreadTurnLog

TURN_ASKED = "a1b2c3d4e5f60718"
TURN_QUIET = "0f1e2d3c4b5a6978"


def _dig(thread: dict[str, Any], path: str) -> Any:
    current: Any = thread
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class _FakeThreads:
    """Enough of ``ThreadsClient.search`` to hold the reader to *how* it asks.

    ``ids`` and ``extract`` are honoured rather than accepted and ignored: a reader that paged the
    whole store, or that asked for ``values`` wholesale, would otherwise pass.
    """

    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self._threads = threads
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "ids": list(ids) if ids else None,
                "limit": limit,
                "select": list(select) if select else None,
                "extract": dict(extract) if extract else None,
            }
        )
        rows = list(self._threads)
        if ids is not None:
            rows = [t for t in rows if t["thread_id"] in set(ids)]
        out = []
        for thread in rows[offset : offset + limit]:
            projected: dict[str, Any] = {"thread_id": thread["thread_id"]}
            if extract:
                projected["extracted"] = {a: _dig(thread, p) for a, p in extract.items()}
            out.append(projected)
        return out


class _FakeClient:
    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self.threads = _FakeThreads(threads)


def _entry(turn_id: str, thread_id: str) -> dict[str, Any]:
    """One envelope in the shape ``api/graph_app.py``'s record node appends."""
    return {
        "asked_at": "2026-08-19T09:41:30",
        "question": "Who are our top performers?",
        "answer_text": "Employee 05, Employee 12, …",
        "outcome": "answered",
        "record": {
            "turn_id": turn_id,
            "thread_id": thread_id,
            "outcome": "answered",
            "execution": {"attempts": [{"passed": True}]},
        },
    }


def _clarification(turn_id: str, answer: str, *, with_turn_id: bool = True) -> dict[str, Any]:
    """One answered clarification, in ``serve/tools.py``'s shape.

    The id keeps the real ``clar-{turn_id}-{digest}`` shape because it is the fallback join when
    ``turn_id`` is absent, and a made-up shape would pin nothing.
    """
    row = {
        "clarification_id": f"clar-{turn_id}-0123456789ab",
        "question": 'By "top performers" do you mean employees or customers?',
        "why": "The schema supports multiple interpretations.",
        "answer": answer,
    }
    if with_turn_id:
        row["turn_id"] = turn_id
    return row


#: One conversation: two turns, one of which asked its reader something.
_THREAD = [
    {
        "thread_id": "t-1",
        "updated_at": "2026-08-19T09:42:00",
        "values": {
            "turns": [_entry(TURN_QUIET, "t-1"), _entry(TURN_ASKED, "t-1")],
            "clarifications": [
                _clarification(TURN_ASKED, "Employees ranked by total sales revenue, top 5.")
            ],
        },
    }
]


def _log(threads: list[dict[str, Any]]) -> tuple[ThreadTurnLog, _FakeClient]:
    client = _FakeClient(threads)
    return ThreadTurnLog(client_factory=lambda: client), client


def test_the_answered_clarification_is_found_for_the_turn_that_asked() -> None:
    log, _ = _log(_THREAD)

    rows = log.clarifications_of("t-1", TURN_ASKED)

    assert [r["answer"] for r in rows] == ["Employees ranked by total sales revenue, top 5."]
    assert rows[0]["question"].startswith('By "top performers"')


def test_a_turn_that_asked_nothing_reports_an_empty_list_and_not_the_thread_s_other_turn() -> None:
    """``[]`` is an answer, and the grouping is per turn.

    One thread holds every turn of its conversation, so a reader that returned the *thread's*
    clarifications would attribute one turn's question to the turn beside it — which is worse than
    reporting none, because it is wrong rather than absent.
    """
    log, _ = _log(_THREAD)

    assert log.clarifications_of("t-1", TURN_QUIET) == []


def test_the_reader_asks_for_one_thread_by_id_and_only_the_clarifications_channel() -> None:
    """One keyed round trip, one path.

    The caller holds the record, which carries ``thread_id``, so nothing here needs a scan. And the
    projection is the clarifications channel alone: ``select=["values"]`` would carry the whole of
    ``ServeState`` — measured at 2.42 MB for sixteen threads, because ``values`` holds the delivered
    context.
    """
    log, client = _log(_THREAD)

    log.clarifications_of("t-1", TURN_ASKED)

    assert len(client.threads.calls) == 1, "one thread by id should not cost more than one call"
    call = client.threads.calls[0]
    assert call["ids"] == ["t-1"]
    assert call["limit"] == 1
    assert call["extract"] == {"clarifications": "values.clarifications"}
    assert call["select"] is not None and "values" not in call["select"]


def test_a_row_written_without_a_turn_id_still_joins_through_its_clarification_id() -> None:
    """``clar-{turn_id}-{digest}`` has always carried the turn, so the join has a fallback.

    ``ask_user`` puts ``turn_id`` on the row today. A row from before it did is still addressable,
    and dropping it would make a turn look as though it had asked nothing.
    """
    threads = [
        {
            "thread_id": "t-1",
            "updated_at": "2026-08-19T09:42:00",
            "values": {
                "turns": [_entry(TURN_ASKED, "t-1")],
                "clarifications": [_clarification(TURN_ASKED, "top 5", with_turn_id=False)],
            },
        }
    ]
    log, _ = _log(threads)

    assert [r["answer"] for r in log.clarifications_of("t-1", TURN_ASKED)] == ["top 5"]


def test_one_turn_may_have_asked_more_than_once() -> None:
    """``ask_user`` is a tool the agent loop can call repeatedly, keyed per call."""
    threads = [
        {
            "thread_id": "t-1",
            "updated_at": "2026-08-19T09:42:00",
            "values": {
                "turns": [_entry(TURN_ASKED, "t-1")],
                "clarifications": [
                    _clarification(TURN_ASKED, "employees"),
                    {**_clarification(TURN_ASKED, "top 5"), "clarification_id": "clar-x-2"},
                ],
            },
        }
    ]
    log, _ = _log(threads)

    assert [r["answer"] for r in log.clarifications_of("t-1", TURN_ASKED)] == ["employees", "top 5"]


def test_a_thread_with_no_clarifications_channel_at_all_is_not_an_error() -> None:
    """Every turn served before this channel existed, and every turn that never asked."""
    threads = [
        {
            "thread_id": "t-1",
            "updated_at": "2026-08-19T09:42:00",
            "values": {"turns": [_entry(TURN_ASKED, "t-1")]},
        }
    ]
    log, _ = _log(threads)

    assert log.clarifications_of("t-1", TURN_ASKED) == []


def test_the_trace_route_carries_the_clarification_beside_the_ledger() -> None:
    """The trace is what has to show it: it is the surface that explains a statement.

    Also pins that the reader did **not** reach it by widening the envelope —
    ``tests/api/test_the_audit_surface_reads_thread_state.py`` holds ``get_turn`` to the five keys
    ``record_node`` stores, and a join that mutated the envelope would satisfy this test and break
    that one.
    """
    log, _ = _log(_THREAD)

    trace = trace_for(log, TURN_ASKED)

    assert trace["found"] is True
    assert [r["answer"] for r in trace["clarifications"]] == [
        "Employees ranked by total sales revenue, top 5."
    ]
    assert set(log.get_turn(TURN_ASKED) or {}) == {
        "asked_at",
        "question",
        "answer_text",
        "outcome",
        "record",
    }


def test_the_trace_reports_no_clarifications_for_a_turn_that_asked_none() -> None:
    log, _ = _log(_THREAD)

    assert trace_for(log, TURN_QUIET)["clarifications"] == []


def test_a_missing_turn_never_reaches_the_clarification_read() -> None:
    """``found: False`` short-circuits, so a bad id costs one lookup and not two."""
    log, client = _log(_THREAD)

    trace = trace_for(log, "ffffffffffffffff")

    assert trace == {"found": False, "turn_id": "ffffffffffffffff"}
    assert all(
        call["extract"] != {"clarifications": "values.clarifications"}
        for call in client.threads.calls
    ), "a turn that does not exist has no thread to read clarifications from"
