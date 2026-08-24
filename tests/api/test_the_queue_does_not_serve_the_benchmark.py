"""What an unauthenticated caller may read off the return path, field by field.

**The defect this pins.** ``_wire_observation`` put the **held-out reference answer** on
``GET /observations``, which mounts unconditionally and authenticates nothing. `gold_sql`,
`gold_fingerprint` and `pred_fingerprint` were all there. Conformance rule V12 exists to stop a
held-out question reaching *the corpus*; serving the answer over HTTP is the same contamination
channel with the gate bypassed, and it was introduced by the branch that added V12's enforcement.

**The second half: the admin gate on patch content was decorative.** With
``GOVERNED_BI_FEEDBACK_ADMIN`` unset, ``GET /patches`` answers 404 — and
``GET /observations/{id}`` returned every patch's `was`, `becomes`, `rationale` and full history
anyway. A reviewer reproduced a response carrying `"was": "OLD SECRET DEFINITION"` and the
steward's private `rationale` with the switch off.

**And `api/routes.py` claimed "the disclosure is unchanged".** It was not. `main`'s `raised` row
carried seven fields: `kind`, `turn_id`, `thread_id`, `note`, `report_id`, `reported_at`, `open`.
This branch's row carries thirty-one.

So this file is an **allowlist**, not a denylist. A new field on `Observation` is invisible to the
wire until somebody adds it here and says why it is safe to serve — which is the opposite of the
default that produced the defect, where a field added to the dataclass was published by the next
deploy.

**What is deliberately still served, and why.** `question`, `generated_sql`, `licensed` and
`missing_tables` stay: they are what makes a queue row reviewable at all, and
``/audit/turns/{id}/trace`` already discloses a turn's SQL to the same caller. That was the
accepted position before this branch and it is unchanged. What was never accepted is the *gold*
statement, because that one is a measurement instrument rather than a record of what happened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from governed_bi.api.routes import make_app
from governed_bi.feedback.events import (
    Kind,
    Observation,
    ObservationState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.store import FeedbackStore, mint_observation_id, mint_patch_id, utc_now
from governed_bi.feedback.validate import CONTENT_HASH_CHARS
from governed_bi.register.assets import AssetType

ADMIN_SWITCH = "GOVERNED_BI_FEEDBACK_ADMIN"
GOLD = "SELECT brauerei_name FROM beer_factory.wurzelbier GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1"

#: Every field an unauthenticated caller is allowed to see on an observation, and nothing else.
#: Adding a name here is a decision about disclosure; leaving one out is the safe default.
PUBLIC_OBSERVATION_FIELDS = frozenset(
    {
        # identity and lifecycle
        "observation_id",
        "filed_at",
        "source",
        "kind",
        "category",
        "state",
        "open",
        "decline_reason",
        "duplicate_of",
        "blocked_note",
        # what the failure was about. `question` and `generated_sql` are the accepted position:
        # `/audit/turns/{id}/trace` already serves a turn's SQL to the same caller.
        "note",
        "turn_id",
        "thread_id",
        "question",
        "outcome",
        "refused_by",
        "generated_sql",
        "licensed",
        "schemas",
        "missing_tables",
        # provenance a reviewer needs to know which run a row came from
        "arm",
        "question_id",
        "db_id",
        "corpus_content_hash",
        "quality_flags",
        # the warning that makes the held-out flag act like one
        "question_is_held_out",
    }
)

#: The three that must never be served unauthenticated. Named individually rather than derived,
#: because the point is that somebody has to type the name to publish it.
BENCHMARK_FIELDS = ("gold_sql", "gold_fingerprint", "pred_fingerprint")

#: Patch fields an unauthenticated caller may see. The *content* of a proposed change is the
#: steward's working draft, so it is admin-only; the fact that a patch exists is not a secret.
PUBLIC_PATCH_FIELDS = frozenset(
    {
        "patch_id",
        "created_at",
        "author",
        "intent",
        "state",
        "namespace",
        "asset_type",
        "asset_id",
        "field_path",
        "ladder",
        "observations",
        "derived_state",
        "withdrawn_reason",
    }
)

PRIVATE_PATCH_FIELDS = ("was", "becomes", "rationale", "base_corpus_content_hash")

TURN = "turn-observed-1"


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


def _imported(store: FeedbackStore) -> str:
    """An imported row, which is the only kind that carries a gold statement."""
    observation = Observation(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.eval,
        kind=Kind.wrong_answer,
        state=ObservationState.open,
        question="Which brewery made the best-selling root beer in 2016?",
        external_key="k-1",
        arm="v4",
        question_id="train_5274",
        db_id="beer_factory",
        gold_sql=GOLD,
        gold_fingerprint="gold-abc",
        pred_fingerprint="pred-def",
        missing_tables=("beer_factory.wurzelbier",),
        generated_sql="SELECT brauerei_name FROM beer_factory.brauerei",
        licensed=("beer_factory.brauerei",),
        schemas=("beer_factory",),
    )
    store.file(observation)
    return observation.observation_id


def _drafted(store: FeedbackStore, observation_id: str) -> str:
    patch = Patch(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.edit_asset,
        state=PatchState.draft,
        namespace="beer_factory",
        asset_type=AssetType.table,
        asset_id="beer_factory.wurzelbier",
        field_path="summary",
        was="OLD SECRET DEFINITION",
        becomes="NEW SECRET DEFINITION",
        rationale="a private note about why the steward thinks this is the fix",
        base_corpus_content_hash="a" * CONTENT_HASH_CHARS,
    )
    store.draft(patch, observations=[observation_id])
    return patch.patch_id


@pytest.fixture()
def unauthenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FeedbackStore]:
    """The default deployment: the admin switch is off, so `GET /patches` 404s."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv(ADMIN_SWITCH, raising=False)
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    return TestClient(make_app(object(), _TurnLog(), _Pending(), store)), store


@pytest.fixture()
def as_steward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FeedbackStore]:
    from fastapi.testclient import TestClient

    monkeypatch.setenv(ADMIN_SWITCH, "1")
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    return TestClient(make_app(object(), _TurnLog(), _Pending(), store)), store


# ── the benchmark answer ──────────────────────────────────────────────────────


def test_the_queue_does_not_serve_the_gold_statement(
    unauthenticated: tuple[Any, FeedbackStore],
) -> None:
    """The held-out reference answer, on a route that authenticates nothing.

    V12 refuses a corpus asset that quotes five consecutive words of a held-out question. This
    served the *answer*, in full, to anybody who could reach the port.
    """
    client, store = unauthenticated
    _imported(store)

    body = client.get("/observations").json()
    row = body["rows"][0]

    for field in BENCHMARK_FIELDS:
        assert field not in row, f"{field} is on the unauthenticated queue"
    assert GOLD not in client.get("/observations").text, (
        "the gold statement appears somewhere in the response body"
    )


def test_the_detail_route_does_not_serve_the_gold_statement(
    unauthenticated: tuple[Any, FeedbackStore],
) -> None:
    client, store = unauthenticated
    observation_id = _imported(store)

    response = client.get(f"/observations/{observation_id}")
    assert response.status_code == 200
    for field in BENCHMARK_FIELDS:
        assert field not in response.json(), f"{field} is on the unauthenticated detail route"
    assert GOLD not in response.text


def test_the_clustered_shape_does_not_serve_the_gold_statement(
    unauthenticated: tuple[Any, FeedbackStore],
) -> None:
    """The grouped projection embeds whole observations, so it is a second door to the same room."""
    client, store = unauthenticated
    _imported(store)

    response = client.get("/observations?group=cluster&state=open")
    assert response.status_code == 200
    assert GOLD not in response.text
    member = response.json()["clusters"][0]["observations"][0]
    for field in BENCHMARK_FIELDS:
        assert field not in member


def test_the_steward_can_still_see_the_gold_statement(
    as_steward: tuple[Any, FeedbackStore],
) -> None:
    """The control. Narrowing must not blind the person the queue is for: the gold statement is
    the strongest evidence on the review surface, and hiding it from the steward would make the
    screen useless. It is the *unauthenticated* route that must not carry it."""
    client, store = as_steward
    observation_id = _imported(store)

    detail = client.get(f"/observations/{observation_id}").json()
    assert detail["gold_sql"] == GOLD
    assert detail["gold_fingerprint"] == "gold-abc"
    assert detail["pred_fingerprint"] == "pred-def"


# ── patch content ─────────────────────────────────────────────────────────────


def test_patch_content_is_not_readable_through_the_observation_route(
    unauthenticated: tuple[Any, FeedbackStore],
) -> None:
    """`GET /patches` 404s with the switch off, and this route served the same content anyway.

    A 404 on one door and an open window on the other is worse than no door: the operator reads
    the 404 as the control working.
    """
    client, store = unauthenticated
    observation_id = _imported(store)
    _drafted(store, observation_id)

    assert client.get("/patches").status_code == 404, "the premise of this test has changed"

    response = client.get(f"/observations/{observation_id}")
    assert response.status_code == 200
    assert "OLD SECRET DEFINITION" not in response.text
    assert "NEW SECRET DEFINITION" not in response.text
    assert "a private note about why" not in response.text

    patch_row = response.json()["patches"][0]
    for field in PRIVATE_PATCH_FIELDS:
        assert field not in patch_row, f"{field} is readable unauthenticated"
    assert patch_row["patch_id"], "the fact that a patch exists is not the secret"
    assert patch_row["state"] == "draft"


def test_the_steward_sees_the_whole_patch(as_steward: tuple[Any, FeedbackStore]) -> None:
    """The control for the half above."""
    client, store = as_steward
    observation_id = _imported(store)
    _drafted(store, observation_id)

    patch_row = client.get(f"/observations/{observation_id}").json()["patches"][0]
    assert patch_row["was"] == "OLD SECRET DEFINITION"
    assert patch_row["becomes"] == "NEW SECRET DEFINITION"
    assert patch_row["rationale"].startswith("a private note")

    listed = client.get("/patches")
    assert listed.status_code == 200
    assert listed.json()["patches"][0]["becomes"] == "NEW SECRET DEFINITION"


# ── the allowlist itself ──────────────────────────────────────────────────────


def test_the_public_observation_shape_is_exactly_the_allowlist(
    unauthenticated: tuple[Any, FeedbackStore],
) -> None:
    """**An allowlist, so a new field is invisible until somebody publishes it deliberately.**

    The default that produced the defect was the opposite: `_wire_observation` enumerated the
    dataclass, so a field added to `Observation` was on an unauthenticated route by the next
    deploy. `gold_sql` arrived exactly that way.
    """
    client, store = unauthenticated
    _imported(store)

    served = set(client.get("/observations").json()["rows"][0])
    extra = served - PUBLIC_OBSERVATION_FIELDS
    assert extra == set(), (
        f"these fields reached an unauthenticated caller and are not on the allowlist: "
        f"{sorted(extra)}. Add each one here with a sentence saying why it is safe to serve, or "
        "keep it off the wire."
    )


def test_the_public_patch_shape_is_exactly_the_allowlist(
    unauthenticated: tuple[Any, FeedbackStore],
) -> None:
    client, store = unauthenticated
    observation_id = _imported(store)
    _drafted(store, observation_id)

    served = set(client.get(f"/observations/{observation_id}").json()["patches"][0])
    extra = served - PUBLIC_PATCH_FIELDS
    assert extra == set(), (
        f"these patch fields reached an unauthenticated caller: {sorted(extra)}"
    )


def test_the_history_does_not_leak_a_detail_string(
    unauthenticated: tuple[Any, FeedbackStore],
) -> None:
    """A transition's ``detail`` is whatever the steward typed -- a decline reason in prose, a
    withdraw note. The *shape* of the trail is public; the sentences are not."""
    client, store = unauthenticated
    observation_id = _imported(store)
    store.move(
        observation_id,
        to=ObservationState.triaged,
        detail="a private steward note in the audit trail",
    )

    response = client.get(f"/observations/{observation_id}")
    assert "a private steward note" not in response.text
    row = response.json()["history"][0]
    assert row["to_state"] == "open", "the trail's shape is still readable"


# ── the switch itself ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["false", "0", "off", "no", "", "  ", "FALSE", "Off"])
def test_a_falsy_switch_value_does_not_arm_the_steward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """`os.environ.get(...)` on a non-empty string is truthy, so `=false` **mounted** the router.

    An operator writing `GOVERNED_BI_FEEDBACK_ADMIN=false` in `.env` to turn it off was granting
    unauthenticated triage, draft and withdraw -- and widening the read projection at the same time.
    Verified before the fix for `false`, `0`, `off` and `no`.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setenv(ADMIN_SWITCH, value)
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    client = TestClient(make_app(object(), _TurnLog(), _Pending(), store))
    observation_id = _imported(store)
    _drafted(store, observation_id)

    assert client.get("/patches").status_code == 404, (
        f"{ADMIN_SWITCH}={value!r} mounted the steward's verbs"
    )
    assert GOLD not in client.get(f"/observations/{observation_id}").text, (
        f"{ADMIN_SWITCH}={value!r} widened the read projection"
    )


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything"])
def test_a_truthy_switch_value_arms_the_steward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The control. A deployment that means to opt in must still be able to, including with a
    value nobody enumerated -- the rule is "not one of the four spellings of no", not an
    allowlist of yesses."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv(ADMIN_SWITCH, value)
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    client = TestClient(make_app(object(), _TurnLog(), _Pending(), store))

    assert client.get("/patches").status_code == 200


def test_the_two_halves_of_the_switch_cannot_disagree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One read, two consequences. A 404 on `GET /patches` while `GET /observations/{id}` serves
    the same patch content is worse than no gate at all: the operator reads the 404 as the control
    working. That is what shipped, and it happened because the projection did not consult the
    switch at all."""
    from fastapi.testclient import TestClient

    for value, mounted in (("1", True), ("false", False)):
        monkeypatch.setenv(ADMIN_SWITCH, value)
        store = FeedbackStore(tmp_path / f"feedback-{value}.sqlite")
        client = TestClient(make_app(object(), _TurnLog(), _Pending(), store))
        observation_id = _imported(store)
        _drafted(store, observation_id)

        listing = client.get("/patches").status_code == 200
        detail = client.get(f"/observations/{observation_id}").json()["patches"][0]
        serves_content = "becomes" in detail

        assert listing is mounted
        assert serves_content is mounted, (
            f"{ADMIN_SWITCH}={value!r}: /patches mounted={listing} but the detail route "
            f"serves patch content={serves_content}. The two halves disagree."
        )
