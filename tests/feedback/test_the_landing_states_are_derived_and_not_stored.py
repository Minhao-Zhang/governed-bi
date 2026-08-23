"""Whether a patch landed is answered from the corpus, on every read, and stored nowhere.

A stored copy of that answer is a second answer able to disagree with the first, and the
disagreement is not hypothetical: two bundles landing in one week make exact-hash matching fail for
a change that *did* ship. A two-state model calls that "handed off, forever" — which is the
unclosable ``open: true`` row this whole design replaces, reintroduced one level up.

So the four-way split is the point of these tests, and ``landed_matched`` is the case that earns it.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from governed_bi.feedback.events import (
    DerivedState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.lifecycle import derived_state
from governed_bi.feedback.store import FeedbackStore, mint_patch_id, utc_now
from governed_bi.register.assets import AssetType

#: Full length on purpose: a truncated hash never equals the digest, and a fixture that
#: carries a prefix would exercise a comparison no real patch can make.
_BASE = "b" * 64
_EXPECTED = "hash-the-bundle-predicted"


def _patch(**over: object) -> Patch:
    base: dict[str, object] = dict(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.edit_asset,
        state=PatchState.exported,
        namespace="beer_factory",
        asset_type=AssetType.term,
        asset_id="term_beer_factory_active",
        field_path="summary",
        was="the old sentence",
        becomes="the new sentence",
        base_corpus_content_hash=_BASE,
        expected_corpus_content_hash=_EXPECTED,
    )
    base.update(over)
    return Patch(**base)  # type: ignore[arg-type]


def test_nobody_has_committed_it_yet() -> None:
    assert (
        derived_state(_patch(), loaded_corpus_hash=_BASE, asset_text_now={})
        is DerivedState.handed_off
    )


def test_the_exact_hash_is_the_unambiguous_answer_and_is_checked_first() -> None:
    assert (
        derived_state(_patch(), loaded_corpus_hash=_EXPECTED, asset_text_now={})
        is DerivedState.landed_verified
    )


def test_a_change_that_shipped_beside_another_one_still_reads_as_landed() -> None:
    """The common real case, and the whole reason there are four states and not two."""
    state = derived_state(
        _patch(),
        loaded_corpus_hash="some-other-hash-because-two-bundles-landed",
        asset_text_now={"term_beer_factory_active": ("the new sentence", "")},
    )
    assert state is DerivedState.landed_matched


def test_a_conflict_or_a_reformat_or_an_edit_before_the_commit_reads_as_superseded() -> None:
    """All three are normal, and all three are invisible without this state."""
    state = derived_state(
        _patch(),
        loaded_corpus_hash="moved",
        asset_text_now={"term_beer_factory_active": ("something a reviewer rewrote", "")},
    )
    assert state is DerivedState.superseded


def test_an_asset_that_is_no_longer_in_the_corpus_reads_as_superseded() -> None:
    assert (
        derived_state(_patch(), loaded_corpus_hash="moved", asset_text_now={})
        is DerivedState.superseded
    )


def test_a_body_edit_is_confirmed_off_the_body() -> None:
    patch = _patch(field_path="body", was="old body", becomes="new body")
    state = derived_state(
        patch,
        loaded_corpus_hash="moved",
        asset_text_now={"term_beer_factory_active": ("untouched summary", "new body")},
    )
    assert state is DerivedState.landed_matched


def test_only_a_passing_fixture_upgrades_a_landing() -> None:
    """``None`` is "nobody re-ran it" and ``False`` is "it landed and did not do what it claimed".
    Neither is ``retrieval_verified``, and a fixture that fails does not un-land a commit."""
    args = dict(
        loaded_corpus_hash=_EXPECTED,
        asset_text_now={"term_beer_factory_active": ("the new sentence", "")},
    )
    assert derived_state(_patch(), retrieval_ok=True, **args) is DerivedState.retrieval_verified
    assert derived_state(_patch(), retrieval_ok=None, **args) is DerivedState.landed_verified
    assert derived_state(_patch(), retrieval_ok=False, **args) is DerivedState.landed_verified


def test_a_fixture_cannot_upgrade_something_that_did_not_land() -> None:
    """``retrieval_ok=True`` on an unlanded patch would claim the corpus changed when it has not."""
    assert (
        derived_state(_patch(), loaded_corpus_hash=_BASE, asset_text_now={}, retrieval_ok=True)
        is DerivedState.handed_off
    )
    assert (
        derived_state(_patch(), loaded_corpus_hash="moved", asset_text_now={}, retrieval_ok=True)
        is DerivedState.superseded
    )


def test_no_column_in_the_store_holds_a_landing_state(tmp_path: Path) -> None:
    """The structural half of the claim: there is nowhere to store it, so it cannot be stored.

    Reads the real schema rather than the dataclass, because a column added in a hurry is exactly
    how a derived answer acquires a stale copy.
    """
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    # `closing`, not a bare `with`: sqlite3's context manager is a *transaction* scope and does
    # not close the handle, which leaves a ResourceWarning the suite reports as an error.
    with closing(sqlite3.connect(store.path)) as conn:
        columns = {
            row[1]
            for table in ("observation", "patch")
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
    landing = {s.value for s in DerivedState}
    assert columns.isdisjoint(landing), f"a landing state has a column: {sorted(columns & landing)}"
    for suspect in ("landed", "landed_at", "derived_state", "is_landed"):
        assert suspect not in columns, f"{suspect!r} is a stored answer to a derived question"
