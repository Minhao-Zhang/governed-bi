"""A quantity that may be absent, with an explicit reason.

Lives in ``registry/`` so the record can test presence without importing upward.
Truthiness and arithmetic are refused. Renaming ``Measured``, ``State``, or
``Relation`` deletes the thread registry; see ``CHECKPOINT_PICKLED_NAMES``.
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
    "CHECKPOINT_PICKLED_NAMES",
    "RENAME_DELETES_THE_THREAD_REGISTRY",
    "renamed_pickled_classes",
]

T = TypeVar("T")
U = TypeVar("U")


class State(str, Enum):
    """Should-exist versus correctly-absent. The second is declared, not inferred."""

    measured = "measured"
    not_measured = "not_measured"
    not_applicable = "not_applicable"


class Relation(str, Enum):
    """A one-sided bound must not render as a point estimate."""

    exact = "="
    at_most = "<="
    at_least = ">="


class NotMeasured(Exception):
    pass


@dataclass(frozen=True)
class Measured(Generic[T]):
    """Reason on every absence; no ``nan`` or ``inf``; no value when absent."""

    state: State
    raw: T | None = None
    why: str = ""
    relation: Relation = Relation.exact

    def __class_getitem__(cls, item: object) -> type[Measured[Any]]:
        """``Measured[int]`` is ``Measured`` at runtime so the checkpoint allowlist sees a class.

        A subscripted generic is not a dataclass, so an unregistered absence comes back as a dict and ``__bool__`` stops
        refusing.
        """
        return cls

    @classmethod
    def of(cls, value: T, relation: Relation = Relation.exact) -> Measured[T]:
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
        if not why:
            raise ValueError(
                "unmeasured() requires a reason: an unexplained absence is "
                "indistinguishable from a forgotten assignment, and the whole "
                "point of the state is that a reader can tell them apart."
            )
        return cls(state=State.not_measured, why=why)

    @classmethod
    def inapplicable(cls, why: str) -> Measured[T]:
        if not why:
            raise ValueError("inapplicable() requires a reason; see unmeasured()")
        return cls(state=State.not_applicable, why=why)

    @classmethod
    def rate(cls, numerator: float, denominator: float, *, what: str) -> Measured[float]:
        """A zero denominator is unmeasured, not a rate of zero."""
        if denominator == 0:
            return Measured(
                state=State.not_measured,
                why=f"no {what}: denominator is zero, so there is no rate to report",
            )
        return Measured.of(numerator / denominator)

    @property
    def is_measured(self) -> bool:
        return self.state is State.measured

    @property
    def value(self) -> T:
        if self.state is not State.measured:
            raise NotMeasured(f"{self.state.value}: {self.why}")
        assert self.raw is not None  # guaranteed by of()
        return self.raw

    def or_else(self, default: U) -> T | U:
        """Display fallback only, never arithmetic."""
        return self.raw if self.state is State.measured else default  # type: ignore[return-value]

    def __bool__(self) -> bool:
        raise TypeError(
            "a Measured has no truth value: `if rate:` is False for a measured 0.0 "
            "and False for no measurement at all, and those are opposite "
            "conclusions. Test .is_measured, or compare .value explicitly."
        )

    def map(self, fn: Callable[[T], U]) -> Measured[U]:
        if self.state is not State.measured:
            return Measured(state=self.state, why=self.why, relation=self.relation)
        return Measured.of(fn(self.value), relation=self.relation)

    def combine(
        self, other: Measured[U], fn: Callable[[T, U], object], *, what: str
    ) -> Measured[object]:
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
        return replace(self, relation=relation)

    def rounded(self, places: int) -> Measured[float]:
        return self.map(lambda v: round(float(v), places))  # type: ignore[arg-type]

    def render(self, places: int = 2, unit: str = "", *, scale: float = 1.0) -> str:
        """The only permitted way to turn a quantity into display text."""
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
    factories: tuple[Callable[[], Measured[float]], ...] = (
        lambda: Measured.unmeasured("probe"),
        lambda: Measured.inapplicable("probe"),
    )
    for factory in factories:
        m = factory()
        if m.raw is not None:  # pragma: no cover - import-time guard
            raise AssertionError(f"{m.state.value} carries a value: {m.raw!r}")
        if not m.why:  # pragma: no cover - import-time guard
            raise AssertionError(f"{m.state.value} has no reason")


_assert_absence_cannot_carry_a_value()


# Pickled by reference into .langgraph_api/.langgraph_ops.pckl. A rename makes the
# next boot delete the thread registry. The module path is pinned from outside, in
# tests/conformance/test_a_rename_deletes_the_thread_registry.py.
CHECKPOINT_PICKLED_NAMES: tuple[str, ...] = ("Measured", "State", "Relation")

# Import refuses until these names still bind. A comment alone did not hold.
RENAME_DELETES_THE_THREAD_REGISTRY = """\
{renamed} no longer resolves under that name in governed_bi.registry.quantity, and that name is
on disk: it is pickled by reference into .langgraph_api/.langgraph_ops.pckl on every served turn
(see CHECKPOINT_PICKLED_NAMES above for the full chain and its evidence).

On the next `langgraph dev` boot the unpickle raises, and langgraph_runtime_inmem/database.py
:start_pool catches every exception and os.remove()s the file. That deletes the
WHOLE THREAD REGISTRY, not the unreadable row -- every interrupted thread goes with it.
runs/conversations.sqlite survives, so the paused turns stay resumable while nothing can list
them: the pending-clarification queue reports an empty queue and the conversation list goes blank.
Nothing rebuilds the registry from the checkpointer.

To rename anyway, in one commit:
  1. stop the server;
  2. deal with .langgraph_api/.langgraph_ops.pckl -- either delete it yourself, having accepted
     that the open threads are gone, or migrate it with a pickle.Unpickler whose find_class()
     maps the old (module, name) to the new one and re-dump it;
  3. update CHECKPOINT_PICKLED_NAMES here;
  4. update tests/conformance/test_a_rename_deletes_the_thread_registry.py, which pins the
     module path this guard cannot see and the set of types that may reach a checkpoint.\
"""


def renamed_pickled_classes(namespace: dict[str, Any]) -> tuple[str, ...]:
    """``__qualname__`` is compared, not mere presence: an alias keeps the binding and still writes the new name.

    The namespace is an argument so a failed guard is testable.
    """
    return tuple(
        name
        for name in CHECKPOINT_PICKLED_NAMES
        if not isinstance(bound := namespace.get(name), type) or bound.__qualname__ != name
    )


_renamed = renamed_pickled_classes(globals())
if _renamed:  # pragma: no cover - import-time guard; the test drives the function directly
    raise AssertionError(
        RENAME_DELETES_THE_THREAD_REGISTRY.format(renamed=" / ".join(_renamed))
    )
del _renamed
