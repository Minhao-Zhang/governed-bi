"""Shared scaffolding for parcel acceptance tests.

Acceptance criteria are authored before (or without editing by) the implementer.
Contracts skip when the package has no code; they still run when code exists but
the parcel is not in :data:`ACCEPTED`. ``tests/conformance/`` fails the
contradiction "accepted with no code" and reports "code with no acceptance."
"""

from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "governed_bi"

#: parcel letter -> (package directory, spec pointer)
PARCELS: dict[str, tuple[str, str]] = {
    "B": ("govern", "ADR 0006"),
    "C": ("datasource", "ADR 0005"),
    "D": ("corpus", "ADR 0005 §1"),
    "E": ("retrieve", "ADR 0005 §2"),
    "F": ("serve", "ADR 0002"),
    "G": ("eval", "ADR 0004"),
    "I": ("model", "ADR 0011"),
    "J": ("api", "ADR 0007"),
}


def is_built(package: str) -> bool:
    """Whether a package exists with a non-``__init__`` ``.py`` file."""
    path = SRC / package
    return path.is_dir() and any(p.suffix == ".py" and p.stem != "__init__" for p in path.iterdir())


def unbuilt() -> frozenset[str]:
    return frozenset(p for p, (pkg, _) in PARCELS.items() if not is_built(pkg))


#: Parcels a design holder has reviewed and accepted (not derived from disk).
ACCEPTED: frozenset[str] = frozenset({"B", "C", "D", "E"})


def accepted_but_absent() -> frozenset[str]:
    """Parcels declared accepted with no code."""
    return frozenset(p for p in ACCEPTED if not is_built(PARCELS[p][0]))


def built_but_unaccepted() -> frozenset[str]:
    """Parcels with code that no design holder has signed off (reported, not failed)."""
    return frozenset(p for p, (pkg, _) in PARCELS.items() if is_built(pkg) and p not in ACCEPTED)


def needs(parcel: str) -> pytest.MarkDecorator:
    """``pytestmark`` for a file whose subject is ``parcel``."""
    package, section = PARCELS[parcel]
    return pytest.mark.skipif(
        not is_built(package),
        reason=(
            f"parcel {parcel} ({package}/) is not built yet — "
            f"{section}. This file is its acceptance criterion: "
            f"make it pass without editing it."
        ),
    )
