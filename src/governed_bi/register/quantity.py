"""``Measured[T]``: a quantity that may not have been measured, and says so.

**Why this is in ``register/`` and not ``measure/``.** It was planned for
``measure/quantity.py``, one layer up. It cannot live there. :mod:`.record` is in
this layer and declares *which fields must be three-valued*; a layer cannot import
the layer above it, so the register would have been structurally unable to name the
type of the thing it was declaring — able to say "absence is an error here" but not
"and absence is representable in the value". That gap is exactly what produced
L-R1's 25 recurrences: a quantity whose absence had no representation, so ``0``
was used, and ``0`` is a measurement.

``Measured`` is a **declaration of how absence is represented**. It belongs beside
the other declarations. ``measure/`` keeps the things that *compute*.

**The three states, and why two is not enough.**

* :attr:`State.measured` — a value exists.
* :attr:`State.not_measured` — it should exist and does not. A dead endpoint, an
  absent price-table entry, a rate over zero trials.
* :attr:`State.not_applicable` — it correctly does not exist. The ``example``
  facet has no lexical channel *by design*.

Collapsing the last two forces every reader to special-case the legitimate absence,
and that special case is where the next silent degradation hides — the same
argument as :class:`~governed_bi.register.facets.ChannelState`, for the same
reason.

**What this type refuses to do**, each because v1 did it:

* ``bool(m)`` raises. ``if rate:`` is false for a measured ``0.0`` and false for no
  measurement, and those are opposite conclusions.
* Arithmetic operators are **not defined**, so ``m + 1`` is a ``TypeError`` rather
  than a coercion. Combining goes through :meth:`map` and :meth:`combine`, which
  propagate absence instead of defaulting it.
* :meth:`value` raises when there is nothing to return. Reaching for a number is
  where the caller must confront the other two states.
* A bound cannot render as a point estimate. :attr:`relation` carries ``<=`` or
  ``>=``, so the rule-of-three ceiling prints ``<= 1.5%`` and cannot be read as
  "we measured 1.5%".
* ``nan`` and ``inf`` are rejected at construction. ``0/0`` reaching a report as
  ``"nan"`` is the same defect wearing a different string, and it is *more*
  dangerous than ``0`` because it looks like an error rather than a claim.

**Formatting lives here and nowhere else.** :meth:`render` is the only place in
``src/`` permitted to format a number; ``tools/check_measurement_locality.py``
fails the build on ``round(`` or a ``:.2f``-style spec anywhere else. Not
stylistic: v1's rounding helpers turned an unmeasured quantity into ``0.0`` on the
way to a report, so the value was honest right up to the last function that
touched it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Generic, TypeVar

__all__ = [
    "State",
    "Relation",
    "Measured",
    "NotMeasured",
]

T = TypeVar("T")
U = TypeVar("U")


class State(str, Enum):
    """Whether a quantity exists, and if not, which kind of not."""

    #: A value exists and is in :attr:`Measured.raw`.
    measured = "measured"
    #: Should exist, does not. Instrumentation failure, missing table entry, or a
    #: rate whose denominator is zero.
    not_measured = "not_measured"
    #: Correctly does not exist for this subject. Declared, not inferred.
    not_applicable = "not_applicable"


class Relation(str, Enum):
    """How :attr:`Measured.raw` relates to the true quantity.

    A one-sided bound reported as a point estimate is a false precision claim, and
    the rule-of-three ceiling is exactly that shape: observing 0 events in 200
    trials does not measure a rate of 0, it bounds it at 1.5%. v1 published the
    zero.
    """

    exact = "="
    at_most = "<="
    at_least = ">="


class NotMeasured(Exception):
    """Raised by :meth:`Measured.value` when there is nothing to return.

    Deliberately loud. The alternative — returning ``None`` — puts the decision at
    a call site that will write ``or 0``.
    """


@dataclass(frozen=True)
class Measured(Generic[T]):
    """A quantity, or a stated reason there is none.

    Construct through :meth:`of`, :meth:`unmeasured`, :meth:`inapplicable` or
    :meth:`rate` rather than directly — the constructors are what enforce the
    invariants (a reason on every absence, no ``nan``, no value when absent).
    """

    state: State
    raw: T | None = None
    #: Why there is no value. Required when absent, forbidden when present.
    why: str = ""
    relation: Relation = Relation.exact

    # ── construction ──────────────────────────────────────────────────────────

    @classmethod
    def of(cls, value: T, relation: Relation = Relation.exact) -> Measured[T]:
        """A measured value.

        Rejects ``nan`` and ``inf``. A ``nan`` here is almost always ``0/0``
        upstream, and it must become :meth:`unmeasured` at the division rather than
        travel to a report as the string ``"nan"``.
        """
        if value is None:
            raise ValueError(
                "Measured.of(None) — None is the absence sentinel this type exists "
                "to replace. Use unmeasured(why) or inapplicable(why)."
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(
                f"Measured.of({value!r}) — a non-finite measurement is not a "
                "measurement. If this came from a division, the denominator was "
                "zero: use Measured.rate(), which returns unmeasured for 0/0."
            )
        return cls(state=State.measured, raw=value, relation=relation)

    @classmethod
    def unmeasured(cls, why: str) -> Measured[T]:
        """Should have a value and does not. ``why`` is mandatory."""
        if not why:
            raise ValueError(
                "unmeasured() requires a reason: an unexplained absence is "
                "indistinguishable from a forgotten assignment, and the whole "
                "point of the state is that a reader can tell them apart."
            )
        return cls(state=State.not_measured, why=why)

    @classmethod
    def inapplicable(cls, why: str) -> Measured[T]:
        """Correctly has no value for this subject. ``why`` is mandatory."""
        if not why:
            raise ValueError("inapplicable() requires a reason; see unmeasured()")
        return cls(state=State.not_applicable, why=why)

    @classmethod
    def rate(cls, numerator: float, denominator: float, *, what: str) -> Measured[float]:
        """``numerator / denominator``, or unmeasured when the denominator is zero.

        **The single place a rate is computed.** A zero denominator is not a rate of
        zero — it is no rate. v1's quotability gate read a degradation rate of "0
        over 0 turns" as a pass on runs where the fan-out never ran, which is the
        reason ADR 0005 §4.1 now requires the count to be published beside the rate.
        """
        if denominator == 0:
            return Measured(
                state=State.not_measured,
                why=f"no {what}: denominator is zero, so there is no rate to report",
            )
        return Measured.of(numerator / denominator)

    # ── access ────────────────────────────────────────────────────────────────

    @property
    def is_measured(self) -> bool:
        return self.state is State.measured

    @property
    def value(self) -> T:
        """The value, or raise :class:`NotMeasured`.

        Raises rather than returning ``None`` so that the two absent states have to
        be handled where the number is wanted, not defaulted three frames later.
        """
        if self.state is not State.measured:
            raise NotMeasured(f"{self.state.value}: {self.why}")
        assert self.raw is not None  # guaranteed by of()
        return self.raw

    def or_else(self, default: U) -> T | U:
        """The value, or ``default`` — spelled out at the call site.

        Legitimate for a *display* fallback. Never legitimate for a quantity that
        then enters arithmetic or a comparison: that is ``x or 0`` with extra steps,
        which ADR 0005 §6 forbids by name.
        """
        return self.raw if self.state is State.measured else default  # type: ignore[return-value]

    def __bool__(self) -> bool:
        raise TypeError(
            "a Measured has no truth value: `if rate:` is False for a measured 0.0 "
            "and False for no measurement at all, and those are opposite "
            "conclusions. Test .is_measured, or compare .value explicitly."
        )

    # ── combination: absence propagates, it does not default ──────────────────

    def map(self, fn: Callable[[T], U]) -> Measured[U]:
        """Apply ``fn`` if measured; carry the reason through unchanged.

        The relation carries too: mapping a ``<=`` bound leaves it a bound, because
        a monotone transform of a bound is still a bound and forgetting that is how
        a ceiling becomes an estimate.
        """
        if self.state is not State.measured:
            return Measured(state=self.state, why=self.why, relation=self.relation)
        return Measured.of(fn(self.value), relation=self.relation)

    def combine(
        self, other: Measured[U], fn: Callable[[T, U], object], *, what: str
    ) -> Measured[object]:
        """Combine two quantities. Unmeasured if **either** side is.

        This is the operator set's replacement for ``+``. Propagating absence is the
        entire behaviour: v1 summed a cost table where one model was missing and
        published the total, which was the sum of the models it happened to know.
        """
        if self.state is not State.measured or other.state is not State.measured:
            absent = self if self.state is not State.measured else other
            return Measured(
                state=absent.state,
                why=f"{what} needs both sides: {absent.why}",
            )
        weakest = (
            Relation.exact
            if self.relation is Relation.exact and other.relation is Relation.exact
            else (self.relation if self.relation is not Relation.exact else other.relation)
        )
        return Measured.of(fn(self.value, other.value), relation=weakest)

    def bounded(self, relation: Relation) -> Measured[T]:
        """Re-label a measured value as a one-sided bound."""
        return replace(self, relation=relation)

    # ── the only formatting site in src/ ──────────────────────────────────────

    def rounded(self, places: int) -> Measured[float]:
        """Round a numeric measurement. Absence survives rounding."""
        return self.map(lambda v: round(float(v), places))  # type: ignore[arg-type]

    def render(self, places: int = 2, unit: str = "", *, scale: float = 1.0) -> str:
        """The one permitted way to turn a quantity into display text.

        Guarantees, each asserted by a test because each was violated in v1:

        * An absent quantity **never renders as a number**, so it cannot be read as
          zero and cannot be pasted into a claim.
        * The reason is carried into the output, so a reader of the artifact alone
          learns why.
        * A bound renders with its relation attached.

        ``scale`` exists so a proportion can be shown as a percentage without a
        caller doing ``x * 100`` and formatting it — which would be formatting
        outside this method, which the locality gate forbids.
        """
        if self.state is State.not_measured:
            return f"not measured ({self.why})"
        if self.state is State.not_applicable:
            return f"n/a ({self.why})"
        raw = self.value
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            body = str(raw)
        else:
            body = f"{float(raw) * scale:.{places}f}"
        prefix = "" if self.relation is Relation.exact else f"{self.relation.value} "
        return f"{prefix}{body}{unit}"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.render()


def _assert_absence_cannot_carry_a_value() -> None:
    """Import-time guard on the invariant the whole type rests on.

    An absent state with a value in :attr:`Measured.raw` would let ``or_else``
    return it — an absence that quietly holds a number is worse than no type at
    all, because it reads as protected.
    """
    for factory in (
        lambda: Measured.unmeasured("probe"),
        lambda: Measured.inapplicable("probe"),
    ):
        m = factory()
        if m.raw is not None:  # pragma: no cover - import-time guard
            raise AssertionError(f"{m.state.value} carries a value: {m.raw!r}")
        if not m.why:  # pragma: no cover - import-time guard
            raise AssertionError(f"{m.state.value} has no reason")


_assert_absence_cannot_carry_a_value()
