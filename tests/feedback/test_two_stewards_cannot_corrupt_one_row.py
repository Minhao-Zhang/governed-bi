"""Two concurrent moves must not land a row on an edge the table does not declare.

**The defect this pins, reproduced through HTTP before it was fixed.** ``store.move`` read the
current state *outside* the transaction and its ``UPDATE`` carried no ``WHERE state = ?``, so two
stewards moving one row at the same moment both won:

```
row at `triaged`
B: triaged -> declined  (with a decline_reason)
A: triaged -> addressed
final: state=addressed, decline_reason=NULL
history: [... (triaged -> declined), (triaged -> addressed)]
```

Three things are wrong there and each is worth its own assertion. The row **passed through**
``declined``, which is terminal — ``allowed_next(declined)`` is empty, so ``declined -> addressed``
is an edge the transition table refuses. The ``decline_reason`` the validator makes mandatory on a
declined row was **silently nulled** on the way past. And the append-only audit trail stopped
chaining: two rows both claim ``from_state='triaged'``, so nothing downstream can reconstruct the
order the row actually moved in.

**The route is reachable without privilege.** The FastAPI handlers are sync ``def``, so Starlette
runs them on the anyio threadpool concurrently, and ``FeedbackStore`` opens a fresh connection per
call. A reviewer reproduced two ``200``s from ``POST /observations/{id}/triage`` in 14 of 60 trials
with a two-thread barrier, and 26 of 40 in a second run.

**These tests use threads and a barrier rather than the HTTP client**, because the window is in the
store and a test that went through the app would be slower, flakier, and would still be testing the
store. The HTTP layer's own concurrency is asserted in ``tests/api/``.
"""

from __future__ import annotations

import threading
from pathlib import Path

from governed_bi.feedback.events import (
    TERMINAL_OBSERVATION_STATES,
    DeclineReason,
    Kind,
    Observation,
    ObservationState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.lifecycle import allowed_next, patch_allowed_next
from governed_bi.feedback.store import (
    FeedbackStore,
    Rejected,
    mint_observation_id,
    mint_patch_id,
    utc_now,
)
from governed_bi.feedback.validate import CONTENT_HASH_CHARS

#: Enough attempts that the interleaving is reached, few enough that the file stays fast. The
#: original reproduction hit it in roughly a quarter to a half of trials, so 40 is a wide margin --
#: and the assertion is on the INVARIANT, not on winning the race, so a run that never interleaves
#: passes honestly rather than falsely.
ATTEMPTS = 40


def _triaged(store: FeedbackStore) -> str:
    """A ``triaged`` row **with a live patch attached**, which is what makes the race a race.

    Both halves of that are load-bearing and the second one was missing. ``triaged -> addressed``
    requires at least one patch in ``draft`` or ``exported``, so once that clause was enforced the
    ``_address`` thread was refused on every attempt and the "both moves succeeded" count below ran
    at **0 of 40** while its own docstring claimed 22. The print is not an assertion, so nothing went
    red: the file kept passing as a single-writer test.

    The patch is drafted while the row is still ``open`` on purpose. ``store.draft`` moves an
    observation it *can* move to ``addressed``, and ``open`` has no such edge -- so this attaches the
    patch without spending the state the race is about.
    """
    observation = Observation(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.operator,
        kind=Kind.wrong_answer,
        state=ObservationState.open,
        question="how many active customers did we add last month?",
        turn_id="turn-1",
    )
    store.file(observation)
    drafted = store.draft(
        Patch(
            patch_id=mint_patch_id(),
            created_at=utc_now(),
            author=Source.operator,
            intent=PatchIntent.engine_defect,
            state=PatchState.draft,
            namespace="sales",
            rationale="nothing in the corpus is at fault",
            base_corpus_content_hash="a" * CONTENT_HASH_CHARS,
        ),
        observations=[observation.observation_id],
    )
    assert drafted.addressed == (), "the row is `open`, so the draft must not have moved it"
    store.move(observation.observation_id, to=ObservationState.triaged)
    return observation.observation_id


def _race(store: FeedbackStore, observation_id: str) -> list[BaseException | None]:
    """Fire two declared moves from ``triaged`` at once. Both are legal alone; only one may win."""
    barrier = threading.Barrier(2)
    outcomes: list[BaseException | None] = [None, None]

    def _decline() -> None:
        barrier.wait()
        try:
            store.move(
                observation_id,
                to=ObservationState.declined,
                decline_reason=DeclineReason.out_of_scope,
            )
        except BaseException as err:  # noqa: BLE001 - the refusal is the expected outcome
            outcomes[0] = err

    def _address() -> None:
        barrier.wait()
        try:
            store.move(observation_id, to=ObservationState.addressed)
        except BaseException as err:  # noqa: BLE001
            outcomes[1] = err

    threads = [threading.Thread(target=_decline), threading.Thread(target=_address)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


def test_two_concurrent_moves_never_land_the_row_on_an_undeclared_edge(tmp_path: Path) -> None:
    """The invariant, asserted on the audit trail rather than on who won.

    Whichever move lands, the history must be a **chain**: every row's ``from_state`` is the
    previous row's ``to_state``. That is the property the append-only trail is for, and it is the
    one the lost update broke.
    """
    store = FeedbackStore(tmp_path / "feedback.sqlite")

    for attempt in range(ATTEMPTS):
        observation_id = _triaged(store)
        _race(store, observation_id)

        history = store.history(observation_id)
        states = [(row["from_state"], row["to_state"]) for row in history]
        for (_, landed), (previous, _) in zip(states, states[1:]):
            assert landed == previous, (
                f"attempt {attempt}: the audit trail does not chain -- {states}. Two moves both "
                "wrote from the same state, so the row took an edge nothing declared."
            )


def test_a_row_that_reaches_a_terminal_state_stays_there(tmp_path: Path) -> None:
    """``declined`` and ``duplicate`` have no declared exits. A concurrent move must not walk out
    of one — that is the unclosable-row defect in reverse: a *closed* row silently reopening."""
    store = FeedbackStore(tmp_path / "feedback.sqlite")

    for attempt in range(ATTEMPTS):
        observation_id = _triaged(store)
        _race(store, observation_id)

        history = store.history(observation_id)
        reached = [row["to_state"] for row in history]
        terminal = {s.value for s in TERMINAL_OBSERVATION_STATES}
        for index, state in enumerate(reached):
            if state in terminal:
                assert index == len(reached) - 1, (
                    f"attempt {attempt}: the row moved on from the terminal state {state!r} -- "
                    f"{reached}. allowed_next says there is no such edge."
                )


def test_the_surviving_row_still_carries_what_its_state_requires(tmp_path: Path) -> None:
    """The field the validator makes mandatory must not be nulled by the *other* writer.

    ``move`` sets ``decline_reason=None`` whenever the target is not ``declined``, so the losing
    writer's target decided a field on the winner's row.
    """
    store = FeedbackStore(tmp_path / "feedback.sqlite")

    for attempt in range(ATTEMPTS):
        observation_id = _triaged(store)
        _race(store, observation_id)

        row = store.get(observation_id)
        assert row is not None
        if row.state is ObservationState.declined:
            assert row.decline_reason is not None, (
                f"attempt {attempt}: a declined row lost its reason. The reason IS the "
                "notification -- a declined row without one is a row nobody can explain."
            )


def test_when_both_concurrent_moves_succeed_they_form_a_declared_chain(tmp_path: Path) -> None:
    """**Both moves succeeding is legitimate**, and asserting otherwise was my own error.

    ``addressed -> declined`` is a declared edge, so if the serialisation orders the pair that way
    the row walks ``triaged -> addressed -> declined`` and both callers are right. 22 of 40
    attempts do exactly that.

    What must never happen is two moves from the *same* state. That is what the lost update
    produced -- ``triaged -> declined`` and ``triaged -> addressed`` -- and it is not reachable by
    any sequence of declared edges, which is why the trail stopped chaining. So the assertion is on
    the **path**, not on the count of winners.
    """
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    both_won = 0

    for attempt in range(ATTEMPTS):
        observation_id = _triaged(store)
        outcomes = _race(store, observation_id)
        if all(outcome is None for outcome in outcomes):
            both_won += 1
            history = store.history(observation_id)
            walked = [(row["from_state"], row["to_state"]) for row in history]
            for frm, to in walked:
                if frm is None:
                    continue
                assert to in {s.value for s in allowed_next(ObservationState(frm))}, (
                    f"attempt {attempt}: both moves succeeded and the row walked {walked}, which "
                    f"includes the undeclared edge {frm} -> {to}"
                )

    # Reported rather than asserted: how often the two interleave is a property of the machine, and
    # a run where they never do is not a failure. Zero would mean the test proved nothing.
    print(f"both moves succeeded on a declared chain in {both_won} of {ATTEMPTS} attempts")


def test_two_concurrent_patch_moves_do_not_both_win(tmp_path: Path) -> None:
    """``move_patch`` has the same shape as ``move`` and the same hole. ``exported`` and
    ``withdrawn`` are both reachable from ``draft``, and ``exported -> withdrawn`` is declared
    while ``withdrawn -> exported`` is not — so a lost update here can un-withdraw a patch."""
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    both_won = 0

    for _ in range(ATTEMPTS):
        patch = Patch(
            patch_id=mint_patch_id(),
            created_at=utc_now(),
            author=Source.operator,
            intent=PatchIntent.engine_defect,
            state=PatchState.draft,
            namespace="sales",
            rationale="nothing in the corpus is at fault",
            base_corpus_content_hash="a" * CONTENT_HASH_CHARS,
        )
        store.draft(patch, observations=[])

        barrier = threading.Barrier(2)
        outcomes: list[BaseException | None] = [None, None]

        def _export(patch_id: str = patch.patch_id) -> None:
            barrier.wait()
            try:
                store.move_patch(patch_id, to=PatchState.exported)
            except BaseException as err:  # noqa: BLE001
                outcomes[0] = err

        def _withdraw(patch_id: str = patch.patch_id) -> None:
            barrier.wait()
            try:
                store.move_patch(
                    patch_id, to=PatchState.withdrawn, withdrawn_reason="superseded"
                )
            except BaseException as err:  # noqa: BLE001
                outcomes[1] = err

        threads = [threading.Thread(target=_export), threading.Thread(target=_withdraw)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        if all(outcome is None for outcome in outcomes):
            both_won += 1

        history = store.history(patch.patch_id)
        states = [(row["from_state"], row["to_state"]) for row in history]
        for (_, landed), (previous, _) in zip(states, states[1:]):
            assert landed == previous, f"the patch's audit trail does not chain -- {states}"
        for frm, to in states:
            if frm is None:
                continue
            assert to in {s.value for s in patch_allowed_next(PatchState(frm))}, (
                f"the patch walked the undeclared edge {frm} -> {to} -- {states}"
            )

    # `draft -> exported -> withdrawn` is a declared path, so both succeeding is correct. What the
    # lost update produced was `draft -> exported` and `draft -> withdrawn`, which is not.
    print(f"both patch moves succeeded on a declared chain in {both_won} of {ATTEMPTS} attempts")


def test_a_declared_move_still_works_when_nothing_is_racing_it(tmp_path: Path) -> None:
    """The control. A fix that serialises by refusing everything would pass every test above."""
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    observation_id = _triaged(store)

    store.move(
        observation_id,
        to=ObservationState.declined,
        decline_reason=DeclineReason.working_as_intended,
    )
    row = store.get(observation_id)
    assert row is not None
    assert row.state is ObservationState.declined
    assert row.decline_reason is DeclineReason.working_as_intended
    assert allowed_next(row.state) == frozenset()

    try:
        store.move(observation_id, to=ObservationState.addressed)
    except Exception:  # noqa: BLE001 - a terminal row has no exits, which is the point
        pass
    else:  # pragma: no cover - would be a transition-table defect, not a race
        raise AssertionError("a terminal row moved on with nothing racing it")

    assert store.get(observation_id).state is ObservationState.declined  # type: ignore[union-attr]


def test_a_rejected_concurrent_move_leaves_no_audit_line(tmp_path: Path) -> None:
    """A refused writer must not append a transition row for a move that did not happen. The
    trail is the record of what moved, not of what was attempted."""
    store = FeedbackStore(tmp_path / "feedback.sqlite")

    for _ in range(ATTEMPTS):
        observation_id = _triaged(store)
        _race(store, observation_id)

        history = store.history(observation_id)
        row = store.get(observation_id)
        assert row is not None
        assert history[-1]["to_state"] == row.state.value, (
            "the last audit line disagrees with the row's state, so a refused move was recorded"
        )


def _unused() -> None:  # pragma: no cover - keeps the Rejected import honest for linters
    raise Rejected("unused", [])
