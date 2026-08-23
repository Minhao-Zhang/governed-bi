"""The three patch verbs on the admin router, and the two things they must not do.

The verbs are mounted only under ``GOVERNED_BI_FEEDBACK_ADMIN``, so every test here sets it. That
switch is the whole of the control — ``api/auth.py`` returns one principal — which is why the
default-off case is asserted in ``test_an_observation_is_filed_on_a_turn.py`` rather than here.

**Neither verb writes to the corpus and neither can.** Drafting records what a change *would* be;
the write is a human's ``git commit`` in a repository this process cannot reach. That is the
provenance gate, and a test that could not tell the difference would be a test of nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from governed_bi.api.routes import make_app
from governed_bi.feedback.events import PatchState
from governed_bi.feedback.store import FeedbackStore
from governed_bi.feedback.validate import CONTENT_HASH_CHARS

TURN = "turn-observed-1"
HASH = "c" * CONTENT_HASH_CHARS


class _TurnLog:
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
            "record": {"turn_id": TURN, "thread_id": "t-1", "outcome": "refused"},
        }

    def clarifications_of(self, thread_id: str, turn_id: str) -> list[Any]:
        return []


class _Pending:
    PENDING_FIELDS = ("asked_at", "observation_id")

    def pending(self, *, limit: int = 50, offset: int = 0) -> Any:
        from governed_bi.api.thread_turns import PendingPage

        return PendingPage(rows=[], truncated=False, threads_scanned=0)


@pytest.fixture()
def client_and_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, FeedbackStore]:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("GOVERNED_BI_FEEDBACK_ADMIN", "1")
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    return TestClient(make_app(object(), _TurnLog(), _Pending(), store)), store


def _draft(client: Any, **over: object) -> Any:
    body: dict[str, Any] = dict(
        intent="edit_asset",
        namespace="sales",
        asset_type="table",
        asset_id="sales.orders",
        field_path="summary",
        was="one row per order",
        becomes="one row per placed order",
        base_corpus_content_hash=HASH,
        rationale="the reference answer reads this table and retrieval did not license it",
    )
    body.update(over)
    return client.post("/patches", json=body)


def test_a_draft_records_the_change_and_writes_no_corpus(
    client_and_store: tuple[Any, FeedbackStore],
) -> None:
    client, store = client_and_store
    response = _draft(client)
    assert response.status_code == 201, response.text

    patch = response.json()["patch"]
    assert patch["state"] == "draft"
    assert patch["author"] == "operator"
    assert patch["was"] == "one row per order"
    assert patch["becomes"] == "one row per placed order"
    assert patch["expected_corpus_content_hash"] is None, (
        "the hash of a tree nobody has written yet. A hash-shaped string nobody can compare is "
        "worse than an absence"
    )
    assert patch["ladder"] == {}, "nothing has verified it"
    assert store.get_patch(patch["patch_id"]) is not None


def test_a_draft_attaches_to_the_observations_it_answers(
    client_and_store: tuple[Any, FeedbackStore],
) -> None:
    client, store = client_and_store
    filed = client.post(f"/turns/{TURN}/raised", json={"kind": "wrong_answer"}).json()
    observation_id = filed["observation"]["observation_id"]

    patch = _draft(client, observations=[observation_id]).json()["patch"]
    assert patch["observations"] == [observation_id]
    assert [p.patch_id for p in store.patches_of(observation_id)] == [patch["patch_id"]]

    detail = client.get(f"/observations/{observation_id}").json()
    assert [p["patch_id"] for p in detail["patches"]] == [patch["patch_id"]]
    assert detail["patches"][0]["derived_state"] is None, (
        "this route has no session and cannot read the corpus; a stale landing state here would "
        "disagree with tools/check_landed.py"
    )


def test_an_observation_that_does_not_exist_is_404_and_not_a_dangling_link(
    client_and_store: tuple[Any, FeedbackStore],
) -> None:
    """The join table has no foreign-key enforcement worth relying on across a fresh file, and a
    patch attached to nothing is a patch the queue can never surface."""
    client, store = client_and_store
    response = _draft(client, observations=["obs-nope"])
    assert response.status_code == 404
    assert store.patches(limit=10).total == 0, "and nothing was written"


def test_an_unknown_intent_names_the_declared_set(
    client_and_store: tuple[Any, FeedbackStore],
) -> None:
    """A client sending `edit` for `edit_asset` should learn the vocabulary from the 422."""
    client, _ = client_and_store
    response = _draft(client, intent="edit")
    assert response.status_code == 422
    assert "edit_asset" in str(response.json()["detail"])


def test_a_truncated_hash_is_refused_through_the_route_too(
    client_and_store: tuple[Any, FeedbackStore],
) -> None:
    """The store's rule, reached through HTTP. A 16-character prefix is what every display shows,
    so it is the value a hand-written body is most likely to carry."""
    client, store = client_and_store
    response = _draft(client, base_corpus_content_hash=HASH[:16])
    assert response.status_code == 422
    assert "16 characters" in str(response.json()["detail"])
    assert store.patches(limit=10).total == 0


def test_withdrawing_needs_a_reason(client_and_store: tuple[Any, FeedbackStore]) -> None:
    client, store = client_and_store
    patch_id = _draft(client).json()["patch"]["patch_id"]

    assert client.post(f"/patches/{patch_id}/withdraw", json={}).status_code == 422
    assert store.get_patch(patch_id).state is PatchState.draft  # type: ignore[union-attr]

    response = client.post(
        f"/patches/{patch_id}/withdraw", json={"reason": "the gap is the router, not the summary"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["patch"]["state"] == "withdrawn"
    assert store.get_patch(patch_id).withdrawn_reason.startswith("the gap")  # type: ignore[union-attr]


def test_withdrawing_twice_is_a_409_from_the_table(
    client_and_store: tuple[Any, FeedbackStore],
) -> None:
    """``PATCH_TRANSITIONS`` decides, not the handler. It was declared and read by nothing until
    the store started consulting it."""
    client, _ = client_and_store
    patch_id = _draft(client).json()["patch"]["patch_id"]
    client.post(f"/patches/{patch_id}/withdraw", json={"reason": "first"})

    again = client.post(f"/patches/{patch_id}/withdraw", json={"reason": "second"})
    assert again.status_code == 409
    assert "not a declared transition" in str(again.json()["detail"])


def test_the_list_is_newest_first_and_filters_by_state(
    client_and_store: tuple[Any, FeedbackStore],
) -> None:
    """Newest-first, where the observation queue is oldest-first. An observation queue is work
    waiting; a patch list is work done, and the one just authored is the one being looked for."""
    client, _ = client_and_store
    first = _draft(client, becomes="first edit").json()["patch"]["patch_id"]
    second = _draft(client, becomes="second edit").json()["patch"]["patch_id"]
    client.post(f"/patches/{first}/withdraw", json={"reason": "superseded by the second"})

    everything = client.get("/patches").json()
    assert [p["patch_id"] for p in everything["patches"]] == [second, first]
    assert everything["meta"]["total"] == 2

    drafts = client.get("/patches?state=draft").json()
    assert [p["patch_id"] for p in drafts["patches"]] == [second]

    unknown = client.get("/patches?state=landed")
    assert unknown.status_code == 422
    assert "withdrawn" in str(unknown.json()["detail"]), "the 422 names the declared set"
