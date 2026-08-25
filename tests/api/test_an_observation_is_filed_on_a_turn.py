"""``POST /turns/{id}/raised`` files into ``runs/feedback.sqlite``, not onto a checkpoint channel.

Replaces ``test_raised_notes_are_filed_on_a_turn.py`` and
``test_filing_a_note_does_not_consume_a_pause.py``. The second file's whole subject was the 409 a
paused thread returned, because ``aupdate_state(as_node="raise_note")`` would have consumed the live
``ask_user`` interrupt. **Nothing writes graph state any more, so that 409 is gone on purpose** —
and the reader whose turn is paused is the one most likely to want to complain, so the test that
matters now is the opposite one, below.

The path and the two ``kind`` values are unchanged, so a client written against the deleted route
keeps working. That is asserted here rather than assumed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from governed_bi.api.routes import make_app
from governed_bi.feedback.events import ObservationState, Source
from governed_bi.feedback.store import FeedbackStore
from governed_bi.feedback.validate import NOTE_MAX_CHARS

TURN = "turn-observed-1"


class _TurnLog:
    """The four members ``make_app`` needs. ``append_raised`` and ``raised_of`` are **absent**, and
    their absence is the point: the seam shrank when the channel went."""

    TURN_LOG_DIR = "/nowhere"
    SUMMARY_FIELDS: tuple[str, ...] = ("turn_id",)

    def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[Any]:
        return []

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        if turn_id != TURN:
            return None
        return {
            "asked_at": "2026-08-20T12:00:00Z",
            "question": "how much revenue last month?",
            "answer_text": None,
            "outcome": "refused",
            "record": {
                "turn_id": TURN,
                "thread_id": "t-1",
                "outcome": "refused",
                "refused_by": "guardrail",
                "generated_sql": "SELECT 1",
                "licensed": ["sales.orders"],
                "schemas": ["sales"],
                "corpus_content_hash": "corpus-a",
                "prompt_set_hash": "prompt-a",
            },
        }

    def clarifications_of(self, thread_id: str, turn_id: str) -> list[Any]:
        return []


class _Pending:
    PENDING_FIELDS = ("asked_at", "observation_id")

    def pending(self, *, limit: int = 50, offset: int = 0) -> Any:
        from governed_bi.api.thread_turns import PendingPage

        return PendingPage(rows=[], truncated=False, threads_scanned=0)


def _client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, steward: bool = False
) -> tuple[Any, FeedbackStore]:
    """A client over the real app and a store in ``tmp_path``, in a **stated** deployment mode.

    The store is the app's **fourth dependency** rather than something it builds, which is what
    keeps this suite out of the operator's real queue.

    ``steward`` is not a convenience. ``api/routes.py`` reads ``GOVERNED_BI_FEEDBACK_ADMIN`` from
    the process environment at mount time, and ``tests/conftest.py`` loads the operator's ``.env``
    into that environment at import -- so every test here inherited whatever the operator happened
    to have set. — `test_the_steward_verbs_are_not_mounted_by_default` passed for a year without
    ever testing the default: it tested one dotenv. Setting
    ``GOVERNED_BI_FEEDBACK_ADMIN=1`` locally on 2026-08-25 turned it red, which is how this was
    found. A test about a security default has to own the variable that decides it.
    """
    from fastapi.testclient import TestClient

    if steward:
        monkeypatch.setenv("GOVERNED_BI_FEEDBACK_ADMIN", "1")
    else:
        monkeypatch.delenv("GOVERNED_BI_FEEDBACK_ADMIN", raising=False)
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    return TestClient(make_app(object(), _TurnLog(), _Pending(), store)), store


def test_the_path_and_the_kind_values_did_not_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A client written against the deleted route keeps working."""
    client, store = _client(tmp_path, monkeypatch)
    response = client.post(f"/turns/{TURN}/raised", json={"kind": "from_refusal"})
    assert response.status_code == 201, response.text
    assert store.queue().total == 1


def test_filing_copies_the_turn_rather_than_joining_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The turn's record elides after 25 turns and its thread index deletes itself on a bare
    exception, so a foreign key into it returns nothing six months from now — which is when a
    reviewer reads the queue.

    The switch is deleted explicitly because ``source`` now depends on it: this is the default
    deployment, where nothing authenticates and the filer is a ``reader``.
    """
    monkeypatch.delenv("GOVERNED_BI_FEEDBACK_ADMIN", raising=False)
    client, store = _client(tmp_path, monkeypatch)
    client.post(f"/turns/{TURN}/raised", json={"kind": "from_refusal"})

    obs = store.queue().rows[0]
    assert obs.turn_id == TURN
    assert obs.thread_id == "t-1"
    assert obs.question == "how much revenue last month?"
    assert obs.generated_sql == "SELECT 1"
    assert obs.licensed == ("sales.orders",)
    assert obs.corpus_content_hash == "corpus-a"
    assert obs.source is Source.reader, (
        "this route hardcoded `operator`, which claims the caller can read the corpus and name an "
        "asset. Nothing here authenticates, so the claim made OPERATOR_ONLY_CATEGORIES a gate that "
        "could not fire on any row in the store"
    )
    assert obs.state is ObservationState.open


def test_a_category_is_optional_and_validated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first tap files something valid; a refinement is never a gate. But a refinement that
    cannot apply to the card is refused, because the queue would sort it into the wrong bucket."""
    client, store = _client(tmp_path, monkeypatch)

    assert client.post(f"/turns/{TURN}/raised", json={"kind": "from_refusal"}).status_code == 201
    assert (
        client.post(
            f"/turns/{TURN}/raised", json={"kind": "from_refusal", "category": "false_refusal"}
        ).status_code
        == 201
    )
    bad = client.post(
        f"/turns/{TURN}/raised", json={"kind": "from_refusal", "category": "wrong_value"}
    )
    assert bad.status_code == 422, bad.text

    # A multiset, not a sequence: the queue orders on `(filed_at, observation_id)` and both rows
    # land in the same second, so the tie-break is a random hex. Asserting insertion order here
    # would be asserting that `secrets.token_hex` came out ascending.
    filed = store.queue().rows
    assert sorted(str(o.category) for o in filed) == ["Category.false_refusal", "None"]


def test_an_unknown_kind_or_category_names_the_vocabulary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    for body, word in (
        ({"kind": "nope"}, "from_refusal"),
        ({"kind": "from_refusal", "category": "nope"}, "false_refusal"),
    ):
        response = client.post(f"/turns/{TURN}/raised", json=body)
        assert response.status_code == 422
        assert word in response.text, response.text


def test_an_over_long_note_is_refused_before_the_store_sees_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """"Too long" without a number is not actionable, so the refusal names the cap."""
    client, store = _client(tmp_path, monkeypatch)
    response = client.post(
        f"/turns/{TURN}/raised", json={"kind": "wrong_answer", "note": "x" * (NOTE_MAX_CHARS + 1)}
    )
    assert response.status_code == 422
    assert str(NOTE_MAX_CHARS) in response.text
    assert store.queue().total == 0


def test_a_whitespace_only_note_becomes_empty_rather_than_spending_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = _client(tmp_path, monkeypatch)
    client.post(f"/turns/{TURN}/raised", json={"kind": "wrong_answer", "note": "   \n  "})
    assert store.queue().rows[0].note == ""


def test_expected_is_carried_and_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The highest-value optional field, and the reason it is short: it is one claim, and a field
    that invites a paragraph gets a paragraph nobody reads."""
    client, store = _client(tmp_path, monkeypatch)
    client.post(
        f"/turns/{TURN}/raised",
        json={"kind": "wrong_answer", "expected": "about 400, not 4102"},
    )
    assert "expected: about 400, not 4102" in store.queue().rows[0].note

    long = client.post(
        f"/turns/{TURN}/raised", json={"kind": "wrong_answer", "expected": "x" * 201}
    )
    assert long.status_code == 422
    assert "200" in long.text


def test_an_unknown_turn_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = _client(tmp_path, monkeypatch)
    assert client.post("/turns/nope/raised", json={"kind": "wrong_answer"}).status_code == 404
    assert store.queue().total == 0


def test_a_note_can_be_filed_while_the_thread_is_paused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**The 409 that went away, asserted as an absence.**

    Filing used to refuse on a paused thread because the write went through
    ``aupdate_state(as_node="raise_note")``, which would consume the live ``ask_user`` interrupt.
    Nothing writes graph state now, so there is no interrupt to consume — and the reader whose turn
    is paused is exactly the reader most likely to want to complain.

    The turn log here does not even expose a thread's status, which is the structural half of the
    same statement: filing cannot depend on something it cannot see.
    """
    client, store = _client(tmp_path, monkeypatch)
    response = client.post(f"/turns/{TURN}/raised", json={"kind": "from_refusal"})
    assert response.status_code == 201
    assert store.queue().total == 1
    assert not hasattr(_TurnLog(), "append_raised"), (
        "the seam still exposes a channel writer; the store is the only writer now"
    )


def test_a_note_can_be_amended_until_somebody_looks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The inversion the design rests on: the note is asked for *after* filing succeeds, so it is a
    bonus rather than a gate. And it freezes at ``triaged``, because a reviewer reading a row whose
    text changes underneath them is worse than a second observation."""
    client, store = _client(tmp_path, monkeypatch)
    filed = client.post(f"/turns/{TURN}/raised", json={"kind": "wrong_answer"}).json()
    observation_id = filed["observation"]["observation_id"]

    amended = client.patch(f"/observations/{observation_id}", json={"note": "it is about 400"})
    assert amended.status_code == 200
    assert store.get(observation_id).note == "it is about 400"  # type: ignore[union-attr]

    store.move(observation_id, to=ObservationState.triaged)
    frozen = client.patch(f"/observations/{observation_id}", json={"note": "too late"})
    assert frozen.status_code == 409
    assert store.get(observation_id).note == "it is about 400"  # type: ignore[union-attr]


def test_the_response_carries_the_row_and_its_computed_openness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    body = client.post(f"/turns/{TURN}/raised", json={"kind": "wrong_answer"}).json()
    observation = body["observation"]
    assert observation["open"] is True, "`open` is computed from the state, never a column"
    assert observation["state"] == "open"
    assert observation["question_is_held_out"] is False, (
        "an operator-filed observation is about a live turn, not a held-out benchmark question"
    )
    assert observation["patches"] == []
    assert [h["to_state"] for h in observation["history"]] == ["open"]
    assert observation["history"][0]["moved_by"] == "filer"


def test_the_steward_verbs_are_not_mounted_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """404 and not 403: a 403 confirms the route exists. With one principal the switch is the whole
    of the control, so the honest default is off."""
    client, store = _client(tmp_path, monkeypatch)
    filed = client.post(f"/turns/{TURN}/raised", json={"kind": "wrong_answer"}).json()
    observation_id = filed["observation"]["observation_id"]

    response = client.post(f"/observations/{observation_id}/triage", json={"to": "triaged"})
    assert response.status_code == 404
    assert store.get(observation_id).state is ObservationState.open  # type: ignore[union-attr]
