"""Every number a ``#`` comment states about a set in this tree, asserted against the set.

A number in prose and the set in code drift apart unless something connects them, so each
assertion is paired with the comment that states the number, and each failure message names the
file whose prose has to change. Corpus-dependent figures are measurements of a sibling checkout
and stay out; docstring promises about behaviour live in
``tests/feedback/test_the_store_keeps_the_promises_in_its_docstrings.py``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PKG = ROOT / "src" / "governed_bi"


def test_the_per_layer_rule_counts_match_govern_layers_comments() -> None:
    """``govern/layers.py`` states two of these in prose, beside the enum members.

    COLUMNS: "three rule ids". TABLES: one rule. The other five layers get no prose count, and
    are pinned here anyway: a rule moving *between* layers is the change that would make the two
    prose numbers wrong without touching either line.
    """
    import collections

    from governed_bi.govern.layers import RULES

    per_layer = collections.Counter(layer.name for layer in RULES.values())

    expected = {
        "PARSE": 5,
        "NO_WRITE": 4,
        "FUNCTIONS": 2,
        "BINDING": 6,
        "COLUMNS": 3,
        "TABLES": 1,
        "COST": 1,
    }
    assert dict(per_layer) == expected, (
        "the reason-code-to-layer map moved. `govern/layers.py` states COLUMNS' count as **three "
        "rule ids** and TABLES' as **one** in the comments above those two enum members. "
        f"Expected {expected}, got {dict(per_layer)}. Update "
        "the prose in `govern/layers.py` and this expectation together, in one commit."
    )


def test_the_arm_profile_field_count_matches_its_comments() -> None:
    """``register/arm_profiles.py`` tells this story twice, and the number is the whole point.

    "passed nine keys while the dataclass had twelve fields, so ``hypothesised_effect``,
    ``readout`` [and ``notes``] …" and "this call passed nine keys against twelve fields for a
    fortnight, and the three it dropped were the three a gate reads". A field added without a
    constructor argument is the exact defect both comments describe, and it would leave both of
    them stating the wrong count.
    """
    from governed_bi.register.arm_profiles import ArmProfile

    names = [f.name for f in dataclasses.fields(ArmProfile)]
    assert len(names) == 12, (
        "`ArmProfile` no longer has twelve fields, and `register/arm_profiles.py` says 'twelve "
        f"fields' in two comments. It has {len(names)}: {names}. If a field was added, check that "
        "every construction site passes it — that omission, silently, is what both comments are a "
        "record of."
    )


def test_the_five_facets_are_five() -> None:
    """``register/stages.py`` heads the block "── The five facets, concurrent ──".

    ``docs/architecture.md``'s serve-spine diagram draws the same five, and ``eval/projection.py``
    prices a turn at "five facets x fifty hits". Adding a sixth changes a fan-out, a diagram and a
    size estimate; this is the one that fails first.
    """
    from governed_bi.register.stages import Stage

    facets = sorted(s.name for s in Stage if s.name.startswith("facet_"))
    assert len(facets) == 5, (
        "the facet count moved. `register/stages.py` heads its block 'The five facets', "
        "`docs/architecture.md` draws five, and `eval/projection.py` prices 'five facets x fifty "
        f"hits'. Found {len(facets)}: {facets}. All three say five."
    )


def test_the_reflector_verdict_offers_exactly_three_operating_points() -> None:
    """``measure/signals.py``: "Three values, so this signal offers three operating points and
    nothing between them."

    The claim is about the *selective-prediction curve*, not about a spelling: a fourth verdict
    would add an operating point, and the sentence promising there is nothing between them would
    become false while still reading as a design guarantee.
    """
    from governed_bi.measure.signals import _VERDICT_RANK

    assert len(_VERDICT_RANK) == 3, (
        "`_VERDICT_RANK` no longer holds three values, so `measure/signals.py`'s 'three operating "
        f"points and nothing between them' is wrong. It holds {len(_VERDICT_RANK)}: "
        f"{sorted(_VERDICT_RANK)}."
    )


def test_absence_has_three_states_not_two() -> None:
    """``measure/gates.py``: "Three states, not two."

    The whole content of the comment is that the third state exists, so a collapse back to two is
    exactly the regression it was written against.
    """
    from governed_bi.measure.gates import Absence

    members = [m.name for m in Absence]
    assert len(members) == 3, (
        "`measure/gates.Absence` no longer has three members, and the comment above it insists "
        f"'Three states, not two.' It has {len(members)}: {members}."
    )


def test_the_guard_rule_count_matches_the_docs() -> None:
    """Five, in ``docs/architecture.md``'s stage table and in ``govern/guard.py``'s own prose.

    Not a comment-only claim: ``conform/rules_metric_and_content.py``'s V21 reuses
    ``GUARD_RULES`` over corpus text, and ``tests/conformance/test_the_whole_tree_rules_fire.py``
    records that V21 once "hand-ran one helper out of five" instead of the set. The count is the
    thing that made that detectable.
    """
    from governed_bi.govern.guard import GUARD_RULES

    assert len(GUARD_RULES) == 5, (
        "`GUARD_RULES` is no longer five rules. `docs/architecture.md` says 'Five deterministic "
        f"rules' in its `guard` row. Found {len(GUARD_RULES)}: {sorted(GUARD_RULES)}."
    )


def test_the_corpus_has_eight_asset_types() -> None:
    """"**Eight types**" is stated in ``docs/glossary.md`` and in ``docs/corpus-format.md``.

    ``corpus/schema.py`` is the declaration; the glossary's entry turns on the number ("There is no
    note or skill asset — ADR 0003 proposed one and ADR 0005 reversed it"), so a ninth type makes a
    documented reversal read as still in force.
    """
    from governed_bi.corpus.schema import ASSET_CLASSES

    assert len(ASSET_CLASSES) == 8, (
        "`ASSET_CLASSES` is no longer eight. `docs/glossary.md` and `docs/corpus-format.md` both "
        f"say eight, and the glossary's entry argues from the number. Found {len(ASSET_CLASSES)}: "
        f"{sorted(a.value for a in ASSET_CLASSES)}."
    )


def test_the_checkpoint_serde_allowlist_is_still_eighteen_entries_with_three_ours() -> None:
    """``register/quantity.py``: "derives 18 entries of which exactly these 3 are ours."

    Verified by hand on 2026-08-20 and never since. The 18 is a fact about **langgraph**, so this
    is the one assertion here that a dependency bump can break — which is the point. The comment is
    load-bearing: the three names it protects are pickled by reference into the thread registry, so
    renaming one deletes every thread. If a bump changes the derivation, somebody has to re-read
    the comment rather than discover it from a wiped registry.
    """
    from langgraph._internal._serde import build_serde_allowlist

    from governed_bi.register.quantity import CHECKPOINT_PICKLED_NAMES
    from governed_bi.serve.state import ServeState

    allowlist = build_serde_allowlist(schemas=[ServeState])
    entries = sorted(str(entry) for entry in allowlist)
    ours = [entry for entry in entries if "governed_bi" in entry]

    assert len(entries) == 18, (
        "`build_serde_allowlist` no longer derives 18 entries, and `register/quantity.py` states "
        f"18 as of 2026-08-20. It derives {len(entries)}. Re-read that comment before assuming "
        "this is harmless: it is the guard on the three names the thread registry pickles by "
        f"reference.\n  " + "\n  ".join(entries)
    )
    assert len(ours) == 3, (
        "exactly 3 of the allowlist entries are supposed to be ours, per "
        f"`register/quantity.py`. Found {len(ours)}: {ours}."
    )
    assert len(CHECKPOINT_PICKLED_NAMES) == 3, (
        "`CHECKPOINT_PICKLED_NAMES` and the comment above it disagree about how many names reach "
        f"a checkpoint: the constant holds {len(CHECKPOINT_PICKLED_NAMES)}."
    )
