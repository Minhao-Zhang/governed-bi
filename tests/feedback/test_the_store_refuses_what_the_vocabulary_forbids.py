"""The refusals that keep a row from parsing, reaching a queue, and being sorted into a bucket
nobody meant.

Every case here is a rule with a named reason in ADR 0015, and each is asserted **through the
store** rather than only through the validator — a rule the validator knows and the store does not
call is a rule that is not in force, which is this repository's most-repeated defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.feedback.events import (
    Category,
    DeclineReason,
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
from governed_bi.feedback.validate import (
    CONTENT_HASH_CHARS,
    EDITABLE_FIELD_PATHS,
    NOTE_MAX_CHARS,
    faults_with,
)
from governed_bi.register.assets import AssetType


def _store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.sqlite")


def _filed(**over: object) -> Observation:
    base: dict[str, object] = dict(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.reader,
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
        namespace="beer_factory",
        asset_type=AssetType.term,
        asset_id="term_beer_factory_active",
        field_path="summary",
        was="before",
        becomes="after",
        base_corpus_content_hash="b" * CONTENT_HASH_CHARS,
    )
    base.update(over)
    return Patch(**base)  # type: ignore[arg-type]


def test_a_category_is_refused_on_a_card_it_cannot_apply_to(tmp_path: Path) -> None:
    """``false_refusal`` on a delivered answer is a contradiction, and the queue would sort it
    into the refusal bucket where nobody is looking for a wrong number."""
    with pytest.raises(Rejected, match="not declared for kind"):
        _store(tmp_path).file(_filed(category=Category.false_refusal))


def test_a_reader_may_not_name_a_column(tmp_path: Path) -> None:
    """The operator-only categories name an asset. A filer who cannot read the corpus cannot name
    one, and a wrong pick sends a reviewer to the wrong asset with a confident-looking pointer."""
    with pytest.raises(Rejected, match="operator-only"):
        _store(tmp_path).file(_filed(category=Category.column_excluded))


def test_an_agent_may_file_column_suspect_and_not_column_excluded(tmp_path: Path) -> None:
    """ADR 0005's split, exactly: ``Reliability.status`` is AI-authorable and
    ``Governance.excluded`` is "human-only, enforced by the absence of a tool"."""
    store = _store(tmp_path)
    store.file(_filed(source=Source.agent, category=Category.column_suspect))
    with pytest.raises(Rejected, match="operator-only"):
        store.file(_filed(source=Source.agent, category=Category.column_excluded))


def test_a_note_over_the_cap_names_the_cap(tmp_path: Path) -> None:
    """"Too long" without a number is not actionable."""
    with pytest.raises(Rejected, match=str(NOTE_MAX_CHARS)):
        _store(tmp_path).file(_filed(note="x" * (NOTE_MAX_CHARS + 1)))


def test_an_observation_with_no_question_is_refused(tmp_path: Path) -> None:
    """The importer joins question text from the dataset, and a missing join must raise rather
    than file a blank: a row that does not carry the question cannot be reviewed."""
    with pytest.raises(Rejected, match="question is empty"):
        _store(tmp_path).file(_filed(question=""))


def test_an_imported_observation_may_not_carry_a_synthesised_turn_id(tmp_path: Path) -> None:
    """Measured: an eval artifact carries no ``turn_id`` on any of 1,351 rows. An invented one
    would 404 on ``/audit/turns/{id}/trace``, so absence is the honest value."""
    with pytest.raises(Rejected, match="leave turn_id and thread_id unset"):
        _store(tmp_path).file(
            _filed(source=Source.eval, external_key="k", arm="v4", question_id="q1")
        )


def test_a_filed_observation_needs_the_turn_it_is_about(tmp_path: Path) -> None:
    with pytest.raises(Rejected, match="needs the turn_id"):
        _store(tmp_path).file(_filed(turn_id=None))


def test_an_observation_cannot_be_filed_into_a_later_state(tmp_path: Path) -> None:
    """The only opening edge in the table goes to ``open``. Filing straight into ``addressed``
    would skip the audit line that says who decided."""
    with pytest.raises(Rejected, match="only opening edge"):
        _store(tmp_path).file(_filed(state=ObservationState.addressed))


def test_a_decline_needs_its_reason_and_a_block_needs_its_note(tmp_path: Path) -> None:
    """The reason **is** the notification, and the block's note is the whole content of the state
    — there is nobody to escalate to, so the sentence is what a reader gets."""
    store = _store(tmp_path)
    oid = store.file(_filed())
    store.move(oid, to=ObservationState.triaged)

    with pytest.raises(Rejected, match="declined without a decline_reason"):
        store.move(oid, to=ObservationState.declined)
    with pytest.raises(Rejected, match="without a blocked_note"):
        store.move(oid, to=ObservationState.blocked_on_a_person)

    store.move(oid, to=ObservationState.declined, decline_reason=DeclineReason.working_as_intended)
    assert store.get(oid).decline_reason is DeclineReason.working_as_intended  # type: ignore[union-attr]


def test_dataset_defect_is_only_reachable_from_an_import(tmp_path: Path) -> None:
    """A filed observation has no dataset to be defective."""
    store = _store(tmp_path)
    oid = store.file(_filed())
    store.move(oid, to=ObservationState.triaged)
    with pytest.raises(Rejected, match="only reachable from an imported observation"):
        store.move(oid, to=ObservationState.declined, decline_reason=DeclineReason.dataset_defect)


def test_a_patch_may_not_author_a_standalone_column(tmp_path: Path) -> None:
    """The measured outage: the served corpus keeps columns inline and
    ``store.load`` splits them at load, so a standalone column file gives the loader one asset id
    twice — accepted with zero problems, then fatal in ``build_index``, *after* the commit."""
    with pytest.raises(Rejected, match="not patchable"):
        _store(tmp_path).draft(_patch(asset_type=AssetType.column), observations=[])


def test_a_patch_may_only_edit_a_field_the_landing_check_can_confirm(tmp_path: Path) -> None:
    """A patch that lands and then reads as ``superseded`` forever is worse than one refused."""
    assert EDITABLE_FIELD_PATHS == {"summary", "body"}
    with pytest.raises(Rejected, match="not editable"):
        _store(tmp_path).draft(_patch(field_path="reliability.status"), observations=[])


def test_an_edit_without_was_is_refused(tmp_path: Path) -> None:
    """``was`` is the concurrency check, not documentation: without it a stale patch silently
    overwrites somebody else's edit instead of failing at ``git apply``."""
    with pytest.raises(Rejected, match="needs `was`"):
        _store(tmp_path).draft(_patch(was=None), observations=[])


def test_a_patch_that_changes_nothing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Rejected, match="changes nothing"):
        _store(tmp_path).draft(_patch(was="same", becomes="same"), observations=[])


def test_the_two_intents_that_author_nothing_must_carry_no_change(tmp_path: Path) -> None:
    """A loop that cannot conclude "there is nothing to patch" will patch. These two members are
    that conclusion, so carrying a change would make them a lie."""
    store = _store(tmp_path)
    with pytest.raises(Rejected, match="authors no asset"):
        store.draft(_patch(intent=PatchIntent.engine_defect, rationale="r_star_projection"), observations=[])
    with pytest.raises(Rejected, match="needs a rationale"):
        store.draft(
            _patch(
                intent=PatchIntent.no_change,
                asset_type=None,
                asset_id=None,
                field_path=None,
                was=None,
                becomes=None,
            ),
            observations=[],
        )
    store.draft(
        _patch(
            intent=PatchIntent.no_change,
            asset_type=None,
            asset_id=None,
            field_path=None,
            was=None,
            becomes=None,
            rationale="the engine was right; the reader misread the units",
        ),
        observations=[],
    )


def test_an_exclusion_request_carries_prose_and_never_a_change(tmp_path: Path) -> None:
    """``Governance.excluded`` is enforced by the absence of a tool. This member is the argument
    for one; a human transcribes it by hand."""
    with pytest.raises(Rejected, match="must not carry a change"):
        _store(tmp_path).draft(
            _patch(
                intent=PatchIntent.exclusion_request,
                rationale="occupant_ssn is a national id",
                becomes="true",
            ),
            observations=[],
        )


def test_the_rejection_carries_every_fault_and_not_the_first(tmp_path: Path) -> None:
    """A caller fixing one problem at a time round-trips as many times as there are problems."""
    with pytest.raises(Rejected) as caught:
        _store(tmp_path).file(_filed(observation_id="", filed_at="", question=""))
    assert len(caught.value.faults) >= 3, caught.value.faults


def test_the_validator_and_the_store_agree(tmp_path: Path) -> None:
    """Whatever the validator accepts, the store takes — the two must not drift into a row that
    passes one and not the other."""
    good = _filed(category=Category.wrong_value)
    assert faults_with(good) == []
    assert _store(tmp_path).file(good) == good.observation_id


# ── an intent nothing can carry to a handoff ──────────────────────────────────


def test_new_asset_is_refused_at_the_draft_and_not_at_the_handoff(tmp_path: Path) -> None:
    """``new_asset`` promised a corpus change no tool can produce.

    ``corpus/patch.py`` has no create primitive, and ``tools/export_bundle.py`` and
    ``tools/verify_patch.py`` both exit 2 on any intent but ``edit_asset``. So the store accepted a
    patch whose whole point was a file, the steward wrote ``asset_yaml``, and the refusal arrived at
    the handoff -- after the work, from a different program, in an exit code.

    Fail closed at the point of entry. The three prose intents are **not** refused: they author
    nothing on purpose and are carried by being read, which is what ``export_bundle``'s own error
    message says.
    """
    store = _store(tmp_path)
    with pytest.raises(Rejected) as caught:
        store.draft(
            _patch(
                intent=PatchIntent.new_asset,
                asset_id=None,
                field_path=None,
                was=None,
                becomes=None,
                asset_yaml="kind: term\nname: active customer\n",
            ),
            observations=[],
        )
    message = str(caught.value)
    assert "edit_asset" in message, f"the refusal must name the declared set: {message}"
    assert "new_asset" in message
    assert store.patches(limit=10).total == 0, "and nothing was written"


def test_the_draftable_set_is_the_one_the_tools_can_carry() -> None:
    """The gate is a declared set rather than an ``is not edit_asset`` test in three files.

    ``DRAFTABLE_PATCH_INTENTS`` is what a steward may store; the tools decide what is in it. When a
    create primitive exists, adding ``new_asset`` here is the whole change -- and until then the
    member is refused in one place instead of two tools and neither.
    """
    from governed_bi.feedback.events import DRAFTABLE_PATCH_INTENTS

    assert PatchIntent.edit_asset in DRAFTABLE_PATCH_INTENTS
    assert PatchIntent.new_asset not in DRAFTABLE_PATCH_INTENTS
    assert DRAFTABLE_PATCH_INTENTS < set(PatchIntent), (
        "a gate that admits every member is the gate that cannot fire"
    )
