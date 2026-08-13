"""The UI's provenance grouping is a hand-copy of the register. Fail when it drifts.

``ui/lib/provenance.ts`` splits a turn record into three drawer sections. It cannot import
:mod:`governed_bi.register.record` — the client shares this repository and nothing else
(ADR 0007) — so the field names are copied by hand, and a hand-copy of a list that grows is
the defect this file exists to catch.

It has already happened once. The lists were written against v1's deleted ``analyst/run_log.py``
and were never re-derived: measured 2026-08-12, **32** of the copied keys named fields the v2
record never emits and **35** of the register's 41 fields appeared on no list, so all of them
fell to the drawer's catch-all group and the three named sections rendered near-empty. Nothing
failed, because nothing was checking.

**Both directions are checked**, because only one of them is the interesting failure. A key in
the TS that the register dropped renders nothing and is merely dead. A register field the TS
never learned about is the one that silently degrades the drawer, and it is the direction a
"does every listed key exist?" test would pass through.

The grouping rule is ``Tier``-pairing, asserted here rather than described: Governance is
``outcome`` + ``decision``, Instrumentation is ``health`` + ``cost``, Run record is ``identity``
+ ``treatment``. Pairing tiers is what makes the mapping re-derivable — a new register row lands
in a group by its declared tier, and this test names the group it belongs in.
"""

from __future__ import annotations

import re
from pathlib import Path

from governed_bi.register.record import RECORD_REGISTER, Tier

PROVENANCE_TS = Path(__file__).resolve().parents[2] / "ui" / "lib" / "provenance.ts"

#: Drawer group -> the register tiers it renders. The whole contract, in one place.
GROUP_TIERS: dict[str, tuple[Tier, ...]] = {
    "GOVERNANCE_KEYS": (Tier.outcome, Tier.decision),
    "INSTRUMENTATION_KEYS": (Tier.health, Tier.cost),
    "RUN_RECORD_KEYS": (Tier.identity, Tier.treatment),
}


def _declared(group: str) -> list[str]:
    """The quoted names in one ``const <group> = [...] as const;`` block."""
    source = PROVENANCE_TS.read_text(encoding="utf-8")
    match = re.search(rf"const {group} = \[(.*?)\] as const;", source, re.S)
    assert match, f"{PROVENANCE_TS.name} declares no `const {group}` array"
    return re.findall(r'"([^"]+)"', match.group(1))


def _expected(group: str) -> list[str]:
    """Tier order as ``GROUP_TIERS`` declares it, register order within a tier.

    Not plain register order: ``RECORD_REGISTER`` interleaves the tiers, and the drawer reads
    outcome-first on purpose. The declared pair *is* the reading order.
    """
    return [f.name for tier in GROUP_TIERS[group] for f in RECORD_REGISTER if f.tier is tier]


def test_the_three_groups_partition_the_register_exactly() -> None:
    """Every register field is in exactly one group, and no group invents a field.

    The positive control is the assertion that the parse found something (D13: a sweep that
    collects offenders and asserts the list is empty passes on zero input — here, on a renamed
    array or a changed formatter, ``_declared`` would return ``[]`` and every set difference
    would be vacuously fine in one direction).
    """
    seen: set[str] = set()
    for group in GROUP_TIERS:
        declared = _declared(group)
        assert declared, f"parsed no names out of {group} — the regex or the file shape changed"
        expected = _expected(group)
        assert expected, f"the register declares no fields for {group}'s tiers"

        missing = sorted(set(expected) - set(declared))
        invented = sorted(set(declared) - set(expected))
        assert not missing, (
            f"{group} is missing register fields {missing}. They will fall into the drawer's "
            f"'Other' group instead of the section their tier names."
        )
        assert not invented, (
            f"{group} lists {invented}, which no register row declares. A key with no field "
            f"renders nothing; delete it or add the register row."
        )
        assert not (seen & set(declared)), f"{group} repeats keys already grouped: {seen & set(declared)}"
        seen |= set(declared)

    every_field = {f.name for f in RECORD_REGISTER}
    assert seen == every_field, f"ungrouped register fields: {sorted(every_field - seen)}"


def test_the_absence_map_matches_the_register() -> None:
    """`ABSENCE` decides what a `null` is *called* in the drawer, so a wrong entry is a lie.

    All three ``Absence`` members encode as JSON ``null``; this map is the client's only way to
    tell them apart. Get ``generated_sql`` wrong and an answered turn's audit row reads "not
    measured" — the engine failed to record the SQL — when the truth is there was no SQL. That
    is a stronger claim than a blank, and it is wrong in the direction that costs trust.
    """
    source = PROVENANCE_TS.read_text(encoding="utf-8")
    block = re.search(r"const ABSENCE: Record<[^>]+> = \{(.*?)\n\};", source, re.S)
    assert block, f"{PROVENANCE_TS.name} declares no `const ABSENCE` map"
    declared = dict(re.findall(r'^\s*(\w+): "(\w+)",', block.group(1), re.M))
    assert declared, "parsed no entries out of ABSENCE — the regex or the file shape changed"

    expected = {f.name: f.absence.value for f in RECORD_REGISTER}
    assert expected, "the register declares no fields"

    assert set(declared) == set(expected), (
        f"ABSENCE covers the wrong fields. Missing {sorted(set(expected) - set(declared))}; "
        f"invented {sorted(set(declared) - set(expected))}."
    )
    wrong = {k: (declared[k], expected[k]) for k in expected if declared[k] != expected[k]}
    assert not wrong, f"ABSENCE disagrees with the register (declared, expected): {wrong}"


def test_the_group_order_follows_the_tier_order() -> None:
    """Within a group the keys are in tier order, so the drawer reads outcome-first.

    Not cosmetic: ``pick()`` renders in list order, so this is the order a reviewer sees. A
    field appended to the bottom of the right array passes the partition test above while
    landing under the wrong tier's heading comment.
    """
    for group in GROUP_TIERS:
        assert _declared(group) == _expected(group), (
            f"{group} holds the right names in the wrong order; it must match "
            f"RECORD_REGISTER order restricted to {[t.value for t in GROUP_TIERS[group]]}"
        )
