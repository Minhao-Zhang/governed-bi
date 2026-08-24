"""Withdrawing a patch had no effect on the observations it was answering.

``store.draft`` got its move yesterday: it is the only producer of ``ObservationState.addressed``
and it reports every row it moved and every row it did not. The inverse was never built.
``move_patch`` referenced ``ObservationState`` nowhere, so a withdrawn patch left its observations
reading ``addressed`` -- *somebody answered this* -- while the only patch that ever answered them
was gone. Driven through the routes against the live store:

    withdraw -> 200
    patch state: withdrawn | withdrawn_reason: T3 refuted it: ...
    observation: addressed          <- still

The ``addressed -> triaged`` edge exists for exactly this, and ``lifecycle.py`` gives its
``requires`` as "every patch for it was withdrawn" -- a clause that had become true and that nothing
evaluated. The row stayed mislabelled until a steward moved it by hand, and until then the queue
could not tell *answered* from *abandoned*.

The rules these tests pin, and they are ``draft``'s rules read backwards:

* a withdrawal that leaves no live patch **moves** the row back to ``triaged``, with an audit line
  naming the patch that was withdrawn;
* a withdrawal that leaves a second draft open moves nothing -- the row has not been abandoned --
  and says which clause stopped it;
* a row the table cannot move that way is **named, not skipped**, and a terminal row is neither
  moved nor reported as moved;
* ``draft -> exported`` moves nothing at all. Exporting is not abandoning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.feedback.events import (
    DeclineReason,
    Kind,
    Observation,
    ObservationState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.lifecycle import Actor
from governed_bi.feedback.store import (
    FeedbackStore,
    mint_observation_id,
    mint_patch_id,
    utc_now,
)
from governed_bi.register.assets import AssetType

CONTENT_HASH = "c" * 64


def _store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.sqlite")


def _filed(store: FeedbackStore, note: str = "the total is about 400, not 4102") -> Observation:
    obs = Observation(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.reader,
        kind=Kind.wrong_answer,
        state=ObservationState.open,
        note=note,
        question="how much revenue last month?",
        turn_id="turn-1",
        thread_id="t-1",
    )
    store.file(obs)
    return obs


def _triaged(store: FeedbackStore, note: str = "the total is about 400, not 4102") -> Observation:
    obs = _filed(store, note)
    store.move(obs.observation_id, to=ObservationState.triaged)
    return obs


def _patch(becomes: str = "orders is the transaction table, one row per placed order.") -> Patch:
    return Patch(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.edit_asset,
        state=PatchState.draft,
        namespace="sales",
        asset_type=AssetType.table,
        asset_id="sales.orders",
        field_path="summary",
        was="orders is the transaction table.",
        becomes=becomes,
        base_corpus_content_hash=CONTENT_HASH,
        rationale="the reference answer reads this table and retrieval did not license it",
    )


def _state(store: FeedbackStore, observation_id: str) -> ObservationState:
    obs = store.get(observation_id)
    assert obs is not None
    return obs.state


# ── the move ──────────────────────────────────────────────────────────────────


def test_withdrawing_the_only_patch_returns_the_row_to_triaged(tmp_path: Path) -> None:
    """The defect, at its smallest. One observation, one patch, withdraw it.

    ``triaged`` and not ``open``: somebody has looked, and the edge back to ``open`` does not exist
    for that reason.
    """
    store = _store(tmp_path)
    obs = _triaged(store)
    patch = _patch()
    store.draft(patch, observations=[obs.observation_id])
    assert _state(store, obs.observation_id) is ObservationState.addressed

    moved = store.move_patch(
        patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="T3 refuted it"
    )

    assert _state(store, obs.observation_id) is ObservationState.triaged
    assert moved.reopened == (obs.observation_id,)
    assert moved.not_reopened == ()


def test_the_withdrawal_says_which_rows_it_returned(tmp_path: Path) -> None:
    """The report is the wire's content, so it is what the route can carry.

    A caller that has to re-read every attached observation to learn whether the withdrawal
    unaddressed them will not do it, which is the argument ``Drafted`` was introduced on.
    """
    store = _store(tmp_path)
    first, second = _triaged(store, "first"), _triaged(store, "second")
    patch = _patch()
    store.draft(patch, observations=[first.observation_id, second.observation_id])

    moved = store.move_patch(
        patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="the join was already declared"
    )

    assert _state(store, first.observation_id) is ObservationState.triaged
    assert _state(store, second.observation_id) is ObservationState.triaged
    assert set(moved.reopened) == {first.observation_id, second.observation_id}
    assert moved.patch.state is PatchState.withdrawn


def test_the_audit_trail_carries_the_move_and_names_the_patch(tmp_path: Path) -> None:
    """The transition table is the audit trail, so a move it does not record did not happen as far
    as anybody reading the queue later is concerned. The steward moved it, through the table's own
    declared actor."""
    store = _store(tmp_path)
    obs = _triaged(store)
    patch = _patch()
    store.draft(patch, observations=[obs.observation_id])

    store.move_patch(patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="wrong asset")

    reopen = [
        row
        for row in store.history(obs.observation_id)
        if row["from_state"] == ObservationState.addressed.value
        and row["to_state"] == ObservationState.triaged.value
    ]
    assert len(reopen) == 1, "one audit line for one move"
    assert reopen[0]["moved_by"] == Actor.steward.value
    assert patch.patch_id in reopen[0]["detail"], (
        "the trail must name the patch that was withdrawn -- otherwise a row with two patches has "
        "an audit line that cannot be attributed"
    )


# ── the requires clause ───────────────────────────────────────────────────────


def test_a_second_live_patch_keeps_the_row_addressed(tmp_path: Path) -> None:
    """``addressed -> triaged`` requires *every* patch withdrawn, and a row with a second draft
    still open has not been abandoned.

    ``_edge_faults`` already enforces this on the way in. The withdrawal does not re-implement the
    check, it lets it fire and reports the refusal, so there is one copy of the rule.
    """
    store = _store(tmp_path)
    obs = _triaged(store)
    first, second = _patch("a synonym"), _patch("a join")
    store.draft(first, observations=[obs.observation_id])
    store.draft(second, observations=[obs.observation_id])

    moved = store.move_patch(
        first.patch_id, to=PatchState.withdrawn, withdrawn_reason="the synonym was already there"
    )

    assert _state(store, obs.observation_id) is ObservationState.addressed
    assert moved.reopened == ()
    assert [u.observation_id for u in moved.not_reopened] == [obs.observation_id]
    assert [u.state for u in moved.not_reopened] == [ObservationState.addressed]
    assert "every patch withdrawn" in moved.not_reopened[0].why


def test_withdrawing_the_last_live_patch_then_returns_the_row(tmp_path: Path) -> None:
    """The other half of the clause. The second withdrawal is the one that abandons the row, and it
    is the one that moves it."""
    store = _store(tmp_path)
    obs = _triaged(store)
    first, second = _patch("a synonym"), _patch("a join")
    store.draft(first, observations=[obs.observation_id])
    store.draft(second, observations=[obs.observation_id])
    store.move_patch(first.patch_id, to=PatchState.withdrawn, withdrawn_reason="superseded")

    moved = store.move_patch(
        second.patch_id, to=PatchState.withdrawn, withdrawn_reason="T3 refuted both"
    )

    assert moved.reopened == (obs.observation_id,)
    assert _state(store, obs.observation_id) is ObservationState.triaged


def test_an_exported_sibling_is_live_and_holds_the_row(tmp_path: Path) -> None:
    """``exported`` is live, not finished: the bundle is with an engineer. A row whose other patch
    is out for review has been answered, so withdrawing this one does not return it."""
    store = _store(tmp_path)
    obs = _triaged(store)
    first, second = _patch("a synonym"), _patch("a join")
    store.draft(first, observations=[obs.observation_id])
    store.draft(second, observations=[obs.observation_id])
    store.move_patch(
        second.patch_id,
        to=PatchState.exported,
        expected_corpus_content_hash="d" * 64,
        detail="bundle written",
    )

    moved = store.move_patch(
        first.patch_id, to=PatchState.withdrawn, withdrawn_reason="the other one is enough"
    )

    assert _state(store, obs.observation_id) is ObservationState.addressed
    assert [u.observation_id for u in moved.not_reopened] == [obs.observation_id]


# ── what the table will not permit ────────────────────────────────────────────


def test_a_declined_row_is_reported_and_not_moved(tmp_path: Path) -> None:
    """``declined`` is terminal: nothing moves out of it, and a withdrawal must not pretend it did.

    Reported rather than skipped, because silence is indistinguishable from a move that worked --
    the defect family this branch has spent three days removing.
    """
    store = _store(tmp_path)
    obs = _triaged(store)
    patch = _patch()
    store.draft(patch, observations=[obs.observation_id])
    store.move(
        obs.observation_id,
        to=ObservationState.declined,
        decline_reason=DeclineReason.cannot_reproduce,
    )

    moved = store.move_patch(
        patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="the row was declined anyway"
    )

    assert _state(store, obs.observation_id) is ObservationState.declined, "still terminal"
    assert moved.reopened == ()
    assert [u.observation_id for u in moved.not_reopened] == [obs.observation_id]
    assert moved.not_reopened[0].state is ObservationState.declined
    assert "declined" in moved.not_reopened[0].why


def test_a_duplicate_row_in_the_patch_set_is_reported_and_not_moved(tmp_path: Path) -> None:
    """A ``duplicate`` joins the original's patch set, so it is attached to a patch it never took an
    ``addressed`` edge for. Withdrawing that patch returns the original and leaves the duplicate,
    which is where the steward already put it."""
    store = _store(tmp_path)
    original, copy = _triaged(store, "first"), _triaged(store, "again")
    patch = _patch()
    store.draft(patch, observations=[original.observation_id])
    store.move(
        copy.observation_id,
        to=ObservationState.duplicate,
        duplicate_of=original.observation_id,
    )
    assert patch.patch_id in {p.patch_id for p in store.patches_of(copy.observation_id)}

    moved = store.move_patch(patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="refuted")

    assert moved.reopened == (original.observation_id,)
    assert _state(store, copy.observation_id) is ObservationState.duplicate
    assert [u.observation_id for u in moved.not_reopened] == [copy.observation_id]


def test_an_open_row_is_reported_and_never_triaged_by_a_withdrawal(tmp_path: Path) -> None:
    """``open -> triaged`` **is** a declared edge, and taking it here would be the bug.

    Drafting against an ``open`` row attaches the patch and leaves the row alone -- ``draft``
    reports that in ``not_addressed``. Withdrawing must not then move it, because ``triaged`` means
    somebody looked and nobody did. So the candidate set is ``addressed`` rows, not every attached
    row, and the rest are named.
    """
    store = _store(tmp_path)
    obs = _filed(store)
    patch = _patch()
    drafted = store.draft(patch, observations=[obs.observation_id])
    assert [u.state for u in drafted.not_addressed] == [ObservationState.open]

    moved = store.move_patch(patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="refuted")

    assert _state(store, obs.observation_id) is ObservationState.open, "not triaged by a withdrawal"
    assert moved.reopened == ()
    assert [u.observation_id for u in moved.not_reopened] == [obs.observation_id]


# ── the other edges ───────────────────────────────────────────────────────────


def test_exporting_moves_no_observation(tmp_path: Path) -> None:
    """``move_patch`` serves ``draft -> exported`` too, and that edge must move nothing.

    The branch is on the *target* state, not on the method: a bundle going out is the patch making
    progress, and returning its observations to the queue would empty ``addressed`` on every
    export.
    """
    store = _store(tmp_path)
    obs = _triaged(store)
    patch = _patch()
    store.draft(patch, observations=[obs.observation_id])

    moved = store.move_patch(
        patch.patch_id,
        to=PatchState.exported,
        expected_corpus_content_hash="d" * 64,
        detail="bundle written",
    )

    assert _state(store, obs.observation_id) is ObservationState.addressed
    assert moved.patch.state is PatchState.exported
    assert (moved.reopened, moved.not_reopened) == ((), ())
    assert not [
        row
        for row in store.history(obs.observation_id)
        if row["to_state"] == ObservationState.triaged.value
        and row["from_state"] == ObservationState.addressed.value
    ], "exporting wrote no observation transition"


def test_a_patch_that_answers_nobody_returns_nobody(tmp_path: Path) -> None:
    """A patch drafted from a corpus audit answers no complaint. Withdrawing it reports two empty
    tuples rather than nothing, for the reason ``Drafted`` does."""
    store = _store(tmp_path)
    patch = _patch()
    store.draft(patch, observations=[])

    moved = store.move_patch(patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="no longer")

    assert (moved.reopened, moved.not_reopened) == ((), ())


def test_the_round_trip_ends_addressed_again(tmp_path: Path) -> None:
    """draft -> withdraw -> draft. The loop the two producers exist to close.

    Without the withdrawal's move the second draft finds the row already ``addressed`` and reports
    it un-moved, so a steward's second attempt reads as a failure.
    """
    store = _store(tmp_path)
    obs = _triaged(store)
    first = _patch("a synonym")
    store.draft(first, observations=[obs.observation_id])
    store.move_patch(first.patch_id, to=PatchState.withdrawn, withdrawn_reason="wrong asset")
    assert _state(store, obs.observation_id) is ObservationState.triaged

    second = _patch("a join")
    again = store.draft(second, observations=[obs.observation_id])

    assert again.addressed == (obs.observation_id,)
    assert again.not_addressed == ()
    assert _state(store, obs.observation_id) is ObservationState.addressed


def test_the_withdrawal_is_refused_before_anything_moves(tmp_path: Path) -> None:
    """A patch already ``withdrawn`` has no edge to ``withdrawn``, and the refusal must arrive
    before any observation is touched -- otherwise a second withdraw call reopens a row whose live
    patch is somebody else's."""
    store = _store(tmp_path)
    obs = _triaged(store)
    first, second = _patch("a synonym"), _patch("a join")
    store.draft(first, observations=[obs.observation_id])
    store.draft(second, observations=[obs.observation_id])
    store.move_patch(first.patch_id, to=PatchState.withdrawn, withdrawn_reason="superseded")

    with pytest.raises(Exception, match="not a declared transition"):
        store.move_patch(first.patch_id, to=PatchState.withdrawn, withdrawn_reason="again")

    assert _state(store, obs.observation_id) is ObservationState.addressed
