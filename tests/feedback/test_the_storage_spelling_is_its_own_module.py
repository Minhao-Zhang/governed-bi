"""``feedback/rows.py`` holds how an ``Observation`` and a ``Patch`` are spelled in SQLite, and
depends on nothing in ``store.py``.

**Why the seam is here and not between reads and writes.** `store.py` reached 1,028 lines and the
hard cap is 1,000, so it needed a cut. The obvious one — lift the eight read methods into a
`queries.py` — separates `observation_row` from `observation_from`, and those two are exactly the
pair that must change together: add a column and the DDL, the writer's mapper and the reader's
mapper all move. `test_the_store_keeps_the_promises_in_its_docstrings.py` says so in its own words,
"the dataclass, `_SCHEMA`, `_observation_row` and `_observation_from` — four places, and a silent
loss in any one of them", and it exists because three mappers each dropping one field once survived
the suite. A read/write cut would have put a module boundary through the middle of that.

So the cut is the storage spelling: the DDL, both directions of both mappers, and the field-checked
`replace_row`. Three of those four places are now adjacent, and the fourth is the dataclass in
``events.py``.

**The direction is asserted, not just intended.** A cycle is what the read/write cut would have
produced — writers need `observation_from` to read a row before updating it, so `store` would have
had to import `queries` and `queries` would have grown the mappers anyway. This module imports
`events` and the standard library and nothing else, and the test below is what keeps that true.
"""

from __future__ import annotations

import ast
from pathlib import Path

FEEDBACK = Path(__file__).resolve().parents[2] / "src" / "governed_bi" / "feedback"


def _imported_modules(path: Path) -> set[str]:
    """Every module name ``path`` imports, absolute or relative, at any depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(("." * node.level) + (node.module or ""))
    return out


def test_the_spelling_module_does_not_import_the_store() -> None:
    """One direction, asserted. Both are in one package, so `tools/check_imports.py` cannot see
    this: its `LAYERS` order governs which package may import which, and `feedback` importing
    `feedback` is inside one entry."""
    imports = _imported_modules(FEEDBACK / "rows.py")
    offenders = {
        name for name in imports if name.endswith("store") or name.endswith("feedback.store")
    }
    assert not offenders, (
        f"rows.py imports {sorted(offenders)}. It is the layer under the store, and a cycle here "
        "is what the read/write cut would have produced."
    )


def test_the_spelling_module_names_only_the_shapes_inside_this_package() -> None:
    """Within `feedback/` it may name `events` — the dataclasses it translates — and nothing else.

    Scoped to this package on purpose. `register` is *below* `feedback` in
    `tools/check_imports.py::LAYERS`, and `patch_from` needs `AssetType` to rebuild a stored
    `asset_type`, so that import is the layering working rather than a leak. The first version of
    this test forbade it and was wrong: it filtered on `governed_bi` and then asserted a set that
    only allowed `events`, which reads as "no dependencies" while claiming "none in the package".
    A second dependency *inside* `feedback` is the thing worth refusing — that is the module
    starting to do a second job.
    """
    inside = {
        name
        for name in _imported_modules(FEEDBACK / "rows.py")
        if name.startswith(".") or name.startswith("governed_bi.feedback")
    }
    assert inside <= {".events", "governed_bi.feedback.events"}, (
        f"rows.py reaches into {sorted(inside)}; inside this package it should need only the "
        "shapes it spells"
    )


def test_the_store_reads_its_spelling_from_that_module() -> None:
    """The seam exists rather than the names having been copied. A copy would pass the test above
    and is the failure this pins: two spellings of one row is how a column gets written by one and
    dropped by the other."""
    source = (FEEDBACK / "store.py").read_text(encoding="utf-8")
    assert "rows import" in source or "from governed_bi.feedback.rows" in source, (
        "store.py does not import the spelling module, so either the seam is gone or the mappers "
        "were duplicated back into it"
    )
    for name in ("observation_row", "observation_from", "patch_row", "patch_from"):
        assert f"def {name}(" not in source, (
            f"store.py defines {name} again — that is the second spelling this seam exists to "
            "prevent"
        )


def test_the_store_is_under_the_hard_cap_with_room() -> None:
    """The cut was made because the file crossed 1,000 lines. Asserted at the warn tier rather than
    the cap, because landing at 999 would mean the next change re-opens this question."""
    lines = len((FEEDBACK / "store.py").read_text(encoding="utf-8").splitlines())
    assert lines < 900, f"store.py is {lines} lines; the cut bought no room"
