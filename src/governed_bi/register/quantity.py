"""``Measured[T]``: a quantity that may be absent, with an explicit reason.

Lives in ``register/`` so :mod:`.record` can recognise unmeasured values in the
presence test without importing upward. Three states: measured, not_measured,
not_applicable. Truthiness and arithmetic are refused; format only via
:meth:`Measured.render`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Generic, TypeVar

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
    """How :attr:`Measured.raw` relates to the true quantity, so that a one-sided
    bound does not render as a point estimate.
    """

    exact = "="
    at_most = "<="
    at_least = ">="


class NotMeasured(Exception):
    """Raised by :meth:`Measured.value` when there is nothing to return."""


@dataclass(frozen=True)
class Measured(Generic[T]):
    """A quantity, or a stated reason there is none.

    Construct through :meth:`of`, :meth:`unmeasured`, :meth:`inapplicable` or
    :meth:`rate`. Invariants: reason on every absence; no ``nan``/``inf``; no
    value when absent.
    """

    state: State
    raw: T | None = None
    #: Why there is no value. Required when absent, forbidden when present.
    why: str = ""
    relation: Relation = Relation.exact

    # ── surviving a checkpoint ────────────────────────────────────────────────

    def __class_getitem__(cls, item: object) -> type[Measured[Any]]:
        """``Measured[int]`` **is** ``Measured`` at runtime: the subscript is erased.

        Erasure is what registers the class with LangGraph's msgpack serde. Under
        strict mode (langgraph 1.2.11) ``StateGraph.compile`` derives the allowlist by
        walking the state schema (``_internal._serde.build_serde_allowlist`` →
        ``BaseCheckpointSaver.with_allowlist``) — the only seam that reaches the
        deployed server's saver, which this repository never builds. That walk
        recognises real classes only. Without this method
        ``dataclasses.is_dataclass(Measured[int])`` would be ``False``, because a
        subscripted generic is a ``typing._GenericAlias``, and the allowlist derived
        from ``ServeState`` named nothing from this module.

        Unregistered, an absence comes back as a plain dict — ``.is_measured`` gone,
        :func:`~.record.missing_required`'s presence test blind to it, and
        :meth:`__bool__` no longer refusing, so a *truthy* dict makes ``if not
        tokens:`` read "we measured something". Unmeasured collapsing into zero is the
        defect this module exists to prevent.

        The parameter is not load-bearing at runtime (nothing reads ``get_args`` of a
        ``Measured``), and a type checker resolves ``Measured[int]`` against
        ``Generic[T]`` rather than this method, so annotations still check.
        """
        return cls

    # ── construction ──────────────────────────────────────────────────────────

    @classmethod
    def of(cls, value: T, relation: Relation = Relation.exact) -> Measured[T]:
        """A measured value. Rejects ``None``, ``nan``, and ``inf``."""
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
        """``numerator / denominator``. A zero denominator is no rate, not a rate of
        zero, so it returns unmeasured.
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
        """The value, or raise :class:`NotMeasured`."""
        if self.state is not State.measured:
            raise NotMeasured(f"{self.state.value}: {self.why}")
        assert self.raw is not None  # guaranteed by of()
        return self.raw

    def or_else(self, default: U) -> T | U:
        """The value, or ``default``. Display fallbacks only, never arithmetic."""
        return self.raw if self.state is State.measured else default  # type: ignore[return-value]

    def __bool__(self) -> bool:
        raise TypeError(
            "a Measured has no truth value: `if rate:` is False for a measured 0.0 "
            "and False for no measurement at all, and those are opposite "
            "conclusions. Test .is_measured, or compare .value explicitly."
        )

    # ── combination: absence propagates, it does not default ──────────────────

    def map(self, fn: Callable[[T], U]) -> Measured[U]:
        """Apply ``fn`` if measured; carry the reason and relation through."""
        if self.state is not State.measured:
            return Measured(state=self.state, why=self.why, relation=self.relation)
        return Measured.of(fn(self.value), relation=self.relation)

    def combine(
        self, other: Measured[U], fn: Callable[[T, U], object], *, what: str
    ) -> Measured[object]:
        """Combine two quantities. Unmeasured if either side is."""
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
        """The one permitted way to turn a quantity into display text: absent
        quantities never render as numbers and bounds keep their relation.
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
    """Import-time: absent states carry no value and require a reason."""
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
