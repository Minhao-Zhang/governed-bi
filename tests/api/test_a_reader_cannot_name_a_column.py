"""``OPERATOR_ONLY_CATEGORIES`` was a gate nothing could trip.

The only filing route hardcoded ``source=Source.operator``, and ``validate.py`` returns ``True`` for
any ``operator``. ``Source.reader`` had no producer anywhere in ``src``, ``tools`` or ``ui``. So the
three categories ``events.py`` says a reader is never asked for -- ``column_suspect``,
``column_excluded``, ``reusable_fact``, each of which names a column -- were filable by any
unauthenticated caller, and the ``Source`` axis carried no information about any filed row.

The route stops claiming a capability nobody verified. ``GOVERNED_BI_FEEDBACK_ADMIN`` is already
"the whole of the control" (``make_admin_router``) and is already threaded in as ``for_steward``, so
it is the one switch that decides: with it on, whoever reaches the port is the steward and files as
``operator``; with it off -- the default deployment -- the caller is a ``reader`` and the gate
fires.

No new control is invented here. The switch already existed and already meant this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from governed_bi.api.routes import make_app
from governed_bi.feedback.events import OPERATOR_ONLY_CATEGORIES, Source
from governed_bi.feedback.store import FeedbackStore

TURN = "turn-observed-1"
ADMIN_SWITCH = "GOVERNED_BI_FEEDBACK_ADMIN"


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
            "outcome": "answered",
            "record": {"turn_id": TURN, "thread_id": "t-1", "outcome": "answered"},
        }

    def clarifications_of(self, thread_id: str, turn_id: str) -> list[Any]:
        return []


class _Pending:
    PENDING_FIELDS = ("asked_at", "observation_id")

    def pending(self, *, limit: int = 50, offset: int = 0) -> Any:
        from governed_bi.api.thread_turns import PendingPage

        return PendingPage(rows=[], truncated=False, threads_scanned=0)


def _client(tmp_path: Path) -> tuple[Any, FeedbackStore]:
    from fastapi.testclient import TestClient

    store = FeedbackStore(tmp_path / "feedback.sqlite")
    return TestClient(make_app(object(), _TurnLog(), _Pending(), store)), store


@pytest.fixture()
def unauthenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FeedbackStore]:
    """The default deployment: nothing authenticates and the admin switch is off."""
    monkeypatch.delenv(ADMIN_SWITCH, raising=False)
    return _client(tmp_path)


@pytest.fixture()
def as_steward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FeedbackStore]:
    """The switch on. ``api/auth.py`` returns one principal, so whoever reaches the port is the
    steward -- which is what the admin router's own docstring says the switch means."""
    monkeypatch.setenv(ADMIN_SWITCH, "1")
    return _client(tmp_path)


def test_an_unauthenticated_filer_is_a_reader(
    unauthenticated: tuple[Any, FeedbackStore]
) -> None:
    """The route claimed ``operator`` for a caller it never identified. ``operator`` means "can
    read the corpus and name an asset", which is a capability, and nothing here checked it."""
    client, store = unauthenticated
    done = client.post(f"/turns/{TURN}/raised", json={"kind": "wrong_answer"})
    assert done.status_code == 201, done.text

    observation_id = done.json()["observation"]["observation_id"]
    row = store.get(observation_id)
    assert row is not None
    assert row.source is Source.reader, (
        f"filed as {row.source.value}, which claims a capability nothing verified"
    )


@pytest.mark.parametrize("category", sorted(c.value for c in OPERATOR_ONLY_CATEGORIES))
def test_the_operator_only_gate_fires_on_the_open_route(
    unauthenticated: tuple[Any, FeedbackStore], category: str
) -> None:
    """Each of the three names a column. A wrong pick sends a reviewer to the wrong asset with a
    confident-looking pointer on it, which is why the source axis gates them at all."""
    client, store = unauthenticated
    done = client.post(
        f"/turns/{TURN}/raised", json={"kind": "wrong_answer", "category": category}
    )
    assert done.status_code == 422, f"{done.status_code}: {done.text[:300]}"
    assert "operator-only" in done.text, done.text[:300]
    assert store.queue(limit=10).total == 0, "and nothing was written"


def test_the_switch_that_mounts_the_steward_verbs_is_the_one_that_grants_the_category(
    as_steward: tuple[Any, FeedbackStore]
) -> None:
    """With the switch on there is a steward, so ``operator`` is a true label and the three
    categories are filable. One switch, read once in ``api/routes.py`` and threaded in."""
    client, store = as_steward
    done = client.post(
        f"/turns/{TURN}/raised", json={"kind": "wrong_answer", "category": "column_excluded"}
    )
    assert done.status_code == 201, done.text

    row = store.get(done.json()["observation"]["observation_id"])
    assert row is not None
    assert row.source is Source.operator
    assert row.category is not None and row.category.value == "column_excluded"


def test_a_reader_can_still_file_the_nine_categories_meant_for_them(
    unauthenticated: tuple[Any, FeedbackStore]
) -> None:
    """The control. A fix that refused every category would pass every test above."""
    client, _ = unauthenticated
    done = client.post(
        f"/turns/{TURN}/raised", json={"kind": "wrong_answer", "category": "wrong_value"}
    )
    assert done.status_code == 201, done.text
    assert done.json()["observation"]["category"] == "wrong_value"
