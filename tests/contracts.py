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
:data:`ACCEPTED` is declared here — and it is a **person's judgement**, not a fact
derived from disk. ``tests/conformance/`` asserts the one direction that is a
contradiction (accepted with no code) and *reports* the other (code with no
acceptance), because failing the second would block the review that resolves it.
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
    # Added 2026-08-03 with the embedder adapters. A package absent from this table is
    # invisible to all three states -- :func:`built_but_unaccepted` iterates it, so
    # `model/` would have existed, unaccepted, and unreported. That is the failure this
    # register exists to prevent, one level up: not an unaccepted parcel, but a parcel
    # nothing knows to ask about.
    "I": ("model", "section 9"),
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


#: Parcels whose implementation a **design holder has reviewed and accepted**.
#:
#: Separate from "has files on disk", and the separation is the whole point. Until
#: 2026-08-03 there was one declaration, ``UNBUILT``, derived from
#: :func:`is_built` — which checks only for a non-``__init__`` ``.py`` file in the
#: package directory. So ``mkdir`` plus one file *forced* the declaration to say
#: "built", and an implementer emptying it was not making a judgement at all. Two
#: parcels (``serve/``, ``eval/``) were graded that way by their own author, and an
#: adversarial review then found in both of them the defect a design-holder contract
#: would have caught: an ``outcome=answered`` on a turn whose every SQL attempt was
#: refused, and a grader re-executing outside ``govern.prepare`` so that governance
#: refusals scored as EX correct.
#:
#: So there are **three** states now, and the middle one is the one that was
#: unrepresentable:
#:
#: * no code — nothing to test, contracts skip
#: * **code, not accepted** — contracts run, and the gap is reported on every run
#: * accepted — reviewed against a contract its implementer did not write
#:
#: Adding a package cannot silently move a parcel into ``ACCEPTED``. Only editing
#: this line can, which is the point: acceptance is a person's judgement, and it
#: should cost a deliberate edit.
#: ``C`` was accepted on 2026-08-03 and rolled back the same day. Not for a defect in
#: its code: the maintainer scoped the project to **Postgres only**, and C's acceptance
#: contract was written by the design holder against ``SqliteConnector``. So it exercised
#: a connector that is now out of scope, while the only in-scope one — ``postgres.py`` —
#: is 69 lines with five stub raises. The contract measured the wrong thing, which is a
#: design-holder error rather than an implementer one, and worth leaving visible: a
#: contract can be honest, thorough, passed cleanly, and still be about the wrong subject.
ACCEPTED: frozenset[str] = frozenset({"B", "C", "D", "E"})


def accepted_but_absent() -> frozenset[str]:
    """Parcels declared accepted with no code. A declaration that cannot be true."""
    return frozenset(p for p in ACCEPTED if not is_built(PARCELS[p][0]))


def built_but_unaccepted() -> frozenset[str]:
    """Parcels with code that no design holder has signed off.

    Reported rather than failed. Failing would block the very review that moves a
    parcel out of this state; printing nothing would restore the defect above.
    """
    return frozenset(p for p, (pkg, _) in PARCELS.items() if is_built(pkg) and p not in ACCEPTED)


def needs(parcel: str) -> pytest.MarkDecorator:
    """``pytestmark`` for a file whose subject is ``parcel``.

    The reason string names the spec section, so a reader of the skip line knows where
    to look without opening this file.
    """
    package, section = PARCELS[parcel]
    # Skip on **absence of code**, not on absence of acceptance: a contract must run
    # against an unaccepted implementation, since running it is how the parcel earns
    # acceptance. What must never happen is the reverse — acceptance following from the
    # directory existing.
    return pytest.mark.skipif(
        not is_built(package),
        reason=(
            f"parcel {parcel} ({package}/) is not built yet -- "
            f"docs/plans/v2-layer-handoffs.md {section}. This file is its acceptance "
            f"criterion: make it pass without editing it."
        ),
    )
