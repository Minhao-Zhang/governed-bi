"""Shared scaffolding for the acceptance tests of unbuilt parcels.

**Why acceptance tests land before the implementation.** The parcels in
``docs/plans/v2-layer-handoffs.md`` are being implemented by agents, and an agent
writes tests that pass against the implementation it just produced. v1's gold-gate
test re-derived ``share > THRESHOLD`` itself, so deleting the gate, flipping the
comparison, and reversing the denominator **all passed**. So the criterion is authored
by whoever holds the design, committed first, and the implementer's job is to make it
go green without editing it.

**Why a skip and not an xfail.** ``xfail(strict=True)`` is right for one test that
needs one missing thing — it fails the suite the moment it starts passing, which forces
someone back. It is wrong for a whole parcel: the implementer would have to strip
markers from thirty tests as part of "make them pass", and stripping markers is
indistinguishable from stripping assertions in a diff.

**Why the skip is not silent.** A skipped parcel and a passing parcel look the same
under ``pytest -q``, and half this repo's retired numbers have that shape. So
:data:`UNBUILT` is declared here, and ``test_unbuilt_parcels_match_the_declaration``
in ``tests/conformance/`` fails when the truth on disk diverges — in **either**
direction. Building ``govern/`` therefore breaks the suite until someone deletes its
entry, and that is the forcing function.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "governed_bi"

#: parcel letter -> (package directory, the section of the handoff doc that specs it)
PARCELS: dict[str, tuple[str, str]] = {
    "B": ("govern", "section 3"),
    "C": ("datasource", "section 4"),
    "D": ("corpus", "section 5"),
    "E": ("retrieve", "section 6"),
    "F": ("serve", "section 7"),
    "G": ("eval", "section 8"),
}


def is_built(package: str) -> bool:
    """Whether a package exists with something in it.

    An empty directory does not count. A parcel that has been ``mkdir``-ed but not
    written would otherwise flip this file's declaration to "built" and silently
    un-skip thirty tests that then fail for the wrong reason.
    """
    path = SRC / package
    return path.is_dir() and any(p.suffix == ".py" and p.stem != "__init__" for p in path.iterdir())


def unbuilt() -> frozenset[str]:
    return frozenset(p for p, (pkg, _) in PARCELS.items() if not is_built(pkg))


#: Parcels not yet implemented, **declared** rather than discovered.
#:
#: Kept in sync by a conformance test rather than by hand-checking, because a
#: declaration nothing verifies is the shape that let v1 carry two fields for a year.
UNBUILT: frozenset[str] = frozenset({"C", "E", "F", "G"})


def needs(parcel: str) -> pytest.MarkDecorator:
    """``pytestmark`` for a file whose subject is ``parcel``.

    The reason string names the spec section, so a reader of the skip line knows where
    to look without opening this file.
    """
    package, section = PARCELS[parcel]
    return pytest.mark.skipif(
        not is_built(package),
        reason=(
            f"parcel {parcel} ({package}/) is not built yet -- "
            f"docs/plans/v2-layer-handoffs.md {section}. This file is its acceptance "
            f"criterion: make it pass without editing it."
        ),
    )
