"""``ObservationState.addressed`` had no producer, so the state was unreachable in the product.

``store.draft`` inserted the patch, recorded a **patch** transition, and never touched
``observation.state``. Nothing else claimed the job: ``ui/components/review/decision-bar.tsx``
omits the button and says the state "is set by drafting a patch", and
``handoff-panel.tsx`` calls ``draft`` and nothing else. So an observation with a live patch stayed
``triaged`` forever, and ``addressed -> triaged`` -- the edge that exists because a patch can be
withdrawn -- was declared for a state nothing could put a row in.

The store is the producer now. The two rules these tests pin:

* an observation the table can move to ``addressed`` **is** moved, in the same call that drafts the
  patch, with an audit row naming the patch;
* an observation the table **cannot** move -- ``open``, because ``-> addressed`` exists only from
  ``triaged`` and ``blocked_on_a_person`` -- is named in the return value. Not skipped silently:
  that is the defect family this branch spent two days removing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.feedback.events import (
    Kind,
    Observation,
    ObservationState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.store import (
    FeedbackStore,
    mint_observation_id,
    mint_patch_id,
    utc_now,
)
from governed_bi.register.assets import AssetType


def _store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.sqlite")


def _filed(store: FeedbackStore) -> Observation:
    obs = Observation(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.reader,
        kind=Kind.wrong_answer,
        state=ObservationState.open,
        note="the total is about 400, not 4102",
        question="how much revenue last month?",
        turn_id="turn-1",
        thread_id="t-1",
    )
    store.file(obs)
    return obs


def _patch() -> Patch:
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
        becomes="orders is the transaction table, one row per placed order.",
        base_corpus_content_hash="c" * 64,
        rationale="the reference answer reads this table and retrieval did not license it",
    )


def test_drafting_against_a_triaged_row_moves_it_to_addressed(tmp_path: Path) -> None:
    """The producer the UI already claims exists."""
    store = _store(tmp_path)
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)

    drafted = store.draft(_patch(), observations=[obs.observation_id])

    # The stored state first, deliberately: it is the defect, and asserting the return value first
    # would report the shape of the answer rather than the thing that was wrong.
    row = store.get(obs.observation_id)
    assert row is not None
    assert row.state is ObservationState.addressed, (
        "a live patch exists and the row still reads as work nobody has answered"
    )
    assert drafted.addressed == (obs.observation_id,)
    assert drafted.not_addressed == ()


def test_the_move_is_on_the_audit_trail_and_names_the_patch(tmp_path: Path) -> None:
    """A state change writes the row **and** its transition line. The steward moved it, and the
    detail says which patch, because "why is this addressed" is answerable from the trail alone."""
    store = _store(tmp_path)
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)
    patch = _patch()

    store.draft(patch, observations=[obs.observation_id])

    walked = [
        (row["from_state"], row["to_state"], row["moved_by"], row["detail"])
        for row in store.history(obs.observation_id)
    ]
    assert walked[-1][:3] == ("triaged", "addressed", "steward")
    assert patch.patch_id in walked[-1][3], f"the trail does not name the patch: {walked}"


def test_an_untriaged_row_is_reported_and_not_silently_skipped(tmp_path: Path) -> None:
    """``open -> addressed`` is not a declared edge, so the row stays ``open``.

    What must not happen is the *skip being invisible*. A caller that has to re-read the row to
    learn the draft did half of what it asked is the same defect as the state having no producer:
    the answer exists only for whoever thinks to look.
    """
    store = _store(tmp_path)
    obs = _filed(store)

    drafted = store.draft(_patch(), observations=[obs.observation_id])

    assert drafted.addressed == ()
    assert [unmoved.observation_id for unmoved in drafted.not_addressed] == [obs.observation_id]
    unmoved = drafted.not_addressed[0]
    assert unmoved.state is ObservationState.open
    assert "triaged" in unmoved.why, (
        f"the reason must name the states the move is declared from: {unmoved.why!r}"
    )
    row = store.get(obs.observation_id)
    assert row is not None
    assert row.state is ObservationState.open, "and nothing pretended otherwise"
    assert store.patches_of(obs.observation_id), "the patch still attached, which is the point"


def test_one_patch_addresses_every_row_it_can_and_names_the_rest(tmp_path: Path) -> None:
    """A patch answers several observations. Two are triaged, one is not, and the caller is told
    which is which in one answer rather than in three reads."""
    store = _store(tmp_path)
    first, second, untriaged = _filed(store), _filed(store), _filed(store)
    for row in (first, second):
        store.move(row.observation_id, to=ObservationState.triaged)

    drafted = store.draft(
        _patch(),
        observations=[first.observation_id, untriaged.observation_id, second.observation_id],
    )

    assert set(drafted.addressed) == {first.observation_id, second.observation_id}
    assert [u.observation_id for u in drafted.not_addressed] == [untriaged.observation_id]


def test_a_blocked_row_is_addressed_too(tmp_path: Path) -> None:
    """``blocked_on_a_person -> addressed`` is declared: the question came back and the answer is a
    patch. It is the second of the two states the move is legal from, and a producer that handled
    only ``triaged`` would leave it as dead as the state was."""
    store = _store(tmp_path)
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)
    store.move(
        obs.observation_id,
        to=ObservationState.blocked_on_a_person,
        blocked_note="asked finance which of the two revenue columns is the booked one",
    )

    drafted = store.draft(_patch(), observations=[obs.observation_id])

    assert drafted.addressed == (obs.observation_id,)
    assert store.get(obs.observation_id).state is ObservationState.addressed  # type: ignore[union-attr]


def test_the_reverse_edge_is_reachable_now_that_something_produces_addressed(
    tmp_path: Path,
) -> None:
    """``addressed -> triaged`` was declared for a state nothing produced, so it could never fire.

    It exists because a patch can be withdrawn and then nothing is addressing the row. With the
    draft as the producer, the whole loop is walkable: draft, withdraw, reopen.
    """
    store = _store(tmp_path)
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)
    patch = _patch()
    store.draft(patch, observations=[obs.observation_id])

    store.move_patch(
        patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="the gap is the router"
    )
    store.move(obs.observation_id, to=ObservationState.triaged)

    assert store.get(obs.observation_id).state is ObservationState.triaged  # type: ignore[union-attr]


def test_a_second_patch_on_an_addressed_row_is_reported_rather_than_refused(
    tmp_path: Path,
) -> None:
    """``addressed -> addressed`` is not an edge, and a second patch on the same row is normal --
    one failure can need a synonym *and* a join. The draft lands and the row is named as un-moved,
    because it is already in the state the draft would have moved it to."""
    store = _store(tmp_path)
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)
    store.draft(_patch(), observations=[obs.observation_id])

    second = _patch()
    drafted = store.draft(second, observations=[obs.observation_id])

    assert drafted.patch_id == second.patch_id
    assert drafted.addressed == ()
    assert [u.state for u in drafted.not_addressed] == [ObservationState.addressed]
    assert len(store.patches_of(obs.observation_id)) == 2


def test_a_patch_that_answers_nobody_moves_nothing(tmp_path: Path) -> None:
    """``observations`` may be empty -- a patch from a corpus audit answers no complaint -- and the
    result says so with two empty tuples rather than with a bare id nobody can interpret."""
    store = _store(tmp_path)
    drafted = store.draft(_patch(), observations=[])
    assert (drafted.addressed, drafted.not_addressed) == ((), ())


def test_the_same_observation_twice_is_one_move(tmp_path: Path) -> None:
    """A caller repeating an id must not make the second copy read as a refused move."""
    store = _store(tmp_path)
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)

    drafted = store.draft(
        _patch(), observations=[obs.observation_id, obs.observation_id]
    )

    assert drafted.addressed == (obs.observation_id,)
    assert drafted.not_addressed == ()


def test_nothing_is_written_when_the_patch_itself_is_refused(tmp_path: Path) -> None:
    """The move is a consequence of a stored patch. A refused patch moves nothing, so a row cannot
    read as addressed by a patch that does not exist."""
    from governed_bi.feedback.store import Rejected

    store = _store(tmp_path)
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)

    bad = Patch(
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
        becomes="orders is the transaction table.",  # identical: changes nothing
        base_corpus_content_hash="c" * 64,
    )
    with pytest.raises(Rejected):
        store.draft(bad, observations=[obs.observation_id])

    assert store.get(obs.observation_id).state is ObservationState.triaged  # type: ignore[union-attr]
    assert store.patches_of(obs.observation_id) == ()


# ── the clause that belonged to one edge and was applied to three ─────────────


def test_looking_at_a_row_that_already_has_a_patch_is_not_a_reopening(tmp_path: Path) -> None:
    """`addressed -> triaged` requires every patch withdrawn. That clause was checked on **every**
    move to `triaged`, so a row with a live patch could not be triaged at all.

    Drafting against an `open` row is the sequence that makes this reachable, and it is the sequence
    the queue produces: the steward reads a row, drafts the change, then says "I am looking at this"
    -- and got `422 reopening requires every patch withdrawn`, about a row nobody had reopened.
    """
    store = _store(tmp_path)
    obs = _filed(store)
    store.draft(_patch(), observations=[obs.observation_id])

    store.move(obs.observation_id, to=ObservationState.triaged)
    assert store.get(obs.observation_id).state is ObservationState.triaged  # type: ignore[union-attr]


def test_clearing_a_block_is_not_a_reopening_either(tmp_path: Path) -> None:
    """The same clause, the other edge it was wrongly applied to.

    ``blocked_on_a_person -> triaged`` is "the block cleared" and says nothing about patches. The
    row gets its patch while it is still ``open``, so it reaches the block with a live patch on it --
    which is the shape that was refused.
    """
    store = _store(tmp_path)
    obs = _filed(store)
    store.draft(_patch(), observations=[obs.observation_id])
    store.move(obs.observation_id, to=ObservationState.triaged)
    store.move(
        obs.observation_id,
        to=ObservationState.blocked_on_a_person,
        blocked_note="asked finance which revenue column is the booked one",
    )

    store.move(obs.observation_id, to=ObservationState.triaged)

    assert store.get(obs.observation_id).state is ObservationState.triaged  # type: ignore[union-attr]
    assert [p.state for p in store.patches_of(obs.observation_id)] == [PatchState.draft], (
        "and the patch is still live, which is what the clause was reading"
    )
