"""``POST /turns/{id}/raised`` appends through ``append_raised``, not ``command.update``."""

from __future__ import annotations

from typing import Any

from governed_bi.api.routes import make_app

TURN = "turn-raised-1"


class _TurnLog:
    TURN_LOG_DIR = "/nowhere"
    SUMMARY_FIELDS: tuple[str, ...] = ("turn_id",)

    def __init__(self) -> None:
        self.written: list[tuple[str, dict[str, Any]]] = []

    def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[Any]:
        return []

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        if turn_id != TURN:
            return None
        return {
            "asked_at": "2026-08-20T12:00:00Z",
            "question": "revenue?",
            "answer_text": None,
            "outcome": "refused",
            "record": {"turn_id": TURN, "thread_id": "t-1", "outcome": "refused"},
        }

    def clarifications_of(self, thread_id: str, turn_id: str) -> list[Any]:
        return []

    def raised_of(self, thread_id: str, turn_id: str) -> list[Any]:
        return [row for _, row in self.written if row.get("turn_id") == turn_id]

    def append_raised(self, thread_id: str, row: dict[str, Any]) -> None:
        self.written.append((thread_id, row))


def _client(log: _TurnLog | None = None) -> tuple[Any, _TurnLog]:
    """A client over the real app. ``fastapi.testclient`` is the local spelling — the same
    Starlette class, but reached through the declared dependency, and imported here rather
    than at module scope like every other ``_client`` in this directory."""
    from fastapi.testclient import TestClient

    log = log or _TurnLog()

    class _Pending:
        PENDING_FIELDS = ("asked_at",)

        def pending(self, *, limit: int = 50, offset: int = 0) -> Any:
            from governed_bi.api.thread_turns import PendingPage

            return PendingPage(rows=[], truncated=False, threads_scanned=0)

    app = make_app(object(), log, _Pending())
    return TestClient(app), log


def test_post_raised_appends_from_refusal_on_the_turn_s_thread() -> None:
    client, log = _client()
    response = client.post(f"/turns/{TURN}/raised", json={"kind": "from_refusal", "note": "too strict"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["row"]["kind"] == "from_refusal"
    assert body["row"]["thread_id"] == "t-1"
    assert body["row"]["turn_id"] == TURN
    assert body["row"]["open"] is True
    assert log.written[0][0] == "t-1"
    assert log.written[0][1]["note"] == "too strict"


def test_post_raised_rejects_an_unknown_kind() -> None:
    client, _ = _client()
    response = client.post(f"/turns/{TURN}/raised", json={"kind": "interrupt"})
    assert response.status_code == 422


def test_post_raised_422s_an_over_long_note_before_the_writer_sees_it() -> None:
    """The cap is the only bound on how much of a never-swept store a caller can grow.

    Two things are pinned, not one: the status, and that ``append_raised`` was never
    called. A cap enforced *after* the append would be decoration.
    """
    from governed_bi.serve.raised import RAISED_NOTE_MAX_CHARS

    client, log = _client()
    note = "x" * (RAISED_NOTE_MAX_CHARS + 1)
    response = client.post(f"/turns/{TURN}/raised", json={"kind": "wrong_answer", "note": note})
    assert response.status_code == 422, response.text
    assert str(RAISED_NOTE_MAX_CHARS) in response.json()["detail"], "the 422 must name the limit"
    assert log.written == []


def test_post_raised_accepts_a_note_at_the_cap_and_empties_a_whitespace_only_one() -> None:
    """The boundary is inclusive, and padding is not a note.

    Stripping happens before the length check, so trailing newlines from a textarea cannot
    push an otherwise-legal note over — and a note of nothing but spaces is stored as "",
    which is what "no note" already looks like on the row.
    """
    from governed_bi.serve.raised import RAISED_NOTE_MAX_CHARS

    client, log = _client()
    at_cap = "x" * RAISED_NOTE_MAX_CHARS
    ok = client.post(f"/turns/{TURN}/raised", json={"kind": "wrong_answer", "note": at_cap + "\n \n"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["row"]["note"] == at_cap

    blank = client.post(f"/turns/{TURN}/raised", json={"kind": "from_refusal", "note": "   \t\n"})
    assert blank.status_code == 200, blank.text
    assert blank.json()["row"]["note"] == ""
    assert [row["note"] for _, row in log.written] == [at_cap, ""]


def test_raised_row_bounds_the_note_for_an_in_process_caller_too() -> None:
    """The route is not the only way in. ``raised_row`` owns the row's shape, so the cap
    lives there as well — otherwise any other caller has an unbounded path to the same
    accumulating channel."""
    import pytest

    from governed_bi.serve.raised import RAISED_NOTE_MAX_CHARS, raised_row

    with pytest.raises(ValueError, match=str(RAISED_NOTE_MAX_CHARS)):
        raised_row(
            kind="from_refusal",
            turn_id=TURN,
            thread_id="t-1",
            note="x" * (RAISED_NOTE_MAX_CHARS + 1),
        )


def test_post_raised_404s_when_the_turn_is_missing() -> None:
    client, _ = _client()
    response = client.post("/turns/missing/raised", json={"kind": "wrong_answer"})
    assert response.status_code == 404


def test_post_raised_409s_when_the_thread_is_busy() -> None:
    from governed_bi.api.raised_write import ThreadBusy

    class _Busy(_TurnLog):
        def append_raised(self, thread_id: str, row: dict[str, Any]) -> None:
            raise ThreadBusy("thread t-1 is paused; filing a note would consume the live interrupt")

    client, _ = _client(_Busy())
    response = client.post(f"/turns/{TURN}/raised", json={"kind": "from_refusal"})
    assert response.status_code == 409
    assert "paused" in response.json()["detail"]


def test_append_raised_does_not_use_the_saverless_compiled_graph() -> None:
    import inspect

    from governed_bi.api.thread_turns import ThreadTurnLog

    src = inspect.getsource(ThreadTurnLog.append_raised)
    assert "compiled_serve_graph" not in src
    assert "raised_write" in src
