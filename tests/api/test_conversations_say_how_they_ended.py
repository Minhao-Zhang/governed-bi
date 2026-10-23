"""W2: ``GET /conversations`` lists every conversation with how it ended, from thread state."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from governed_bi.api.routes import make_app
from governed_bi.api.thread_turns import ThreadReader, ending_of
from governed_bi.registry.stages import Outcome


def _turn(outcome: str, question: str = "q", asked_at: str = "2026-10-01T10:00:00Z") -> dict[str, Any]:
    return {"asked_at": asked_at, "question": question, "outcome": outcome,
            "record": {"bulk": "x" * 1000}}


def _thread(thread_id: str, updated_at: str, turns: list[dict[str, Any]], *,
            status: str = "idle", waiting: bool = False) -> dict[str, Any]:
    interrupts: dict[str, Any] = {}
    if waiting:
        interrupts = {"task": [{"id": "i1", "value": {
            "kind": "clarification", "clarification_id": "clar-abc-0123", "question": "which?"}}]}
    return {
        "thread_id": thread_id,
        "updated_at": updated_at,
        "status": "interrupted" if waiting else status,
        "interrupts": interrupts,
        "values": {"question": turns[-1]["question"] if turns else "open question",
                   "turns": turns, "messages": ["not to be read"]},
    }


class _Threads:
    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self._threads = threads
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append(kw)
        rows = sorted(self._threads, key=lambda t: t["updated_at"],
                      reverse=kw.get("sort_order") == "desc")
        if kw.get("status") is not None:
            rows = [t for t in rows if t["status"] == kw["status"]]
        out = []
        for thread in rows[kw["offset"]: kw["offset"] + kw["limit"]]:
            projected = {k: v for k, v in thread.items() if k in kw["select"]}
            projected["extracted"] = {
                alias: thread["values"].get(path.removeprefix("values."))
                for alias, path in (kw.get("extract") or {}).items()
            }
            out.append(projected)
        return out


class _Client:
    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self.threads = _Threads(threads)


STORE = [
    _thread("answered", "2026-10-04T00:00:00Z", [_turn("refused"), _turn("answered", "how many?")]),
    _thread("refused", "2026-10-03T00:00:00Z", [_turn("refused", "who?")]),
    _thread("waiting", "2026-10-05T00:00:00Z", [], waiting=True),
    _thread("crashed", "2026-10-02T00:00:00Z", [_turn("crashed")]),
    _thread("declined", "2026-10-01T00:00:00Z", [_turn("clarification")]),
]


def _http(store: list[dict[str, Any]] = STORE) -> tuple[TestClient, _Client]:
    client = _Client(store)
    return TestClient(make_app(object(), ThreadReader(client_factory=lambda: client))), client


def test_each_conversation_says_how_it_ended_newest_first() -> None:
    http, _ = _http()
    rows = http.get("/conversations").json()["rows"]
    assert [(r["thread_id"], r["ending"]) for r in rows] == [
        ("waiting", "waiting"),
        ("answered", "answered"),
        ("refused", "not_answered"),
        ("crashed", "failed"),
        ("declined", "stopped"),
    ]
    answered = rows[1]
    assert [t["outcome"] for t in answered["turns"]] == ["refused", "answered"]
    assert set(answered["turns"][0]) == {"asked_at", "question", "outcome"}, "a record leaked"
    assert rows[0]["waiting"] is True and rows[0]["question"] == "open question"


def test_the_filter_is_by_ending_and_the_window_follows_it() -> None:
    http, _ = _http()
    page = http.get("/conversations", params={"ending": "failed"}).json()
    assert [r["thread_id"] for r in page["rows"]] == ["crashed"]
    assert page["meta"]["total"] == 1 and page["meta"]["ending"] == "failed"
    windowed = http.get("/conversations", params={"limit": 2, "offset": 1}).json()
    assert [r["thread_id"] for r in windowed["rows"]] == ["answered", "refused"]
    assert windowed["meta"]["truncated"] is True and windowed["meta"]["total"] == 5
    assert http.get("/conversations", params={"ending": "nope"}).status_code == 422


def test_the_reader_extracts_turns_rather_than_reading_every_value() -> None:
    http, client = _http()
    http.get("/conversations")
    call = client.threads.calls[0]
    assert "values" not in call["select"]
    assert call["extract"] == {"turns": "values.turns", "question": "values.question"}


def test_every_outcome_has_an_ending() -> None:
    for outcome in Outcome:
        assert ending_of(waiting=False, status="idle", last_outcome=outcome.value) is not None
    assert ending_of(waiting=False, status="error", last_outcome="answered") == "failed"
    assert ending_of(waiting=False, status="idle", last_outcome=None) is None
