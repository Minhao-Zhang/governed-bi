"""Notes derived from clarifications: their firing condition, and the always cap.

Two defects in the same area, both of which silently made a note fire on questions
it has nothing to say about.

``record_caveats`` is the only note producer in the pipeline, and it took
``NoteKind.context``'s ``always`` default with empty ``triggers``. All 162 notes of
the 2026-07-27 corpus were therefore injected into every question in their schema,
including the caveat raised about *one* BIRD question (docs/plans/eval-rebuild.md
§1). ``apply_always_budget`` then failed to bound the pile: its count cap applied
only to notes with an EMPTY scope, and every produced note is ``schema:``-scoped.

The conversion is only a fix if an ``on_match`` note can still reach the prompt.
Under the eval config it reaches it through semantic retrieval
(``RetrievalResult.note_ids``) — ``pin_triggers_enabled`` is False by default and
the eval drivers build ``Settings.for_env(dev)`` without overriding it, so
``fire_triggers`` returns nothing there. The first test pins that channel; the
trigger tests run with PIN forced on, which is the only configuration in which the
derived triggers are consulted at all.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from governed_bi.analyst.note_inject import (
    apply_always_budget,
    licensed_scope_from_tables,
    select_notes_for_injection,
)
from governed_bi.config import Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import (
    NoteActivation,
    NoteAsset,
    NoteKind,
    ProvenanceStatus,
    TableAsset,
)
from governed_bi.curator.asset_bag import AssetBag, derive_keyword_triggers
from governed_bi.curator.clarifications import ClarificationRecord, ClarificationRecordStatus
from governed_bi.retrieval import RetrievalResult
from governed_bi.retrieval.triggers import fire_triggers

# The two clarifications that motivated the fix, verbatim from
# corpora/curated_sme_20260727/*/_build/clarifications.jsonl.
MONTEREY_Q = (
    "Gold SQL filters geografisch.landkreis = 'Monterey', but the actual county value "
    "in the data is 'monterey county' (no bare 'Monterey' exists), so the query returns "
    "no rows. Likely a mislabeled value; should the filter be 'monterey county'?"
)
ELVIS_Q = (
    "Question asks for titles of films starring 'Elvis Marx', but the gold SQL filters "
    "actor 'Russell Close' with length BETWEEN 110 AND 150 (the filter from train_9237). "
    "Question/SQL mismatch - which is authoritative?"
)


def _answered(scope: str, question: str, answer: str = "Yes, use the county value.") -> ClarificationRecord:
    return ClarificationRecord(
        id=scope,
        scope=scope,
        question=question,
        status=ClarificationRecordStatus.answered,
        answer=answer,
        answered_by="sme",
    )


def _caveat_note(bag: AssetBag) -> NoteAsset:
    (note,) = bag.notes.values()
    return note


@pytest.fixture
def pin_on() -> Settings:
    """PIN forced on: the only config in which a note's triggers are read."""
    return replace(
        Settings.for_env("dev"),
        pin_triggers_enabled=True,
        pin_require_certified=True,
        pin_max=3,
    )


def _scoped_note(
    note_id: str,
    *,
    activation: str,
    summary: str = "caveat",
    kind: str = "context",
    confidence: float | None = 0.5,
) -> NoteAsset:
    return NoteAsset(
        id=note_id,
        kind=kind,
        scope=["schema:s"],
        summary=summary,
        activation=activation,
        confidence=confidence,
        publication_status=ProvenanceStatus.certified,
    )


ORDERS = TableAsset(id="tbl_s_orders", schema="s", physical_name="orders")


def _licensed(corpus: Corpus):
    return licensed_scope_from_tables(corpus, frozenset({ORDERS.id}))


def test_an_on_match_note_reaches_the_prompt_only_when_retrieval_matched_it():
    """The delivery channel the whole conversion depends on.

    ``select_notes_for_injection`` unions ``note_ids`` (semantic/lexical retrieval)
    with ``triggered_note_ids`` (PIN). PIN is off by default, so if the semantic
    channel stopped populating ``note_ids`` for notes, flipping caveats to
    ``on_match`` would not narrow them — it would delete them from every prompt.
    """
    note = _scoped_note("note_s_1", activation="on_match", summary="monterey county is the value")
    corpus = Corpus(assets=[ORDERS, note])
    licensed = _licensed(corpus)

    matched = RetrievalResult(question="q", table_ids=[ORDERS.id], note_ids=[note.id])
    assert [n.id for n in select_notes_for_injection(corpus, matched, licensed)] == [note.id]

    unmatched = RetrievalResult(question="q", table_ids=[ORDERS.id])
    assert select_notes_for_injection(corpus, unmatched, licensed) == []


def test_a_caveat_naming_a_specific_value_becomes_on_match_with_that_value_as_a_trigger():
    """A clarification is raised about one question; its caveat must not outlive it.

    ``NoteKind.context`` defaults to ``always``, and ``record_caveats`` used to take
    that default with no triggers — which is how a note about one restaurant question
    ended up in the prompt of every restaurant question.
    """
    bag = AssetBag(schema="restaurant")
    assert bag.record_caveats([_answered("pair:train_1732", MONTEREY_Q)]) == 1

    note = _caveat_note(bag)
    assert note.activation is NoteActivation.on_match
    assert [t.value for t in note.triggers] == ["Monterey", "monterey county"]
    assert all(t.kind == "keyword" for t in note.triggers)
    assert note.scope == ["schema:restaurant"]


def test_the_derived_triggers_fire_on_the_originating_question_and_not_on_a_sibling(pin_on):
    """The property that makes the derivation worth doing at all.

    A trigger that matched every question in the schema would reproduce the bug in
    the PIN channel; one that matched nothing would make ``on_match`` a deletion.
    Both questions here are real BIRD questions against ``restaurant``.
    """
    bag = AssetBag(schema="restaurant")
    bag.record_caveats([_answered("pair:train_1732", MONTEREY_Q)])
    corpus = Corpus(assets=[TableAsset(id="tbl_r_geo", schema="restaurant", physical_name="geografisch")])
    corpus = Corpus(assets=[*corpus.assets, *bag.notes.values()])
    note_id = _caveat_note(bag).id

    originating = "How many restaurants are there in Monterey county?"
    sibling = "What is the street name of the restaurant with the highest review rating?"

    assert fire_triggers(corpus, originating, settings=pin_on) == [note_id]
    assert fire_triggers(corpus, sibling, settings=pin_on) == []


def test_a_caveat_naming_no_value_stays_always_because_it_has_no_firing_condition():
    """No trigger means nothing to match on, so ``on_match`` would silently drop it.

    A statement about a table's general reliability is not question-specific, and
    ``always`` is the honest activation for it. Emitting ``on_match`` with empty
    triggers would leave the note reachable only by chance through retrieval.
    """
    bag = AssetBag(schema="address")
    general = "Is the zip_data table reliable enough to serve household counts from?"
    assert bag.record_caveats([_answered("query:audit", general, answer="Mostly, with caveats.")]) == 1

    note = _caveat_note(bag)
    assert note.activation is NoteActivation.always
    assert note.triggers == []


def test_prose_apostrophes_and_bare_lowercase_words_do_not_become_triggers():
    """The two false-positive shapes the extractor is filtered against.

    A naive single-quote pattern reads the text between two possessive apostrophes
    as a literal ("the airport's readable name" → "s readable name"), and a bare
    lower-case noun is the schema's own vocabulary, which fires on every question in
    the schema. Either one turns a trigger into noise that pins unrelated notes.
    """
    prose = "the airport's readable name comes from the station table"
    assert derive_keyword_triggers(prose) == []

    assert [t.value for t in derive_keyword_triggers("the county is 'monterey county'")] == [
        "monterey county"
    ]


def test_the_curator_question_wins_over_the_answer_when_both_carry_literals():
    """The answer is schema prose; the question is what identifies the one question.

    Reading both would let backticked column names from a long SME answer outvote
    the literal in the suspicion — and the cap means outvoting is losing.
    """
    triggers = derive_keyword_triggers(ELVIS_Q, "The `Actor` table's `FirstName` is authoritative.")
    assert [t.value for t in triggers] == ["Elvis Marx", "Russell Close"]

    fallback = derive_keyword_triggers("Which reading is right?", "Use `FirstName` from Actor.")
    assert [t.value for t in fallback] == ["FirstName"]


def test_propose_note_leaves_activation_to_the_kind_unless_a_caller_decides():
    """``NoteAsset`` derives activation from ``kind``; an omitted key must stay omitted.

    ``propose_note`` accepted no ``activation``, so ``kind``'s default was the only
    possible value and no caller could author an ``on_match`` note. The default has
    to survive the new parameter: a caller that says nothing gets the kind default.
    """
    bag = AssetBag(schema="s")
    assert bag.propose_note("a plain caveat").startswith("ok:")
    assert bag.notes["note_s_1"].activation is NoteActivation.always
    assert bag.notes["note_s_1"].triggers == []

    bag.propose_note(
        "a matched caveat",
        kind=NoteKind.context,
        triggers=derive_keyword_triggers("the value is 'monterey county'"),
        activation=NoteActivation.on_match,
    )
    assert bag.notes["note_s_2"].activation is NoteActivation.on_match
    assert [t.value for t in bag.notes["note_s_2"].triggers] == ["monterey county"]


def test_every_always_note_counts_against_the_global_cap_even_when_schema_scoped():
    """The count cap has to bound the prompt, not just the globally-scoped notes.

    It used to apply only to ``scope=[]`` notes. Every note the curator writes is
    ``schema:``-scoped, so the cap was dead and ``char_max`` was the only gate — 40
    short caveats all fit under 2000 characters.
    """
    notes = [_scoped_note(f"note_s_{i}", activation="always", summary=f"caveat {i}") for i in range(10)]

    kept = apply_always_budget(notes, global_max=3, char_max=10_000)

    assert len(kept) == 3


def test_the_binding_cap_keeps_the_notes_the_question_retrieved():
    """Which notes survive a binding cap must depend on the question.

    The precedence key had no relevance term, so at equal force and status the
    survivors were decided by confidence and id — the same three notes for every
    question in the schema. Relevance sorts below force/status, so this only decides
    ties (all four notes here are certified advisory).
    """
    notes = [_scoped_note(f"note_s_{i}", activation="always", summary=f"caveat {i}") for i in range(4)]
    relevance = {"note_s_3": 2.0, "note_s_2": 1.0}

    kept = apply_always_budget(notes, global_max=2, char_max=10_000, relevance=relevance)

    assert [n.id for n in kept] == ["note_s_3", "note_s_2"]
