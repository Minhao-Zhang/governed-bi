"""``Population``: the row set a metric was computed over (L-R3).

Metrics and significance tests take the same object. Unit ids unique; missing
outcome fields are unmeasured, not failed; zero-row rates are unmeasured.
:meth:`restrict` records filters for comparable populations.
"""


from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from ..register.quantity import Measured

__all__ = ["Population", "TurnRow"]

#: One recorded turn. Named apart from ``ports.Row`` (a database result row,
#: ``tuple[Any, ...]``): two different types under one import name is the defect
#: ``tools/check_one_implementation.py`` exists to catch.
TurnRow = Mapping[str, object]


@dataclass(frozen=True)
class Population:
    """A set of rows, its provenance, and the metrics computed over exactly it.

    Construct with :meth:`of`. ``filtered_by`` accumulates through :meth:`restrict`
    and is what makes two populations comparable-or-not as a checkable fact.
    """

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
        """Build a population; raise on missing or duplicated unit id."""
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
                f"e.g. {dupes[:3]}. v1 merged 1025 rows and 326 rows into one arm "
                "score this way, double-weighting the overlap. If duplication is "
                "intended, aggregate before constructing the population."
            )
        return cls(label=label, unit_key=unit_key, rows=materialised)

    # ── shape ────────────────────────────────────────────────────────────────

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def units(self) -> frozenset[str]:
        return frozenset(str(r[self.unit_key]) for r in self.rows)

    def by_unit(self) -> dict[str, TurnRow]:
        """Rows keyed by unit id. Safe because :meth:`of` rejected duplicates."""
        return {str(r[self.unit_key]): r for r in self.rows}

    def restrict(self, predicate: Callable[[TurnRow], bool], label: str) -> Population:
        """A sub-population, with the filter recorded.

        ``label`` is what :func:`~.stats.mcnemar` compares to decide whether two
        populations are the same population, so it must describe the *filter*, not the
        intent — "excluded crashes" rather than "cleaned".
        """
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

    # ── metrics, computed here so they cannot be computed elsewhere ───────────

    def coverage(self, field: str) -> Measured[float]:
        """Share of rows carrying a non-``None`` ``field``.

        Reported alongside :meth:`rate` rather than folded into it: "60% correct" and
        "60% correct of the 70% we could read" are different claims, and a single
        number cannot carry both.
        """
        present = sum(1 for r in self.rows if r.get(field) is not None)
        return Measured.rate(present, self.n, what=f"{field} coverage in {self.label!r}")

    def count(self, outcome: str) -> Measured[int]:
        """Number of rows where ``outcome`` is truthy, or unmeasured if any is absent.

        The absent case is not a zero. An arm whose instrumentation dropped
        ``correct`` on 40% of rows has an *unknown* score, and v1 reported it as a
        low one.
        """
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
        """``count(outcome) / n``. Unmeasured when the count is, or when ``n`` is 0."""
        counted = self.count(outcome)
        if not counted.is_measured:
            return Measured.unmeasured(counted.why)
        return Measured.rate(counted.value, self.n, what=f"{outcome} rate in {self.label!r}")

    def describe(self) -> str:
        """One line naming the population, for putting beside any number from it.

        Exists so quoting a rate without its population takes deliberate effort.
        """
        trail = " -> ".join(self.filtered_by) if self.filtered_by else "unfiltered"
        return f"{self.label!r} n={self.n} ({trail})"


def _assert_absent_outcome_is_not_zero() -> None:
    """Import-time guard: an absent outcome must stay unmeasured, not become a zero.

    Rewriting :meth:`count` as ``sum(1 for r in rows if r.get(outcome))`` looks like a
    cleanup and passes any test built from complete rows. This fails the import instead.
    """
    probe = Population.of("probe", [{"question_id": "a", "correct": True}, {"question_id": "b"}])
    if probe.count("correct").is_measured:  # pragma: no cover - import-time guard
        raise AssertionError(
            "Population.count treated an absent outcome as a value; absent is not zero"
        )
    if probe.rate("correct").is_measured:  # pragma: no cover - import-time guard
        raise AssertionError("Population.rate reported a rate over incomplete outcomes")


_assert_absent_outcome_is_not_zero()
