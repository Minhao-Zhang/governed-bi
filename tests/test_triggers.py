"""Trigger PIN: what may be hard-included into retrieval, and by whose authority.

``fire_triggers`` is the one channel in retrieval that bypasses ranking entirely —
its output is unioned into ``selected`` (``rvgd.py``) and prepended to the schema
shortlist (``schema_router.py``) without ever scoring through RRF (ADR 0003,
R7/R8). A pin therefore *is* authority, which is why it is fenced by three gates:
a default-off kill switch, a certified-only publication gate in prod, and a cap.

This file exists because that coverage was silently lost: the only tests for
``fire_triggers`` lived in ``tests/test_note_gates.py``, which was deleted along
with its main subject (``eval/note_gates.py``) while ``triggers.py`` stayed live.
The eval harness does not cover the gap either — ``activation=on_match`` is never
emitted, so no run exercises the PIN path (docs/open-work.md).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from governed_bi.config import Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import (
    Governance,
    NoteAsset,
    ProvenanceStatus,
    TableAsset,
)
from governed_bi.retrieval.triggers import fire_triggers


def _note(
    note_id: str,
    *,
    triggers: list[dict[str, str]],
    status: ProvenanceStatus = ProvenanceStatus.certified,
    confidence: float | None = None,
    governance: Governance | None = None,
) -> NoteAsset:
    return NoteAsset(
        id=note_id,
        kind="routing",
        summary=f"note {note_id}",
        triggers=triggers,
        publication_status=status,
        confidence=confidence,
        governance=governance,
        scope=["schema:s"],
    )


@pytest.fixture
def pin_on() -> Settings:
    """Prod-shaped PIN config: enabled, certified-only, cap 3."""
    return replace(
        Settings.for_env("dev"),
        pin_triggers_enabled=True,
        pin_require_certified=True,
        pin_max=3,
    )


def test_only_notes_whose_keyword_appears_in_the_question_are_pinned(pin_on):
    """The basic contract: a pin is a match, not a guess.

    If this regresses, every question hard-includes unrelated notes ahead of the
    ranked results — the pin channel has no score to fall back on, so a false
    positive is indistinguishable from a real hit downstream.
    """
    hit = _note("note_revenue", triggers=[{"kind": "keyword", "value": "revenue"}])
    miss = _note("note_weather", triggers=[{"kind": "keyword", "value": "rainfall"}])
    corpus = Corpus(assets=[TableAsset(id="tbl_s_orders", schema="s", physical_name="orders"), hit, miss])

    assert fire_triggers(corpus, "total revenue last quarter", settings=pin_on) == ["note_revenue"]


def test_a_question_matching_nothing_pins_nothing(pin_on):
    """No match must return an empty list, not "everything" or "the best one".

    ``fire_triggers`` has no notion of a nearest miss. A non-empty return on a
    non-matching question would inject an off-topic note into every turn.
    """
    corpus = Corpus(assets=[_note("note_revenue", triggers=[{"kind": "keyword", "value": "revenue"}])])

    assert fire_triggers(corpus, "how many breweries are there", settings=pin_on) == []


def test_keyword_matching_ignores_case_on_both_sides(pin_on):
    """Triggers are authored by hand; casing must not decide whether they fire.

    Both the question and the trigger value are casefolded. Drop either fold and
    curator- or SME-authored triggers become silently inert depending on how
    someone happened to type them.
    """
    note = _note("note_gross_margin", triggers=[{"kind": "keyword", "value": "Gross Margin"}])
    corpus = Corpus(assets=[note])

    assert fire_triggers(corpus, "what is GROSS MARGIN by month", settings=pin_on) == ["note_gross_margin"]


def test_regex_triggers_are_inert_because_regex_is_deferred(pin_on):
    """ADR 0003 defers regex-over-the-question; a regex trigger must not fire.

    ``Trigger.kind`` accepts ``"regex"`` at the schema level (authored in Phase 1),
    but nothing compiles it. Firing it would mean untrusted corpus text reaching
    ``re`` on the serve path — the exact escape ADR 0003 calls out — and would also
    quietly enable an unmeasured retrieval mode.
    """
    regex_only = _note("note_regex", triggers=[{"kind": "regex", "value": "rev.*"}])
    corpus = Corpus(assets=[regex_only])

    assert fire_triggers(corpus, "revenue please", settings=pin_on) == []


def test_a_keyword_trigger_still_fires_when_a_sibling_regex_trigger_does_not(pin_on):
    """A note carrying both trigger kinds pins on its keyword, exactly once.

    The kind filter is inside the per-trigger loop, so skipping regex must not
    skip the note. And the loop ``break``s on first match, so a note must not be
    pinned twice — a duplicate id would spend the cap on one note.
    """
    mixed = _note(
        "note_mixed",
        triggers=[
            {"kind": "regex", "value": "rev.*"},
            {"kind": "keyword", "value": "revenue"},
            {"kind": "keyword", "value": "quarter"},
        ],
    )
    corpus = Corpus(assets=[mixed])

    assert fire_triggers(corpus, "revenue this quarter", settings=pin_on) == ["note_mixed"]


def test_the_settings_kill_switch_disables_the_channel_entirely(pin_on):
    """``pin_triggers_enabled`` defaults to False and must be honoured first.

    ADR 0003 ships trigger PIN default-off: until trigger coverage and the
    no-EX-regression arm are measured, no deployment should be pinning. If this
    regresses, the default config starts silently using an unvalidated retrieval
    mode — and the eval numbers stop describing the shipped system.
    """
    corpus = Corpus(assets=[_note("note_revenue", triggers=[{"kind": "keyword", "value": "revenue"}])])

    assert Settings.for_env("prod").pin_triggers_enabled is False
    assert fire_triggers(corpus, "revenue", settings=replace(pin_on, pin_triggers_enabled=False)) == []


def test_only_certified_notes_may_pin_when_the_publication_gate_is_on(pin_on):
    """Prod PIN authority is certified-only; dev may graduate drafts.

    This is the D6/ADR-0003 graduation gate. A draft note is curator output that
    no human has certified, and a pin is unranked authority — letting drafts pin
    in prod hands the retrieval override to whatever the last curator run wrote.
    """
    draft = _note(
        "note_draft_pin",
        triggers=[{"kind": "keyword", "value": "revenue"}],
        status=ProvenanceStatus.draft,
    )
    certified = _note("note_cert_pin", triggers=[{"kind": "keyword", "value": "revenue"}])
    corpus = Corpus(assets=[TableAsset(id="tbl_s_orders", schema="s", physical_name="orders"), draft, certified])

    assert fire_triggers(corpus, "total revenue please", settings=pin_on) == ["note_cert_pin"]

    dev = replace(pin_on, pin_require_certified=False)
    assert fire_triggers(corpus, "total revenue please", settings=dev) == [
        "note_cert_pin",
        "note_draft_pin",
    ]


def test_the_certified_gate_is_closed_when_no_settings_are_supplied():
    """Called without ``settings``, the gate must fail closed to certified-only.

    ``require_certified`` defaults to ``None``, not ``False``. A direct call — a
    test, a script, a future caller that has no ``Settings`` in hand — must not be
    the loosest path in the codebase.
    """
    draft = _note(
        "note_draft",
        triggers=[{"kind": "keyword", "value": "revenue"}],
        status=ProvenanceStatus.draft,
    )
    corpus = Corpus(assets=[draft])

    assert fire_triggers(corpus, "revenue") == []
    assert fire_triggers(corpus, "revenue", require_certified=False) == ["note_draft"]


def test_an_explicit_require_certified_argument_beats_the_settings_default(pin_on):
    """The keyword argument wins over ``settings.pin_require_certified``.

    Settings only fill ``require_certified`` when the caller left it ``None``. The
    offline note gates relied on being able to force the gate open or shut
    independently of the ambient config; collapsing the two makes that untestable.
    """
    draft = _note(
        "note_draft",
        triggers=[{"kind": "keyword", "value": "revenue"}],
        status=ProvenanceStatus.draft,
    )
    corpus = Corpus(assets=[draft])

    assert fire_triggers(corpus, "revenue", settings=pin_on, require_certified=False) == ["note_draft"]


def test_a_governance_excluded_note_never_pins(pin_on):
    """D6 exclusion outranks a matching trigger, in every environment.

    ``governance.excluded`` means the asset is gone from everything the Analyst
    sees. The pin channel is a second door into the prompt that bypasses
    ``for_analyst``-shaped filtering at the shortlist level, so it has to enforce
    the exclusion itself or an excluded note is re-materialized by a keyword.
    """
    excluded = _note(
        "note_excluded",
        triggers=[{"kind": "keyword", "value": "revenue"}],
        governance=Governance(excluded=True, reason="withdrawn"),
    )
    kept = _note("note_kept", triggers=[{"kind": "keyword", "value": "revenue"}])
    corpus = Corpus(assets=[excluded, kept])

    assert fire_triggers(corpus, "revenue", settings=pin_on) == ["note_kept"]


def test_pins_are_capped_and_ordered_certified_then_confidence_then_id(pin_on):
    """The cap keeps prompt bloat bounded, so the *order* decides what survives.

    ADR 0003's tiebreak: certified before draft, then confidence descending, then
    id. A truncation that dropped the certified high-confidence note in favour of
    an unranked draft would make the cap actively harmful. Run with the gate open
    so both statuses compete.
    """
    corpus = Corpus(
        assets=[
            _note(
                "note_draft_high",
                triggers=[{"kind": "keyword", "value": "revenue"}],
                status=ProvenanceStatus.draft,
                confidence=0.99,
            ),
            _note("note_cert_low", triggers=[{"kind": "keyword", "value": "revenue"}], confidence=0.10),
            _note("note_cert_high", triggers=[{"kind": "keyword", "value": "revenue"}], confidence=0.90),
            _note("note_cert_none", triggers=[{"kind": "keyword", "value": "revenue"}]),
        ]
    )

    dev = replace(pin_on, pin_require_certified=False, pin_max=3)
    assert fire_triggers(corpus, "revenue", settings=dev) == [
        "note_cert_high",
        "note_cert_low",
        "note_cert_none",
    ]

    assert fire_triggers(corpus, "revenue", settings=replace(dev, pin_max=1)) == ["note_cert_high"]


def test_settings_pin_max_overrides_the_argument_default(pin_on):
    """With ``settings`` passed, the cap comes from config — the argument is ignored.

    Both live callers pass ``settings``, so ``pin_max=3`` in the signature is not
    the effective cap for them. Pinning this stops a reader from tuning the
    argument and believing the deployment changed.
    """
    corpus = Corpus(
        assets=[
            _note("note_a", triggers=[{"kind": "keyword", "value": "revenue"}]),
            _note("note_b", triggers=[{"kind": "keyword", "value": "revenue"}]),
        ]
    )

    assert fire_triggers(corpus, "revenue", settings=replace(pin_on, pin_max=1), pin_max=99) == ["note_a"]


def test_non_note_assets_are_never_pinned(pin_on):
    """Only ``NoteAsset`` carries triggers; the isinstance filter must hold.

    Callers resolve every returned id as a note (``schema_router`` reads
    ``note.scope``), so leaking a table id here would either crash or, worse,
    hard-include a table into the shortlist off a keyword match.
    """
    table = TableAsset(id="tbl_revenue", schema="s", physical_name="revenue")
    corpus = Corpus(assets=[table])

    assert fire_triggers(corpus, "revenue", settings=pin_on) == []


def test_a_note_with_no_triggers_is_not_pinned(pin_on):
    """An always-on note is delivered by scope/licensing, not by the pin channel.

    ``triggers`` defaults to empty, and most notes have none. Pinning them would
    turn every certified note in the corpus into an unconditional prompt insert.
    """
    corpus = Corpus(assets=[_note("note_always", triggers=[])])

    assert fire_triggers(corpus, "revenue", settings=pin_on) == []
