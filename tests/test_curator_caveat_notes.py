"""What a caveat note keeps, and where the kept part is allowed to show up.

``record_caveats`` clipped every SME answer to ``_CAVEAT_NOTE_MAX_CHARS`` and wrote
nothing else: on the 20260730T034522Z run all 31 recorded caveats hit the 400-char
ceiling, so the tail of every one of them — the reasoning behind the conclusion — was
gone from the corpus the arm served. ``NoteAsset.body`` existed for exactly this and
was left ``None``.

The cap itself is not the bug and is not raised here. These tests pin the two halves
of the fix: the whole answer survives in ``body``, and ``body`` does not reach the
places the cap protects (the note's embedding document, the always-injection payload,
the hard ``always-note-budget`` finding that aborts a build).
"""

from __future__ import annotations

import pytest
import yaml

from governed_bi.analyst.note_inject import (
    format_note_lines,
    licensed_scope_from_tables,
    select_notes_for_injection,
)
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import NoteActivation, TableAsset, parse_asset
from governed_bi.corpus.serialize import dump_asset
from governed_bi.corpus.validate import ALWAYS_NOTE_TOTAL_CHARS_MAX, validate_corpus
from governed_bi.curator.asset_bag import _CAVEAT_NOTE_MAX_CHARS, AssetBag
from governed_bi.curator.clarifications import (
    ClarificationRecord,
    ClarificationRecordStatus,
)
from governed_bi.retrieval import RetrievalResult
from governed_bi.retrieval.rvgd import asset_document

# The shape of answer that motivated the fix, from the run's SME replies: a verdict in
# the first sentences and the evidence for it afterwards. Long enough to clip (>400
# chars) with the reasoning in the part that used to be discarded.
LONG_ANSWER = (
    "Confirmed, the column names in congress are swapped relative to what they hold. "
    "congress.first_name stores surnames and congress.last_name stores given names, "
    "so a query that filters on first_name for a given name returns nothing at all. "
    "The documented dataset I have describes the two columns in the ordinary order, "
    "which is why the physical values look inverted against it. "
    "The practical consequence is that any join or filter written from the column "
    "names alone will silently return an empty result rather than an error, and the "
    "gold literal in the pair you asked about is therefore wrong: it matches the "
    "given name against the column that holds surnames. Prefer matching on the "
    "bioguide identifier when one is available, and treat the two name columns as "
    "unlabelled text otherwise."
)

SHORT_ANSWER = "Yes, the gold literal is wrong; the county value is 'monterey county'."


def _answered(
    rec_id: str, answer: str, *, scope: str = "pair:train_1732", question: str = "Which reading is right?"
) -> ClarificationRecord:
    """An answered clarification on a scope no asset owns, so it lands as a caveat."""
    return ClarificationRecord(
        id=rec_id,
        scope=scope,
        question=question,
        status=ClarificationRecordStatus.answered,
        answer=answer,
        answered_by="sme",
    )


def _only_note(bag: AssetBag):
    (note,) = bag.notes.values()
    return note


def test_a_clipped_caveat_keeps_the_whole_answer_in_body():
    """The regression. ``summary`` stays inside the cap; nothing is discarded.

    ``_clip_words`` marks the cut with a trailing " …", so a clipped summary can run
    two characters past the cap — which is why the run's surviving summaries cluster at
    398 to 401 rather than at exactly 400. The cap is on the payload, not on the
    marker, so that is the bound asserted here.
    """
    bag = AssetBag(schema="congress")

    assert bag.record_caveats([_answered("q1", LONG_ANSWER)]) == 1

    note = _only_note(bag)
    assert len(note.summary) <= _CAVEAT_NOTE_MAX_CHARS + len(" …")
    assert note.summary != LONG_ANSWER  # it really was clipped
    assert note.summary.endswith("…")
    assert note.body == " ".join(LONG_ANSWER.split())
    # The part that used to be thrown away.
    assert "bioguide identifier" in note.body
    assert "bioguide identifier" not in note.summary


def test_an_answer_that_fits_the_cap_carries_no_body():
    """A body equal to the summary would be the same text stored twice — and would
    then be rendered twice on an ``on_match`` turn."""
    bag = AssetBag(schema="restaurant")

    assert bag.record_caveats([_answered("q1", SHORT_ANSWER)]) == 1

    note = _only_note(bag)
    assert note.summary == SHORT_ANSWER
    assert note.body is None


def test_the_body_is_not_the_notes_embedding_document():
    """``body`` must not dilute the note's own vector / BM25 document.

    ``asset_document`` is the single text a note is indexed by, in both the retrieval
    index and the schema router, so this is the whole embedding surface.
    """
    bag = AssetBag(schema="congress")
    bag.record_caveats([_answered("q1", LONG_ANSWER)])
    note = _only_note(bag)

    assert asset_document(note) == note.summary
    assert "bioguide identifier" not in asset_document(note)


def test_bodies_do_not_count_against_the_hard_always_note_budget():
    """The budget the cap exists to protect is a HARD ``validate_corpus`` finding, and
    a hard finding aborts the whole schema build. Four always-caveats whose summaries
    fit under 2000 characters but whose bodies together run past it must still validate
    clean. (A fifth would be dropped by the producer's own budget check, which is the
    behaviour the retracted "notes are being dropped" claim was about and is not what
    this test is measuring.)"""
    bag = AssetBag(schema="congress")
    # No literal in question or answer → no trigger → ``always`` activation, which is
    # the only activation the budget applies to.
    records = [
        _answered(f"q{i}", LONG_ANSWER, question="Is this table reliable enough to serve from?")
        for i in range(4)
    ]

    recorded = bag.record_caveats(records)

    assert recorded == 4
    notes = list(bag.notes.values())
    assert all(n.activation is NoteActivation.always for n in notes)
    assert sum(len(n.summary) for n in notes) <= ALWAYS_NOTE_TOTAL_CHARS_MAX
    assert sum(len(n.body or "") for n in notes) > ALWAYS_NOTE_TOTAL_CHARS_MAX
    findings = validate_corpus(bag.all_assets())
    assert [f for f in findings if f.code == "always-note-budget"] == []


def test_an_always_caveats_body_never_reaches_the_prompt():
    """End to end through the injector the analyst prompt is built from: an ``always``
    note contributes its summary and nothing else."""
    bag = AssetBag(schema="congress")
    bag.record_caveats(
        [_answered("q1", LONG_ANSWER, question="Is this table reliable enough to serve from?")]
    )
    note = _only_note(bag)
    assert note.activation is NoteActivation.always

    table = TableAsset(id="tbl_congress_members", schema="congress", physical_name="members")
    corpus = Corpus(assets=[table, note])
    licensed = licensed_scope_from_tables(corpus, frozenset({table.id}))

    (injected,) = select_notes_for_injection(
        corpus, RetrievalResult(question="q", table_ids=[table.id]), licensed
    )

    assert injected.body is None
    must, advisory = format_note_lines([injected])
    rendered = "\n".join([*must, *advisory])
    assert "bioguide identifier" not in rendered


def test_a_matched_on_match_caveat_does_render_its_body():
    """The one place a body is prompt text, pinned so the cost is not a surprise.

    ``select_notes_for_injection`` passes a body through for an ``on_match`` note that
    retrieval matched, and charges it against the shared note char budget. That is the
    documented progressive disclosure (``note_inject`` line "body only for on_match"),
    and it is the reason the summary cap still matters: the full answer arrives only on
    the turns whose question matched the caveat's literal.
    """
    bag = AssetBag(schema="congress")
    bag.record_caveats(
        [
            _answered(
                "q1",
                LONG_ANSWER,
                question="Gold SQL filters congress.first_name = 'Elvis Marx'; which reading is right?",
            )
        ]
    )
    note = _only_note(bag)
    assert note.activation is NoteActivation.on_match
    assert note.triggers  # the literal that fires it

    table = TableAsset(id="tbl_congress_members", schema="congress", physical_name="members")
    corpus = Corpus(assets=[table, note])
    licensed = licensed_scope_from_tables(corpus, frozenset({table.id}))

    matched = RetrievalResult(question="q", table_ids=[table.id], note_ids=[note.id])
    (injected,) = select_notes_for_injection(corpus, matched, licensed)
    assert injected.body == note.body

    unmatched = RetrievalResult(question="q", table_ids=[table.id])
    assert select_notes_for_injection(corpus, unmatched, licensed) == []


def test_the_body_survives_the_yaml_round_trip():
    """A field the writer drops is a field that does not exist. ``dump_asset`` uses
    ``exclude_none``, so an absent body stays absent and a written one comes back."""
    bag = AssetBag(schema="congress")
    bag.record_caveats([_answered("q1", LONG_ANSWER)])
    note = _only_note(bag)

    reloaded = parse_asset(yaml.safe_load(dump_asset(note)))

    assert reloaded.body == note.body
    assert reloaded.summary == note.summary


def test_the_build_log_reports_clipping_as_disclosure_not_as_loss(capsys: pytest.CaptureFixture):
    """The counter is quoted out of run logs, and it used to read as a loss report.
    Clipping no longer discards anything, and the line has to say which."""
    bag = AssetBag(schema="congress")
    bag.record_caveats([_answered("q1", LONG_ANSWER)])

    out = capsys.readouterr().out
    assert "1 recorded, 1 summaries clipped" in out
    assert "full answer kept in note.body" in out
