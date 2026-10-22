"""Denominators here are not the config. Unpriceable declines are unknowable, not zero.

The rate is not stored. A caller who wants it divides two integers it is holding.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..registry.quantity import Measured
from .population import Population
from .selective import DECLINED

__all__ = ["PricedAbstention", "WouldHaveBeenWrong"]


@dataclass(frozen=True)
class WouldHaveBeenWrong:
    """The rate is not stored. ``share`` takes the denominator, so the caller has to have read ``priced``."""

    #: ``None`` when unmeasured.
    wrong: int | None
    #: Declines the dataset could price. Not the count of declines.
    priced: int
    population: str
    why_unmeasured: str = ""

    @property
    def is_measured(self) -> bool:
        return not self.why_unmeasured and self.wrong is not None

    def share(self, of_priced: int) -> Measured[float]:
        """Refuses a denominator that is not this object's priced count."""
        if not self.is_measured:
            return Measured.unmeasured(
                self.why_unmeasured or "no priced decline carries a counterfactual grade"
            )
        if of_priced != self.priced:
            raise ValueError(
                f"this rate is over {self.priced} priced decline(s) and was asked for over "
                f"{of_priced}. A numerator and a denominator that never met is L-R3; if the "
                "wider population is the intended one, say what happens to the declines the "
                "dataset cannot price -- they are unknowable, not zero."
            )
        assert self.wrong is not None  # guaranteed by is_measured
        return Measured.rate(
            self.wrong, self.priced, what="priced declines that would have been wrong"
        )

    def render(self, places: int = 4) -> str:
        if not self.is_measured:
            return Measured.unmeasured(
                self.why_unmeasured or "no priced decline carries a counterfactual grade"
            ).render(places)
        return f"{self.share(self.priced).render(places)} ({self.wrong}/{self.priced})"


@dataclass(frozen=True)
class PricedAbstention:
    """The rate is not stored. ``priced`` must be a restriction of ``declined``.

    Unpriceable declines are unknowable, not zero.
    """

    declined: Population
    priced: Population

    def __post_init__(self) -> None:
        if self.priced.filtered_by[: len(self.declined.filtered_by)] != self.declined.filtered_by:
            raise ValueError(
                f"priced population {self.priced.describe()} is not a restriction of "
                f"{self.declined.describe()}: numerator and denominator were filtered "
                "differently, which is how a rate over a population that does not exist gets "
                "published (L-R3)."
            )
        if not self.priced.units <= self.declined.units:
            raise ValueError(
                "the priced population contains turns the engine did not decline; abstention "
                "precision over answered turns is not abstention precision."
            )

    @classmethod
    def of(cls, config: Population) -> PricedAbstention:
        declined = config.restrict(lambda r: r.get("outcome") in DECLINED, "declined turns only")
        return cls(
            declined=declined,
            priced=declined.restrict(
                lambda r: r.get("computed_correct") is not None,
                "excluded declines the dataset cannot price",
            ),
        )

    @property
    def unpriceable(self) -> int:
        return self.declined.n - self.priced.n

    @property
    def would_have_been_wrong(self) -> WouldHaveBeenWrong:
        """Uses ``computed_correct``, never ``correct``: a turn that did not commit gets no credit.

        Returns two integers. The rate is derived by the caller.
        """
        right = self.priced.count("computed_correct")
        if not right.is_measured:
            return WouldHaveBeenWrong(
                wrong=None,
                priced=self.priced.n,
                population=self.priced.describe(),
                why_unmeasured=right.why,
            )
        return WouldHaveBeenWrong(
            wrong=self.priced.n - int(right.value),
            priced=self.priced.n,
            population=self.priced.describe(),
        )

    def render(self) -> str:
        return (
            f"declines that would have been wrong: {self.would_have_been_wrong.render(4)} "
            f"over {self.priced.describe()}; {self.unpriceable} of {self.declined.n} decline(s) "
            "carry no gold fingerprint, so what the engine would have got there is unknowable, "
            "not zero"
        )
