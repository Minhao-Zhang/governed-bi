"""The contract of the M4b N19 split, and of the shim that makes it survivable.

Two things have to hold for the move to be real rather than cosmetic, and
neither is checked anywhere else:

1. `eval.statistics` does not depend on `eval.run_datalake`. If it did, the
   statistics would still be reachable only by importing a 3.9k-line driver and
   the split would have bought nothing.
2. Every migration alias in the driver is *the same object* as the function it
   forwards to. An alias that drifts into a second copy is the exact failure the
   whole exercise exists to end -- two spellings of one statistic, diverging.

The aliases are deliberately time-boxed (see the block at the top of
`run_datalake.py`). When they are deleted, delete `test_the_migration_aliases_are_
the_same_objects_not_copies` with them; the remaining tests here stand alone.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from governed_bi.eval import run_datalake, statistics

#: (alias on `run_datalake`, canonical name on `eval.statistics`).
ALIASES = (
    ("_summarise_rows", "summarise_rows"),
    ("_compare_arms", "compare_arms"),
    ("_routing_escaped", "routing_escaped"),
    ("_fmt_rate", "fmt_rate"),
)

#: Names the driver no longer calls but still re-exports for callers mid-migration.
REEXPORTS = (
    "PRICE_VERDICT_TAGS",
    "_bool_rate",
    "_ex_by_stamp",
    "_guardrail_ceiling",
    "_mean",
    "_positive",
    "_rate_over",
    "_split",
    "_sum_counters",
    "_twin_stamps_complete",
    "price_verdict",
)


def test_statistics_does_not_import_the_driver():
    """The point of the split. `statistics` may import any peer in `eval/`, but the
    moment it imports `run_datalake` the dependency is circular in spirit and the
    1.5k lines are no more reusable than they were inside the driver."""
    tree = ast.parse(Path(inspect.getfile(statistics)).read_text(encoding="utf-8"))
    named = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            named.append(node.module)
        elif isinstance(node, ast.Import):
            named.extend(a.name for a in node.names)
    offenders = [m for m in named if "run_datalake" in m]
    assert not offenders, f"eval.statistics imports the driver: {offenders}"


def test_the_migration_aliases_are_the_same_objects_not_copies():
    for alias, canonical in ALIASES:
        got = getattr(run_datalake, alias)
        want = getattr(statistics, canonical)
        assert got is want, (
            f"run_datalake.{alias} is not eval.statistics.{canonical} — the shim has "
            "drifted into a second implementation, which is the failure this split "
            "was meant to end"
        )


def test_the_re_exports_still_resolve():
    """These have no caller inside the driver, so nothing else would notice if an
    import cleanup dropped one — it would surface as a collection error in whichever
    test module still imports it. Named here instead."""
    for name in REEXPORTS:
        assert getattr(run_datalake, name, None) is getattr(statistics, name), name


def test_the_driver_calls_the_public_names_not_its_own_aliases():
    """The aliases exist for *callers*, not for the driver. If the driver goes on
    using the underscore spellings, deleting the aliases later breaks the driver and
    the deadline quietly slips."""
    src = Path(inspect.getfile(run_datalake)).read_text(encoding="utf-8")
    body = src.split("# Derived from the enum", 1)[1]
    for alias, canonical in ALIASES:
        assert alias not in body, (
            f"the driver still calls {alias} below the alias block; it should call "
            f"{canonical}, so the aliases can be deleted without touching it"
        )


def test_the_aliases_carry_a_deletion_deadline():
    """An alias with no stated end date is a permanent second name. The block has to
    say when it goes."""
    src = Path(inspect.getfile(run_datalake)).read_text(encoding="utf-8")
    block = src.split("# Migration aliases (M4b N19)", 1)
    assert len(block) == 2, "the migration alias block lost its heading"
    head = block[1][:2000]
    assert "DELETE" in block[0][-200:] + block[1][:200], (
        "the alias block no longer says it is to be deleted"
    )
    assert "release" in head, "the alias block no longer says when it can be deleted"


def test_the_statistics_entry_points_are_importable_under_their_public_names():
    """What the 181 references migrate *to*. If these are not importable the batches
    have nowhere to go."""
    for name in ("summarise_rows", "ladder_deltas", "compare_arms", "routing_escaped"):
        assert callable(getattr(statistics, name)), name
