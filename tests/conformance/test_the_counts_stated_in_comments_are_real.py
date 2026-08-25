"""Every number a ``#`` comment states about a set in this tree, asserted against the set.

**Why this file exists.** A 2026-08-25 audit went looking for comments that had drifted and found
almost none: ``ArmProfile`` really does have twelve fields, the serde allowlist really does derive
eighteen entries of which exactly three are ours, COLUMNS really does own five rule ids. The
comments were right. What none of them had was a *reason to stay* right — the number lived in prose
and the set lived in code, and nothing connected them. That is the shape of every drift defect this
repository has actually shipped: not a wrong statement, but a true statement with no gate under it.

So this is the gate. Each assertion below is paired with the comment that states the number, and
each failure message names the file whose prose has to change. A count that moves is a decision;
this makes it a decision somebody records rather than a sentence that quietly stops being true.

**What is deliberately not here.** Corpus-dependent figures (``api/browse.py``'s "7,300 semantic
nodes over 12,131 edges", measured on ``30872d3``) are measurements of a sibling checkout, not of
this tree, and they carry their corpus and their date. Pinning them here would fail on a corpus bump
for a reason that is not a defect. Docstring promises about *behaviour* live in
``tests/feedback/test_the_store_keeps_the_promises_in_its_docstrings.py``, which is this file's
model.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PKG = ROOT / "src" / "governed_bi"


def test_the_per_layer_rule_counts_match_govern_layers_comments() -> None:
    """``govern/layers.py`` states two of these in prose, beside the enum members.

    COLUMNS: "Five rule ids, of which ``check._columns`` raises four in the order that section
    fixes". TABLES: "Three rules, in a fixed order that is itself the security property". Both are
    ADR 0012 claims, and the ordering half of each is what the adversarial suite measures. The other
    five layers get no prose count, and are pinned here anyway: a rule moving *between* layers is
    the change that would make the two prose numbers wrong without touching either line.
    """
    import collections

    from governed_bi.govern.layers import RULES

    per_layer = collections.Counter(layer.name for layer in RULES.values())

    expected = {
        "PARSE": 5,
        "NO_WRITE": 4,
        "FUNCTIONS": 2,
        "BINDING": 6,
        "COLUMNS": 5,
        "TABLES": 3,
        "COST": 1,
    }
    assert dict(per_layer) == expected, (
        "the reason-code-to-layer map moved. `govern/layers.py` states COLUMNS' count as **five "
        "rule ids** and TABLES' as **three rules** in the comments above those two enum members, "
        f"and `docs/adr/0006` fixes the order. Expected {expected}, got {dict(per_layer)}. Update "
        "the prose in `govern/layers.py` and this expectation together, in one commit."
    )


def test_the_columns_rule_with_no_raiser_is_still_the_one_named() -> None:
    """The COLUMNS comment names its own dead branch: five ids, four raised.

    ``r_column_authorization_unavailable`` "is declared here with no raiser anywhere". That is an
    absence claim, and an absence claim is the kind that rots silently in the *safe* direction —
    somebody wires it up, the comment keeps saying nothing raises it, and a reader trusts the
    comment. Asserted by scanning `src/` rather than by importing, because "nothing raises it" is a
    statement about the whole tree.
    """
    from governed_bi.govern.layers import RULES, Layer

    columns_rules = {rule for rule, layer in RULES.items() if layer is Layer.COLUMNS}
    assert "r_column_authorization_unavailable" in columns_rules, (
        "the rule the COLUMNS comment calls declared-with-no-raiser is not a COLUMNS rule any "
        f"more. COLUMNS owns {sorted(columns_rules)}."
    )

    sources = [p for p in sorted(PKG.rglob("*.py")) if "__pycache__" not in p.parts]
    assert len(sources) >= 50, (
        f"scanned {len(sources)} files under {PKG}, far below the engine's size — the walk is "
        "reaching the wrong root and the assertion below would be vacuous."
    )

    naming: list[str] = []
    for path in sources:
        if path.name == "layers.py":
            continue  # the declaration itself
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "r_column_authorization_unavailable" in line and not line.lstrip().startswith("#"):
                naming.append(f"{path.relative_to(PKG).as_posix()}:{number}: {line.strip()}")

    assert not naming, (
        "`r_column_authorization_unavailable` now has a raiser, so `govern/layers.py`'s COLUMNS "
        "comment — 'five rule ids, of which `check._columns` raises four' and "
        "'declared here with no raiser anywhere' — is wrong. Fix the comment, then change this "
        "test on purpose:\n  " + "\n  ".join(naming)
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
