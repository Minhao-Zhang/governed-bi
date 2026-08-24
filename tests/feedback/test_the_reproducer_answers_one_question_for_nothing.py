"""T3: does the failure still happen, and what a green one is allowed to mean.

The routing half needs a live catalog and an embedder, so it is not driven here — ``routing_recall``
is covered in ``tests/eval/``. What is covered here is everything around it, which is where the
mistakes are: which observations the check *applies* to, and the fact that ``retrieval_verified``
was a declared state nothing could reach.

**The claim a green T3 licenses is narrow and it is asserted as a string.** On turns where every
gold table was licensed, measured accuracy is 0.7555 — so about one in four complaints "fixed" here
would still come back with a wrong number. Two tests below exist to keep the word "resolved" out of
the vocabulary and the number in the sentence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from governed_bi.feedback.events import (
    DerivedState,
    Kind,
    Observation,
    ObservationState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.lifecycle import derived_state
from governed_bi.feedback.store import (
    FeedbackStore,
    mint_observation_id,
    mint_patch_id,
    utc_now,
)
from governed_bi.feedback.validate import CONTENT_HASH_CHARS
from governed_bi.register.assets import AssetType

HASH_A = "a" * CONTENT_HASH_CHARS
HASH_B = "b" * CONTENT_HASH_CHARS
ASSET = "address.zip_congress"
WAS = "zip_congress maps a zip code to a district."
BECOMES = "zip_congress maps a zip code to a district. Questions about a district read this."

GOLD = "SELECT district FROM address.zip_congress JOIN address.congress USING (district)"


def _observation(**over: object) -> Observation:
    base: dict[str, object] = dict(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.eval,
        kind=Kind.wrong_answer,
        state=ObservationState.open,
        question="which district is this zip in?",
        external_key="k",
        arm="v4",
        question_id="train_5122",
        db_id="address",
        gold_sql=GOLD,
        missing_tables=("address.zip_congress",),
    )
    base.update(over)
    return Observation(**base)  # type: ignore[arg-type]


def _patch(**over: object) -> Patch:
    base: dict[str, object] = dict(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.edit_asset,
        state=PatchState.exported,
        namespace="address",
        asset_type=AssetType.table,
        asset_id=ASSET,
        field_path="summary",
        was=WAS,
        becomes=BECOMES,
        base_corpus_content_hash=HASH_A,
    )
    base.update(over)
    return Patch(**base)  # type: ignore[arg-type]


# ── which observations the check applies to ───────────────────────────────────


def _why_not(observation: Observation) -> str | None:
    from reproduce_observation import _why_not as under_test

    return under_test(observation)


def test_a_coverage_failure_is_checkable() -> None:
    assert _why_not(_observation()) is None


def test_an_observation_with_no_gold_statement_is_not_checkable() -> None:
    """A row a person filed carries no reference answer, so coverage has nothing to compare to.
    Saying so beats reporting a pass on a check that never ran."""
    why = _why_not(_observation(gold_sql=None))
    assert why is not None and "no gold_sql" in why


def test_an_unparseable_gold_statement_is_named_as_a_dataset_defect() -> None:
    """A different sentence from the one above, on purpose: this sends the reader to the benchmark
    rather than to the queue."""
    why = _why_not(_observation(gold_sql="SELECT FROM WHERE ("))
    assert why is not None and "dataset defect" in why


def test_a_row_that_was_never_a_coverage_failure_is_not_checkable() -> None:
    """Every gold table was licensed and the answer was still wrong. That is a real failure and
    coverage cannot say anything about it -- reporting a pass would call it fixed."""
    why = _why_not(_observation(missing_tables=()))
    assert why is not None and "not a coverage failure" in why


# ── retrieval_verified, which nothing could reach ─────────────────────────────


def test_a_landing_without_a_t3_run_is_not_retrieval_verified() -> None:
    """`None` means nobody asked. It must leave the landing where it was rather than upgrade it."""
    state = derived_state(
        _patch(),
        loaded_corpus_hash=HASH_B,
        asset_text_now={ASSET: (BECOMES, "")},
        retrieval_ok=None,
    )
    assert state is DerivedState.landed_matched


def test_a_failed_t3_leaves_the_landing_alone_rather_than_downgrading_it() -> None:
    """`False` is "asked, and the question still fails". The change *did* land, so the landing state
    is still true -- and collapsing `False` into `None` would make an unrun check read as a failed
    one, which is what sends a good change back to the steward."""
    state = derived_state(
        _patch(),
        loaded_corpus_hash=HASH_B,
        asset_text_now={ASSET: (BECOMES, "")},
        retrieval_ok=False,
    )
    assert state is DerivedState.landed_matched


def test_a_passing_t3_upgrades_the_landing() -> None:
    state = derived_state(
        _patch(),
        loaded_corpus_hash=HASH_B,
        asset_text_now={ASSET: (BECOMES, "")},
        retrieval_ok=True,
    )
    assert state is DerivedState.retrieval_verified


def test_a_patch_that_never_landed_is_not_upgraded_by_a_passing_t3() -> None:
    """The order matters. A patch still sitting at its authoring hash is `handed_off`, and a green
    coverage check on a corpus that does not contain it says something about the corpus, not about
    the patch."""
    state = derived_state(
        _patch(),
        loaded_corpus_hash=HASH_A,
        asset_text_now={ASSET: (BECOMES, "")},
        retrieval_ok=True,
    )
    assert state is DerivedState.handed_off


def test_check_landed_reads_the_verdict_off_the_ladder(tmp_path: Path) -> None:
    """The wire. `derived_state` has taken `retrieval_ok` since it was written and every caller
    passed `None`, so the state was declared and unreachable."""
    from check_landed import _retrieval_ok

    assert _retrieval_ok(_patch()) is None, "an empty ladder is 'nobody asked'"
    assert _retrieval_ok(_patch(ladder={"T3": {"passed": True}})) is True
    assert _retrieval_ok(_patch(ladder={"T3": {"passed": False}})) is False
    assert _retrieval_ok(_patch(ladder={"T0": {"passed": True}})) is None, (
        "T0 passing says nothing about retrieval"
    )
    assert _retrieval_ok(_patch(ladder={"T3": {"detail": "ran"}})) is None, (
        "a T3 row with no verdict is not a verdict"
    )


# ── the claim ─────────────────────────────────────────────────────────────────


def test_the_claim_names_the_number_and_the_population_it_was_measured_on() -> None:
    """The one sentence that stops a green T3 being read as "fixed", and it has to name **which**
    turns it was measured over.

    0.7555 is the accuracy on turns where every gold table was licensed *and the gold names at least
    one table* — n=1,145. Read literally, "every gold table was licensed" also admits the 127 turns
    whose gold reads no table at all: a frozen literal satisfies the condition vacuously, and no
    engine can win one. Include them and the same figure is **0.7131** over n=1,272.

    The sentence carried 0.7555 with the literal wording for the life of this branch, in six places.
    The number was defended once against a challenge — with a script that skipped the tableless rows
    and therefore reproduced 1,145 and agreed with itself. So this asserts the population, not just
    the digits: a number is not a measurement until it says what it was measured over.
    """
    from reproduce_observation import CLAIM

    assert "0.7555" in CLAIM
    assert "at least one table" in CLAIM, (
        "the claim states an accuracy without the exclusion that produced it, which is the reading "
        f"that made 0.7131 look like a rival number instead of the same one: {CLAIM}"
    )
    assert "0.7131" in CLAIM, "and the figure for the literal reading, so neither can hide"
    assert "NOT that the answer is right" in CLAIM


def test_the_vocabulary_has_no_resolved() -> None:
    """`retrieval_verified` is the narrowest upgrade the free ladder licenses. A state called
    `resolved` would be a claim nobody can make from a coverage check."""
    assert "resolved" not in {s.value for s in DerivedState}


def test_the_ladder_row_records_which_retrieval_channel_ran(tmp_path: Path) -> None:
    """Measured while building this: an observation recorded with 1 missing gold table came back
    with 2 on a lexical-only re-check, and with 1 again once the embedder matched the arm's. A T3
    row that did not say which channel ran would make that indistinguishable from a real finding.
    """
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    patch = _patch()
    store.draft(patch, observations=[])
    store.record_ladder(
        patch.patch_id,
        "T3",
        {"passed": True, "retrieval_channel": "lexical+semantic", "detail": "x"},
    )
    recorded = dict(store.get_patch(patch.patch_id).ladder)  # type: ignore[union-attr]
    assert recorded["T3"]["retrieval_channel"] == "lexical+semantic"


@pytest.mark.parametrize("field", ["summary", "body"])
def test_only_a_summary_edit_is_answerable_by_retrieval(field: str) -> None:
    """`body` does not enter the retrieval index -- `serve/context.py` puts it in the model's
    prompt -- so a `body`-only patch cannot be verified by a coverage check at any price. The tool
    refuses rather than reporting a pass, and the honest tier is T4, which costs money."""
    from reproduce_observation import _population

    store_path = Path("nonexistent")
    del store_path  # the refusal happens before any read; asserted through the store below

    class _Store:
        def get_patch(self, _: str) -> Patch:
            return _patch(field_path=field)

        def observations_of(self, _: str) -> tuple[Observation, ...]:
            return (_observation(),)

    result = _population(_Store(), observation_id=None, patch_id="pat-x")  # type: ignore[arg-type]
    if field == "body":
        assert result is None, "a body-only patch must be refused, not checked"
    else:
        assert result is not None and len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# "Nothing here applies" is not "this tier failed".
# ─────────────────────────────────────────────────────────────────────────────


def test_a_tier_that_could_not_run_records_nothing_rather_than_a_failure() -> None:
    """The docstring says the tool says which of the three -- "never `passed`". It said *failed*.

    ``passed = bool(gone) and not still``, so when every observation is not-applicable both lists
    are empty and the tier records ``passed: False`` and the tool exits 1. A ``body``-only patch is
    exactly this case, and the module names it: ``body`` does not enter the retrieval index, so
    retrieval cannot see the change and the honest tier is T4.

    ``verify_patch.py`` already states the rule this violates -- "a tier that cannot run must not be
    reported as passing, so an unrun tier is simply absent from the ladder rather than recorded as
    skipped-therefore-fine". Absent, and *not* failed: a failed T3 blocks a handoff that nothing
    here has any evidence against.
    """
    import reproduce_observation as ro

    verdict = ro.tier_verdict([ro.Outcome("obs-1", None, "no gold statement")])
    assert verdict is None, f"an all-not-applicable run must record nothing, got {verdict}"


@pytest.mark.parametrize(
    ("reproduced", "expected"),
    [
        ((False,), True),
        ((False, False), True),
        ((True,), False),
        ((True, False), False),
        ((None, False), True),
        ((None, True), False),
    ],
)
def test_the_verdict_reads_only_the_observations_it_could_check(
    reproduced: tuple[bool | None, ...], expected: bool
) -> None:
    """A not-applicable observation neither passes nor fails the tier -- it is not evidence.

    Mixed runs are the common case: a cluster's observations do not all carry a parseable gold
    statement. The tier's verdict is about the ones it could check, and the ones it could not are
    reported in ``outcomes`` for the reviewer rather than folded into a boolean.
    """
    import reproduce_observation as ro

    outcomes = [ro.Outcome(f"obs-{i}", value, "") for i, value in enumerate(reproduced)]
    assert ro.tier_verdict(outcomes) is expected
