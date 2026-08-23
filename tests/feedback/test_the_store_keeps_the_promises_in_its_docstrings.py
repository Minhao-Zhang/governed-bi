"""Every claim ``feedback/store.py`` makes about itself, asserted.

**Why this file exists.** An adversarial review mutation-tested the store against the whole suite
and **nine deliberate breakages survived**: `_tx` reduced to plain autocommit, `truncated`
hard-coded `False`, both orderings reversed, `move_patch`'s validator deleted,
``PRAGMA foreign_keys = ON`` deleted, `amend_note`'s freeze gate disabled, and three field mappers
each dropping a field. Every one of those is a sentence in a docstring — the module says it is
atomic, says `truncated` is load-bearing, says the orderings are deliberate and why. None of it
was checked.

So each test below is named for the promise it pins, and each one was **verified to fail** against
the corresponding mutation before being committed. A test that cannot fail is what this file is a
response to; a test in it that stops being able to fail is a defect.

The one promise NOT pinned here is transaction isolation between two writers — that needs two
threads and lives in ``test_two_stewards_cannot_corrupt_one_row.py``, because it is a defect rather
than a promise.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields as dataclass_fields
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
    Rejected,
    mint_observation_id,
    mint_patch_id,
    utc_now,
)
from governed_bi.feedback.validate import CONTENT_HASH_CHARS
from governed_bi.register.assets import AssetType

HASH = "a" * CONTENT_HASH_CHARS


def _store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.sqlite")


def _observation(**over: object) -> Observation:
    base: dict[str, object] = dict(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.operator,
        kind=Kind.wrong_answer,
        state=ObservationState.open,
        question="how many active customers did we add last month?",
        turn_id="turn-1",
    )
    base.update(over)
    return Observation(**base)  # type: ignore[arg-type]


def _patch(**over: object) -> Patch:
    base: dict[str, object] = dict(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.edit_asset,
        state=PatchState.draft,
        namespace="sales",
        asset_type=AssetType.table,
        asset_id="sales.orders",
        field_path="summary",
        was="before",
        becomes="after",
        base_corpus_content_hash=HASH,
    )
    base.update(over)
    return Patch(**base)  # type: ignore[arg-type]


# ── "One transaction. A state change and its audit row land together or not at all." ──


def test_a_failed_write_leaves_neither_the_row_nor_its_audit_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_tx``'s whole claim, and the mutation that survived was `_tx` with no BEGIN at all.

    Forced at the seam between the two statements, because that is the only window where the
    two halves can disagree: the observation is inserted, then the transition row raises.
    """
    from governed_bi.feedback import store as store_module

    store = _store(tmp_path)
    observation = _observation()

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the audit line failed")

    monkeypatch.setattr(store_module, "_record_transition", _explode)
    with pytest.raises(RuntimeError):
        store.file(observation)

    assert store.get(observation.observation_id) is None, (
        "the observation survived a failed audit line, so the store is not atomic"
    )
    assert store.queue().total == 0
    assert store.history(observation.observation_id) == ()


def test_a_failed_draft_leaves_no_patch_and_no_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same property on the two-table write. `draft` inserts a patch AND its join rows."""
    from governed_bi.feedback import store as store_module

    store = _store(tmp_path)
    observation = _observation()
    store.file(observation)
    patch = _patch()

    monkeypatch.setattr(
        store_module,
        "_record_transition",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        store.draft(patch, observations=[observation.observation_id])

    assert store.get_patch(patch.patch_id) is None
    assert store.patches_of(observation.observation_id) == ()


# ── "`truncated` is load-bearing (ADR 0009)" ──────────────────────────────────


def _fill(store: FeedbackStore, n: int) -> None:
    for index in range(n):
        store.file(_observation(question=f"question number {index} about customers"))


def test_truncated_is_true_only_when_rows_are_behind_the_page(tmp_path: Path) -> None:
    """The claim: a caller that cannot tell a full page from the end of the queue stops at the
    first page and believes it saw everything. Hard-coding `False` survived the whole suite."""
    store = _store(tmp_path)
    _fill(store, 5)

    assert store.queue(limit=2, offset=0).truncated is True, "3 rows are still behind this page"
    assert store.queue(limit=2, offset=3).truncated is False, "this page reaches the end"
    assert store.queue(limit=5, offset=0).truncated is False, "one page holds everything"
    assert store.queue(limit=10, offset=0).truncated is False
    assert store.queue(limit=2, offset=5).truncated is False, "past the end is not truncated"


def test_truncated_is_true_on_a_patch_page_with_rows_behind_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for _ in range(4):
        store.draft(_patch(), observations=[])

    assert store.patches(limit=2).truncated is True
    assert store.patches(limit=2, offset=2).truncated is False
    assert store.patches(limit=4).truncated is False


# ── "Oldest-first ... a queue is read oldest-first" / "newest first" ──────────


def test_the_queue_is_oldest_first_and_the_patch_list_is_newest_first(tmp_path: Path) -> None:
    """Both orderings reversed survived the suite, and the asymmetry is deliberate: an
    observation queue is work waiting, a patch list is work done."""
    store = _store(tmp_path)
    first = _observation(question="the first question anybody asked about customers")
    second = _observation(question="the second question anybody asked about customers")
    store.file(first)
    store.file(second)

    ordered = [row.observation_id for row in store.queue().rows]
    assert ordered == [first.observation_id, second.observation_id], (
        "the queue is newest-first, so the row that has waited longest is invisible"
    )

    early = _patch()
    late = _patch()
    store.draft(early, observations=[])
    store.draft(late, observations=[])
    assert [p.patch_id for p in store.patches().rows] == [late.patch_id, early.patch_id]


def test_rows_filed_in_the_same_second_keep_insertion_order(tmp_path: Path) -> None:
    """`filed_at` is to the second and an id carries a random suffix, so an id tiebreak orders
    differently on every read — which is what the importer's 73 rows would do."""
    store = _store(tmp_path)
    stamp = utc_now()
    ids = [mint_observation_id() for _ in range(6)]
    for observation_id in ids:
        store.file(
            _observation(
                observation_id=observation_id,
                filed_at=stamp,
                question=f"a question filed at {stamp} about customers",
            )
        )

    assert [row.observation_id for row in store.queue().rows] == ids, (
        "rows filed in one second are not in insertion order, so the queue reorders per read"
    )


# ── "The new row is validated before it is written" ───────────────────────────


def test_move_patch_validates_the_row_it_is_about_to_write(tmp_path: Path) -> None:
    """Deleting `move_patch`'s `faults_with` call survived, because no test named `move_patch`."""
    store = _store(tmp_path)
    patch = _patch()
    store.draft(patch, observations=[])

    with pytest.raises(Rejected):
        store.move_patch(
            patch.patch_id,
            to=PatchState.exported,
            expected_corpus_content_hash="not-a-hash",
        )
    assert store.get_patch(patch.patch_id).state is PatchState.draft  # type: ignore[union-attr]


def test_move_validates_the_row_it_is_about_to_write(tmp_path: Path) -> None:
    """The observation half of the same promise: a `declined` row must carry a reason."""
    store = _store(tmp_path)
    observation = _observation()
    store.file(observation)
    store.move(observation.observation_id, to=ObservationState.triaged)

    with pytest.raises(Rejected):
        store.move(observation.observation_id, to=ObservationState.declined)
    assert store.get(observation.observation_id).state is ObservationState.triaged  # type: ignore[union-attr]


# ── foreign keys ─────────────────────────────────────────────────────────────


def test_a_patch_cannot_attach_to_an_observation_that_does_not_exist(tmp_path: Path) -> None:
    """Deleting `PRAGMA foreign_keys = ON` survived the suite. Without it the join table
    accumulates rows pointing at nothing, and `observations_of` silently returns fewer than the
    patch claims to answer."""
    store = _store(tmp_path)
    patch = _patch()

    with pytest.raises((Rejected, sqlite3.IntegrityError, KeyError)):
        store.draft(patch, observations=["obs-does-not-exist"])
    assert store.get_patch(patch.patch_id) is None, "the patch landed with a dangling attachment"


def test_a_duplicate_id_is_refused_rather_than_overwriting(tmp_path: Path) -> None:
    store = _store(tmp_path)
    observation = _observation()
    store.file(observation)

    with pytest.raises((Rejected, sqlite3.IntegrityError)):
        store.file(_observation(observation_id=observation.observation_id, question="a different q"))
    assert store.queue().total == 1


# ── "a note can only be amended while nobody has triaged it" ─────────────────


def test_a_note_freezes_once_somebody_has_triaged_the_row(tmp_path: Path) -> None:
    """Turning the gate into `if False` survived, because no test named `amend_note`. The reason
    it matters: a reviewer reading a row whose text changes underneath them is worse than a
    second observation."""
    store = _store(tmp_path)
    observation = _observation()
    store.file(observation)
    store.amend_note(observation.observation_id, "it is about 400, not 4102")
    assert store.get(observation.observation_id).note.startswith("it is about 400")  # type: ignore[union-attr]

    store.move(observation.observation_id, to=ObservationState.triaged)
    with pytest.raises(Rejected):
        store.amend_note(observation.observation_id, "too late")
    assert store.get(observation.observation_id).note.startswith("it is about 400")  # type: ignore[union-attr]


def test_amending_an_observation_that_does_not_exist_is_a_key_error(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        _store(tmp_path).amend_note("obs-nope", "anything")


# ── the field set, which is spelled out in four places ───────────────────────


def test_every_observation_field_survives_a_round_trip(tmp_path: Path) -> None:
    """Three mappers each dropping one field survived the suite, because nothing compared a row
    against itself. The field list lives in the dataclass, `_SCHEMA`, `_observation_row` and
    `_observation_from` — four places, and a silent loss in any one of them.
    """
    store = _store(tmp_path)
    observation = _observation(
        category=None,
        note="a note",
        thread_id="t-1",
        outcome="answered",
        refused_by=None,
        generated_sql="SELECT 1",
        licensed=("sales.orders",),
        schemas=("sales",),
        missing_tables=("sales.customers",),
        gold_sql="SELECT 2",
        gold_fingerprint="gold-1",
        pred_fingerprint="pred-1",
        quality_flags=("degenerate",),
        arm="v4",
        question_id="q-1",
        db_id="sales",
        git_sha="abc1234",
        prompt_set_hash="prompt-1",
        corpus_content_hash="c" * CONTENT_HASH_CHARS,
        external_key="key-1",
    )
    store.file(observation)

    back = store.get(observation.observation_id)
    assert back is not None
    lost = [
        f.name
        for f in dataclass_fields(observation)
        if getattr(back, f.name) != getattr(observation, f.name)
    ]
    assert lost == [], f"these fields did not survive the round trip: {lost}"


def test_every_patch_field_survives_a_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    patch = _patch(rationale="because the reference answer reads it", withdrawn_reason="")
    store.draft(patch, observations=[])
    store.record_ladder(patch.patch_id, "T0", {"passed": True, "detail": "fine"})

    back = store.get_patch(patch.patch_id)
    assert back is not None
    lost = [
        f.name
        for f in dataclass_fields(patch)
        if f.name != "ladder" and getattr(back, f.name) != getattr(patch, f.name)
    ]
    assert lost == [], f"these fields did not survive the round trip: {lost}"
    assert dict(back.ladder)["T0"]["passed"] is True
