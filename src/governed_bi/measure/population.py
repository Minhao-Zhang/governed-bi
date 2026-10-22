"""``Population``: the row set a metric was computed over (L-R3).

Metrics and significance tests take the same object. Unit ids unique; missing
outcome fields are unmeasured, not failed; zero-row rates are unmeasured.
:meth:`restrict` records filters for comparable populations.
"""


from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from ..registry.quantity import Measured

__all__ = ["Population", "TurnRow"]

#: One recorded turn. Named apart from ``ports.Row`` (a database result row,
#: ``tuple[Any, ...]``): two different types must not share one import name.
TurnRow = Mapping[str, object]


@dataclass(frozen=True)
class Population:
    """``filtered_by`` accumulates through ``restrict`` and is what a paired test compares."""

    label: str
    unit_key: str
    rows: tuple[TurnRow, ...]
    filtered_by: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        label: str,
        rows: Sequence[TurnRow],
        *,
        unit_key: str = "question_id",
    ) -> Population:
        materialised = tuple(rows)
        missing = [i for i, r in enumerate(materialised) if r.get(unit_key) is None]
        if missing:
            raise ValueError(
                f"population {label!r}: {len(missing)} row(s) have no {unit_key!r} "
                f"(first at index {missing[0]}). A row that cannot be identified "
                "cannot be paired, and an unpairable row silently drops out of a "
                "paired test rather than failing it."
            )
        counts = Counter(str(r[unit_key]) for r in materialised)
        dupes = sorted(u for u, n in counts.items() if n > 1)
        if dupes:
            raise ValueError(
                f"population {label!r}: {len(dupes)} duplicated {unit_key} value(s), "
                f"e.g. {dupes[:3]}. v1 merged 1025 rows and 326 rows into one config "
                "score this way, double-weighting the overlap. If duplication is "
                "intended, aggregate before constructing the population."
            )
        return cls(label=label, unit_key=unit_key, rows=materialised)

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def units(self) -> frozenset[str]:
        return frozenset(str(r[self.unit_key]) for r in self.rows)

    def by_unit(self) -> dict[str, TurnRow]:
        """Safe because ``of`` rejected duplicates."""
        return {str(r[self.unit_key]): r for r in self.rows}

    def restrict(self, predicate: Callable[[TurnRow], bool], label: str) -> Population:
        """``label`` must describe the filter, not the intent. A paired test compares it."""
        if not label:
            raise ValueError(
                "restrict() requires a label: an unlabelled filter makes two "
                "populations look identical when they are not, which is the exact "
                "failure this class exists to prevent."
            )
        kept = tuple(r for r in self.rows if predicate(r))
        return Population(
            label=self.label,
            unit_key=self.unit_key,
            rows=kept,
            filtered_by=self.filtered_by + (label,),
        )

    def coverage(self, field: str) -> Measured[float]:
        """Kept beside ``rate``: a hit rate and a hit rate among readable rows are different claims."""
        present = sum(1 for r in self.rows if r.get(field) is not None)
        return Measured.rate(present, self.n, what=f"{field} coverage in {self.label!r}")

    def count(self, outcome: str) -> Measured[int]:
        """An absent outcome is unmeasured, not zero."""
        absent = sum(1 for r in self.rows if r.get(outcome) is None)
        if absent:
            return Measured.unmeasured(
                f"{absent}/{self.n} rows in {self.label!r} have no {outcome!r}; "
                "an absent outcome is not a negative one"
            )
        if self.n == 0:
            return Measured.unmeasured(f"{self.label!r} is empty")
        return Measured.of(sum(1 for r in self.rows if r[outcome]))

    def rate(self, outcome: str) -> Measured[float]:
        counted = self.count(outcome)
        if not counted.is_measured:
            return Measured.unmeasured(counted.why)
        return Measured.rate(counted.value, self.n, what=f"{outcome} rate in {self.label!r}")

    def describe(self) -> str:
        """So quoting a rate without its population takes deliberate effort."""
        trail = " -> ".join(self.filtered_by) if self.filtered_by else "unfiltered"
        return f"{self.label!r} n={self.n} ({trail})"


def _assert_absent_outcome_is_not_zero() -> None:
    """``sum(1 for r in rows if r.get(outcome))`` looks like a cleanup and passes tests on complete rows."""
    probe = Population.of("probe", [{"question_id": "a", "correct": True}, {"question_id": "b"}])
    if probe.count("correct").is_measured:  # pragma: no cover - import-time guard
        raise AssertionError(
            "Population.count treated an absent outcome as a value; absent is not zero"
        )
    if probe.rate("correct").is_measured:  # pragma: no cover - import-time guard
        raise AssertionError("Population.rate reported a rate over incomplete outcomes")


_assert_absent_outcome_is_not_zero()
