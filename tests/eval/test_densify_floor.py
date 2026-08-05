"""The mechanical floor must actually apply to every summary, including the widest ones.

``tools/densify_summaries.py`` is the acceptance bar in
``docs/plans/corpus-summary-rewrite-2026-08-05.md``, so a defect in it moves the bar rather than
breaking a feature -- which is why it gets tests and the scratch variants do not.

It had two, both found by comparing its output against a hand-edited corpus:

* It composed with ``f"...{nouns}"[:cap]``. For a summary **already at the cap** the slice threw
  the nouns away and the tool made no change at all -- to the 26 widest tables in the corpus, the
  ones with the most columns to disambiguate. The printed count excluded them, so nothing said so.
* It carried gold's inherited truncation markers straight through, leaving 26 indexed summaries
  ending in ``…`` preceded by a partial identifier, in the one field the index reads, while
  ``corpus/validate.py`` calls truncation "the treatment" and forbids it.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]


def _tool():
    path = REPO / "tools" / "densify_summaries.py"
    spec = importlib.util.spec_from_file_location("_densify", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    if str(REPO / "tools") not in sys.path:
        sys.path.insert(0, str(REPO / "tools"))
    spec.loader.exec_module(module)
    return module


CAP = 250


def test_a_table_already_at_the_cap_still_gets_domain_vocabulary() -> None:
    """The silent no-op. The tail gives way; the added vocabulary does not."""
    tool = _tool()
    columns = ", ".join(f"column_{i}" for i in range(40))
    doc = {
        "asset_type": "table",
        "physical_name": "cards",
        "summary": f"cards (cards): {columns}"[:CAP],
        "body": "Individual card printings. Grain is one printing, with name, artist and rarity.",
    }
    out, trimmed = tool.dense_table(doc, CAP)

    assert out != doc["summary"], "a summary at the cap must not be left untouched"
    assert len(out) <= CAP
    assert trimmed, "the identifier tail is what gives way, and that must be reported"
    for word in ("printing", "artist", "rarity"):
        assert word in out, f"{word!r} is domain vocabulary from body and must survive the cap"
    assert "cards" in out, "physical_name must remain -- corpus/validate.py requires it in summary"


def test_no_summary_keeps_an_inherited_truncation_marker() -> None:
    """Gold arrives with 3 schema and 26 table summaries already cut with an ellipsis."""
    tool = _tool()
    schema = {
        "asset_type": "schema",
        "name": "hockey",
        "summary": "hockey: 22 tables — abbrev, AwardsMisc, AwardsPlayers, Goali…",
        "body": "Historical ice-hockey statistics. Players, coaches, scoring, goaltending.",
    }
    out, _ = tool.dense_schema(schema, CAP)
    assert "…" not in out and "..." not in out
    assert "Goali" not in out, "the fragment before the marker names nothing and must go too"
    assert "AwardsPlayers" in out, "complete entries before the marker are real identifiers"
    assert "hockey" in out


def test_entries_are_dropped_whole_never_mid_word() -> None:
    """A fragment in the index is the thing being removed, not introduced."""
    tool = _tool()
    doc = {
        "asset_type": "table",
        "physical_name": "wide",
        "summary": "wide (wide): " + ", ".join(f"averylongcolumnname_{i}" for i in range(30)),
        "body": "Supply terms per part and supplier with quantity and cost.",
    }
    out, _ = tool.dense_table(doc, CAP)
    assert len(out) <= CAP
    tail = out.split(" — ", 1)[-1] if " — " in out else ""
    for entry in (e.strip() for e in tail.split(",") if e.strip()):
        assert entry.startswith("averylongcolumnname_"), f"{entry!r} is a fragment"


def test_a_summary_with_no_body_is_returned_unchanged() -> None:
    """The tool reads `body`; with none there is nothing to move and inventing text is not its job."""
    tool = _tool()
    doc = {"asset_type": "table", "physical_name": "t", "summary": "t (t): a, b", "body": None}
    out, trimmed = tool.dense_table(doc, CAP)
    assert out == "t (t): a, b" and not trimmed


def test_content_words_drop_the_vocabulary_every_schema_shares() -> None:
    """A term fitting twenty of 57 schemas cannot separate them and BM25 charges for it anyway.

    The membership of ``STOP`` was set by measuring which extracted words reach the most schema
    bodies, so this asserts the two defensible classes -- function words, and verbs of the
    "this database *holds* / *tracks*" kind -- and deliberately does **not** assert that domain
    nouns are dropped. ``catalog`` reaches 11 of 57 and stays, because a catalog is not a
    transaction log; stopping nouns is where the list starts eroding the signal it concentrates.
    """
    tool = _tool()
    got = tool.content_words(
        "This database holds records in tables about root beer brands, and it tracks the "
        "breweries that store them.",
        limit=14,
    )
    folded = [w.lower() for w in got]
    for shared in ("database", "records", "tables", "the", "about", "tracks", "store", "them"):
        assert shared not in folded, f"{shared!r} cannot discriminate between schemas"
    assert "root" in folded and "breweries" in folded, "domain nouns must survive"
    assert len(folded) == len(set(folded)), "duplicates waste the character budget"


def test_the_stopword_list_does_not_swallow_domain_nouns() -> None:
    """The other direction, because over-stopping is the failure that would be invisible."""
    tool = _tool()
    for noun in ("catalog", "sales", "reference", "restaurant", "inspection", "draft"):
        assert noun not in tool.STOP, (
            f"{noun!r} is a domain noun and stopping it would delete discriminating signal"
        )


def test_a_trailing_hyphen_that_belongs_to_an_identifier_survives() -> None:
    """``Post+/-`` became ``Post+/`` — a token naming nothing, in the only indexed field.

    The composer strips dangling separators from the end, and the strip set included ``-``. Tail
    entries are joined with ``", "``, so a trailing hyphen can only have come from an identifier.
    This is the residue of the same defect the ellipsis fix was about: the tool putting fragments
    into indexed text while removing someone else's.
    """
    tool = _tool()
    doc = {
        "asset_type": "table",
        "physical_name": "statistiken",
        "summary": "stats (statistiken): GP, G, A, Pts, PIM, Post+/-",
        "body": "Per-season player scoring. Grain is one player-season.",
    }
    out, _ = tool.dense_table(doc, CAP)
    assert out.endswith("Post+/-"), f"the identifier was mangled: {out[-20:]!r}"


def test_no_summary_ends_in_a_fragment_of_an_identifier() -> None:
    """The defect at corpus scale, asserted over a synthetic table wide enough to force a trim.

    The first version of this tool sliced with ``[:cap]`` and left a mid-word fragment as the
    final entry in **518 of 713** rewritten summaries -- tokens like ``movi``, ``lan``, ``c`` and
    ``TeamsP``. The measured +6.1 pp floor was achieved despite that, which is why nothing caught
    it: the number went up.
    """
    tool = _tool()
    names = [f"column_named_{i:02d}" for i in range(60)]
    doc = {
        "asset_type": "table",
        "physical_name": "wide",
        "summary": "wide (wide): " + ", ".join(names),
        "body": "Supply terms per part and supplier with available quantity and cost.",
    }
    out, trimmed = tool.dense_table(doc, CAP)
    assert trimmed and len(out) <= CAP
    tail = out.split(" — ", 1)[-1]
    entries = [e.strip() for e in tail.split(",") if e.strip()]
    assert entries, "the trim must not remove every identifier"
    for entry in entries:
        assert entry in names, f"{entry!r} is a fragment, not one of the column names"
