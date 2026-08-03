"""Declared tables. stdlib only, plus ``governed_bi.ports``.

Not a pipeline stage and not a layer. This package exists because six of the
thirty drift incidents in ``docs/lessons-from-v1.md`` were **two tables that
should have been one**:

* ``COMPARABILITY_KEYS`` derived correctly from the knob list, while the ledger
  record was built from a hand-written subset — eight gates dead, because an
  absent key cannot make a diff and this system's own rule reads absence as
  agreement.
* A provenance relay that was an allow-list and never named two fields, so they
  existed **for a year** and reached no artifact.
* ``budgets.get(cls, 0)``, which silently dropped every asset type nobody
  remembered to budget — which is why ``NegativeExampleAsset`` was structurally
  unreachable while the code that dropped it was cited as the reason budgets
  exist at all.
* Two ``LOW_CONFIDENCE_JOIN`` constants **with different comparison operators**,
  one in the scored artifact and one in the UI reading the same corpus.
* Nine competing failure vocabularies, so "which part of the system is breaking?"
  had no answer you could compute.
* A price table entry that overstated a measured run **nine-fold**.

Two rules follow, and they are applied throughout:

**A register declares values; a predicate lives once, next to the type it
tests.** This is the fix for the two comparison operators. The threshold is a
knob in :mod:`.knobs`; the comparison is a method on the asset in
``corpus.assets``. Splitting the value from the comparison is precisely what let
the operators diverge, so the value lives here and the comparison does not.

**Nothing here may be filtered by a consumer — only iterated.** A consumer that
filters is a consumer that can forget. ``budgets.get(cls, 0)`` is the shape to
make unrepresentable: :mod:`.assets` gives every type an explicit budget value,
including the literals ``"all"`` and ``"n/a"``, so there is no default to fall
through to.

**On the direction of the dependency.** These tables describe fields produced by
nodes that sit at the far end of the system, which looks like an inversion. It is
not, because *aboutness has no direction; imports do* — and the framing that
makes it work is that a register is **not a description of the producer, it is a
specification the producer must satisfy**. Same content, opposite dependency
edge. A description flows upward and must import what it describes; a
specification flows downward and is imported by what it constrains.

Neither end can prove closure without an upward import, so closure is proven
where upward imports are legal: ``tests/conformance/``.
"""

from __future__ import annotations

__all__: list[str] = []
