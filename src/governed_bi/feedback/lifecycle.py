"""Which states are stored, who moves them, and which are computed on read (ADR 0015 §3).

**The rule: a state is stored if and only if a named actor moves it. Everything else is derived at
read time.** It was found by building the state machine rather than by arguing about it — a
throwaway prototype could not write seven transitions without inventing an answer, and four of the
seven were the same mistake: a stored state nobody moves. The unclosable ``open: true`` row this
design replaces is exactly that.

So this module has two halves that do not resemble each other. :data:`TRANSITIONS` is a table with
an **actor on every edge**, and ``tests/feedback/`` walks it and fails on an edge whose actor is
empty. :func:`derived_state` stores nothing at all: it takes the corpus the session actually
loaded and answers "did this land" from scratch, because a stored copy of that answer is a second
answer able to disagree with the first.

Pure functions over the vocabulary in :mod:`.events`. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from governed_bi.feedback.events import (
    TERMINAL_OBSERVATION_STATES,
    DerivedState,
    ObservationState,
    Patch,
    PatchState,
)

__all__ = [
    "Actor",
    "Transition",
    "TRANSITIONS",
    "PATCH_TRANSITIONS",
    "is_open",
    "allowed_next",
    "transition_for",
    "derived_state",
    "TransitionRefused",
]


class TransitionRefused(ValueError):
    """A move the table does not declare. Raised rather than returned: an undeclared transition is
    a caller bug, and returning ``False`` for it is how a queue acquires a row in a state nothing
    can move on from."""


class Actor(str, Enum):
    """Who moves a state. Three roles, and on this deployment one person holds all three.

    Named as roles anyway, and not collapsed into "the operator", because the *role* is what
    explains the edge: a filer cannot decline their own observation and an engineer does not
    triage. The day there is a second identity, the table already says which edges each may take.
    There is no ``system`` actor — that would be the stored state with no actor this module exists
    to make unrepresentable.
    """

    #: Whoever or whatever filed it. The only actor on the opening edge.
    filer = "filer"
    #: Reads the evidence and decides. Every edge out of ``open`` is theirs.
    steward = "steward"
    #: Produces the bundle and commits it in the corpus repository.
    engineer = "engineer"


@dataclass(frozen=True, slots=True)
class Transition:
    """One declared edge. ``requires`` is prose for the reviewer, not a predicate.

    ``requires`` is checked by the store rather than here, because the checks read fields
    (``decline_reason``, ``duplicate_of``) and this module is deliberately a table.
    """

    moved_by: Actor
    requires: str = ""


#: Observation edges. ``None`` is "did not exist yet", so the opening edge is declared like any
#: other and the filer is on it. **Every value carries an actor** — that is the invariant a test
#: walks this table to assert.
TRANSITIONS: Mapping[tuple[ObservationState | None, ObservationState], Transition] = {
    (None, ObservationState.open): Transition(
        Actor.filer, "the turn exists and has finished"
    ),
    (ObservationState.open, ObservationState.triaged): Transition(Actor.steward),
    (ObservationState.triaged, ObservationState.declined): Transition(
        Actor.steward, "decline_reason is set"
    ),
    (ObservationState.triaged, ObservationState.duplicate): Transition(
        Actor.steward,
        "duplicate_of names another observation, and this one joins that one's patch set -- "
        "otherwise a landing counts one affected observation instead of two",
    ),
    (ObservationState.triaged, ObservationState.addressed): Transition(
        Actor.steward, "at least one patch is draft or exported"
    ),
    (ObservationState.triaged, ObservationState.blocked_on_a_person): Transition(
        Actor.steward, "blocked_note is set"
    ),
    # Out of the block, back to wherever the answer takes it. Not to `open`: somebody has looked.
    (ObservationState.blocked_on_a_person, ObservationState.triaged): Transition(Actor.steward),
    (ObservationState.blocked_on_a_person, ObservationState.declined): Transition(
        Actor.steward, "decline_reason is set"
    ),
    (ObservationState.blocked_on_a_person, ObservationState.addressed): Transition(
        Actor.steward, "at least one patch is draft or exported"
    ),
    # `addressed` is not terminal, because a patch can be withdrawn and then there is nothing
    # addressing it. Back to `triaged`, which is true: somebody has looked.
    (ObservationState.addressed, ObservationState.triaged): Transition(
        Actor.steward, "every patch for it was withdrawn"
    ),
    (ObservationState.addressed, ObservationState.declined): Transition(
        Actor.steward, "decline_reason is set"
    ),
}

#: Patch edges. ``exported`` is terminal from this store's point of view: what happens next
#: happens in a git repository this process cannot write to, and :func:`derived_state` reads it
#: back out of the corpus rather than being told.
PATCH_TRANSITIONS: Mapping[tuple[PatchState | None, PatchState], Transition] = {
    (None, PatchState.draft): Transition(Actor.steward),
    (PatchState.draft, PatchState.exported): Transition(
        Actor.engineer, "a bundle was written, so expected_corpus_content_hash is set"
    ),
    (PatchState.draft, PatchState.withdrawn): Transition(
        Actor.steward, "withdrawn_reason is set"
    ),
    (PatchState.exported, PatchState.withdrawn): Transition(
        Actor.steward, "withdrawn_reason is set"
    ),
}


def is_open(state: ObservationState) -> bool:
    """Whether an observation still wants somebody's attention. **Computed, never stored.**

    ``TERMINAL_OBSERVATION_STATES`` is the stored half of the answer and this is its complement,
    so the two cannot disagree. ``addressed`` is *open*: a patch exists, and until it lands the
    thing the filer cared about has not changed.
    """
    return state not in TERMINAL_OBSERVATION_STATES


def allowed_next(state: ObservationState | None) -> frozenset[ObservationState]:
    """States reachable from ``state`` in one declared move. Empty is a real answer."""
    return frozenset(to for (frm, to) in TRANSITIONS if frm == state)


def transition_for(
    state: ObservationState | None, to: ObservationState
) -> Transition:
    """The declared edge, or raise. The store calls this before it writes anything."""
    edge = TRANSITIONS.get((state, to))
    if edge is None:
        reachable = sorted(s.value for s in allowed_next(state))
        frm = "nothing" if state is None else state.value
        raise TransitionRefused(
            f"{frm} -> {to.value} is not a declared transition; from {frm} the declared moves are "
            f"{reachable or 'none'}. A move the table does not declare is a caller bug, and "
            "allowing it is how a queue acquires a row nothing can move on from."
        )
    return edge


def derived_state(
    patch: Patch,
    *,
    loaded_corpus_hash: str,
    asset_text_now: Mapping[str, tuple[str, str]],
    retrieval_ok: bool | None = None,
) -> DerivedState:
    """Did this patch land? Answered from the corpus the session loaded, and stored nowhere.

    ``asset_text_now`` maps an asset id to its ``(summary, body)`` in the loaded corpus; an id
    absent from it is an asset absent from the corpus. ``retrieval_ok`` is the affected question's
    retrieval fixture: ``True`` upgrades a landing to :attr:`DerivedState.retrieval_verified`,
    ``None`` means nobody re-ran it, and ``False`` leaves the landing as it is — a fixture that
    fails does not un-land a commit, it means the patch landed and did not do what it claimed.

    The order of the checks is the whole content. Exact-hash first, because it is the only
    unambiguous answer. Then **content matching**, which is the common real case and the one a
    two-state model silently mislabels: two bundles landing in one week make the exact hash fail
    for a change that did ship. Only then ``superseded``, which is a conflict, a CI reformat, or a
    reviewer editing before committing — all three normal, and all three read as "handed off,
    forever" without this state.
    """
    if loaded_corpus_hash == patch.base_corpus_content_hash:
        return DerivedState.handed_off

    expected = patch.expected_corpus_content_hash
    if expected and loaded_corpus_hash == expected:
        return _with_retrieval(DerivedState.landed_verified, retrieval_ok)

    if _content_is_there(patch, asset_text_now):
        return _with_retrieval(DerivedState.landed_matched, retrieval_ok)

    return DerivedState.superseded


def _with_retrieval(landed: DerivedState, retrieval_ok: bool | None) -> DerivedState:
    """Upgrade a landing to ``retrieval_verified`` only on a fixture that actually passed.

    ``None`` and ``False`` both return the landing unchanged, and they mean different things that
    this function is deliberately not the place to distinguish: the caller has the fixture result
    and the record has room for it.
    """
    return DerivedState.retrieval_verified if retrieval_ok is True else landed


def _content_is_there(patch: Patch, asset_text_now: Mapping[str, tuple[str, str]]) -> bool:
    """Whether the asset the patch touched carries the text the patch expected.

    Only ``summary`` and ``body`` are compared, and that is a stated limit rather than an
    oversight: they are the two fields the review surface renders and the two a patch in this cut
    may edit. A patch that changed ``reliability.status`` and nothing else lands as
    ``superseded`` here, which is wrong — and is why an edit outside those two fields is refused
    upstream rather than mis-derived here.
    """
    if patch.asset_id is None or patch.becomes is None:
        return False
    now = asset_text_now.get(patch.asset_id)
    if now is None:
        return False
    summary, body = now
    if patch.field_path == "summary":
        return summary == patch.becomes
    if patch.field_path == "body":
        return body == patch.becomes
    return False


def _assert_every_declared_edge_names_an_actor() -> None:
    """The rule of ADR 0015 §3, made mechanical at import.

    A test walks the same table, and this guard is here as well because the table is data: an
    edge added in a hurry with no actor is a stored state nobody moves, which is the defect the
    whole module exists to prevent, and it should not wait for a test run to be caught.
    """
    for table, name in ((TRANSITIONS, "TRANSITIONS"), (PATCH_TRANSITIONS, "PATCH_TRANSITIONS")):
        for edge, transition in table.items():
            if not transition.moved_by:  # pragma: no cover - import-time guard
                raise AssertionError(
                    f"{name}{edge} declares no actor. A stored state nobody moves is the "
                    "unclosable `open: true` row this design replaces."
                )
    unreachable = {
        state
        for state in ObservationState
        if state is not ObservationState.open
        and not any(to is state for (_, to) in TRANSITIONS)
    }
    if unreachable:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"no declared transition reaches {sorted(s.value for s in unreachable)}, so the state "
            "exists in the vocabulary and nothing can ever put a row in it."
        )


_assert_every_declared_edge_names_an_actor()
