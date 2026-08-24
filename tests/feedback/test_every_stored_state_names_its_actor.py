"""ADR 0015 §3's rule, made mechanical: a state is stored only if a named actor moves it.

The rule was not derived on paper. A throwaway prototype of the lifecycle could not write seven
transitions without inventing an answer, and four of those seven were the same mistake — a stored
state with nobody to move it. The engine already ships one: ``ServeState.raised`` rows carry
``open: true`` under a comment reading "until a later closer exists", and there is no later closer.

So the table is data and these tests walk it. They fail on a *new* edge with no actor as loudly as
on a state nothing reaches, because both are that defect arriving by a different door.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.feedback.events import (
    TERMINAL_OBSERVATION_STATES,
    DerivedState,
    Kind,
    Observation,
    ObservationState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.lifecycle import (
    PATCH_TRANSITIONS,
    TRANSITIONS,
    Actor,
    TransitionRefused,
    allowed_next,
    is_open,
    transition_for,
)
from governed_bi.feedback.store import (
    FeedbackStore,
    Rejected,
    mint_observation_id,
    mint_patch_id,
    utc_now,
)
from governed_bi.register.assets import AssetType


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


def _drafted(store: FeedbackStore, obs: Observation) -> Patch:
    patch = Patch(
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
    store.draft(patch, observations=[obs.observation_id])
    return patch


def test_every_declared_edge_carries_an_actor() -> None:
    """The whole rule, over both tables, in one assertion each way."""
    for name, table in (("TRANSITIONS", TRANSITIONS), ("PATCH_TRANSITIONS", PATCH_TRANSITIONS)):
        for edge, transition in table.items():
            assert transition.moved_by, f"{name}{edge} has no actor"
            assert isinstance(transition.moved_by, Actor), (
                f"{name}{edge}'s actor is {transition.moved_by!r}, not an Actor member. A free "
                "string is how 'system' gets written into the one field that must name a person."
            )


def test_no_actor_is_a_machine() -> None:
    """There is deliberately no ``system`` actor.

    An automated mover is a stored state nobody moves wearing a name, which is the whole defect.
    The importer is not an exception: it takes the *opening* edge, whose actor is ``filer``,
    because filing is a thing that happened rather than a decision that was taken.
    """
    assert {a.value for a in Actor} == {"filer", "steward", "engineer"}


def test_every_state_in_the_vocabulary_is_reachable() -> None:
    """A state nothing reaches is a state that exists only in an enum."""
    reachable = {to for (_, to) in TRANSITIONS}
    assert reachable == set(ObservationState), (
        f"unreachable: {sorted(s.value for s in set(ObservationState) - reachable)}"
    )
    patch_reachable = {to for (_, to) in PATCH_TRANSITIONS}
    assert patch_reachable == set(PatchState)


def test_only_the_filer_opens_and_only_the_steward_decides() -> None:
    opening = [edge for edge in TRANSITIONS if edge[0] is None]
    assert opening == [(None, ObservationState.open)]
    assert TRANSITIONS[(None, ObservationState.open)].moved_by is Actor.filer

    for (frm, to), transition in TRANSITIONS.items():
        if frm is not None:
            assert transition.moved_by is Actor.steward, (
                f"{frm.value} -> {to.value} is moved by {transition.moved_by.value}; every "
                "decision about an observation is the steward's"
            )


def test_the_engineer_only_appears_where_a_bundle_is_produced() -> None:
    """The one edge that corresponds to an action outside this repository."""
    engineer_edges = [
        edge for edge, t in PATCH_TRANSITIONS.items() if t.moved_by is Actor.engineer
    ]
    assert engineer_edges == [(PatchState.draft, PatchState.exported)]


def test_a_declined_observation_cannot_be_reopened() -> None:
    """Re-opening is a *new* observation, because the evidence is attached to the turn that
    produced it and a second look wants a second turn."""
    assert allowed_next(ObservationState.declined) == frozenset()
    with pytest.raises(TransitionRefused, match="not a declared transition"):
        transition_for(ObservationState.declined, ObservationState.triaged)


def test_a_duplicate_is_terminal_and_addressed_is_not() -> None:
    """``addressed`` stays open on purpose: a patch exists, and until it lands nothing the filer
    cared about has changed. A store that closed the row here would be claiming the fix."""
    assert TERMINAL_OBSERVATION_STATES == {
        ObservationState.declined,
        ObservationState.duplicate,
    }
    assert is_open(ObservationState.addressed) is True
    assert is_open(ObservationState.blocked_on_a_person) is True
    assert is_open(ObservationState.declined) is False


def test_open_is_computed_from_the_terminal_set_and_not_declared_twice() -> None:
    """One source for "is this still somebody's problem", so the two cannot disagree."""
    for state in ObservationState:
        assert is_open(state) == (state not in TERMINAL_OBSERVATION_STATES)


def test_the_refusal_names_what_was_possible_instead() -> None:
    """A refusal that does not say what *is* allowed makes the caller guess."""
    with pytest.raises(TransitionRefused) as caught:
        transition_for(ObservationState.open, ObservationState.addressed)
    message = str(caught.value)
    assert "triaged" in message, message
    assert "not a declared transition" in message


def test_no_derived_state_is_an_observation_state() -> None:
    """The two vocabularies must not overlap: a landing state is recomputed on every read, and a
    stored copy of one is a second answer to "did this land" able to disagree with the first."""
    stored = {s.value for s in ObservationState} | {s.value for s in PatchState}
    derived = {s.value for s in DerivedState}
    assert stored.isdisjoint(derived), f"overlap: {sorted(stored & derived)}"


# ─────────────────────────────────────────────────────────────────────────────
# `requires` says the store checks it. For four edges the store did not.
# ─────────────────────────────────────────────────────────────────────────────


def test_addressed_needs_a_patch_behind_it(tmp_path: Path) -> None:
    """`triaged -> addressed` requires "at least one patch is draft or exported".

    `addressed` is the terminal "this was answered" state and it was reachable with nothing behind
    it, so the queue could report work done that has no artifact -- and `derived_state` then answers
    "did this land" about a patch that does not exist.
    """
    store = FeedbackStore(tmp_path / "f.sqlite")
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)
    with pytest.raises(Rejected, match="patch"):
        store.move(obs.observation_id, to=ObservationState.addressed)


def test_reopening_needs_every_patch_withdrawn(tmp_path: Path) -> None:
    """`addressed -> triaged` requires "every patch for it was withdrawn".

    Reopening while a patch is live leaves a row in the queue whose patch still derives a landing
    state, so the same observation reads as both open work and answered work.

    **There is no `move(to=addressed)` here any more, and its absence is the point.** This test
    used to draft the patch and then move the row itself, which is how the state came to have no
    producer outside a test: `store.draft` is the mover now, so an explicit move after it would be
    `addressed -> addressed`, an edge the table does not declare.
    """
    store = FeedbackStore(tmp_path / "f.sqlite")
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)
    patch = _drafted(store, obs)
    assert store.get(obs.observation_id).state is ObservationState.addressed  # type: ignore[union-attr]

    with pytest.raises(Rejected, match="withdrawn"):
        store.move(obs.observation_id, to=ObservationState.triaged)

    store.move_patch(patch.patch_id, to=PatchState.withdrawn, withdrawn_reason="wrong asset")
    store.move(obs.observation_id, to=ObservationState.triaged), "and now it reopens"


def test_exporting_needs_the_hash_the_edge_names(tmp_path: Path) -> None:
    """`draft -> exported` requires "a bundle was written, so expected_corpus_content_hash is set".

    Until the exporter recorded that hash there was nothing to enforce -- the field was always
    `None`. Now that it is set on every real export, an `exported` patch without it means something
    moved the state without writing a bundle, and `derived_state` would answer `landed_matched` at
    best for a handoff that never happened.
    """
    store = FeedbackStore(tmp_path / "f.sqlite")
    obs = _filed(store)
    store.move(obs.observation_id, to=ObservationState.triaged)
    patch = _drafted(store, obs)

    with pytest.raises(Rejected, match="expected_corpus_content_hash"):
        store.move_patch(patch.patch_id, to=PatchState.exported, detail="no bundle")

    store.move_patch(
        patch.patch_id,
        to=PatchState.exported,
        detail="bundle at bnd-x",
        expected_corpus_content_hash="d" * 64,
    )


def test_a_duplicate_joins_the_patch_set_of_the_row_it_duplicates(tmp_path: Path) -> None:
    """The second half of `triaged -> duplicate`'s requirement, which names its own consequence.

    "`duplicate_of` names another observation, **and this one joins that one's patch set** --
    otherwise a landing counts one affected observation instead of two." The naming half was
    enforced; the joining half was not, so every deduplicated complaint silently stopped counting.
    """
    store = FeedbackStore(tmp_path / "f.sqlite")
    original, dupe = _filed(store), _filed(store)
    for row in (original, dupe):
        store.move(row.observation_id, to=ObservationState.triaged)
    patch = _drafted(store, original)

    store.move(dupe.observation_id, to=ObservationState.duplicate, duplicate_of=original.observation_id)

    attached = {o.observation_id for o in store.observations_of(patch.patch_id)}
    assert attached == {original.observation_id, dupe.observation_id}, (
        f"the patch answers {len(attached)} observation(s) and the duplicate is not among them"
    )
