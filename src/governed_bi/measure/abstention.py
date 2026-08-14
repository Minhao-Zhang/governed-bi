"""What the engine's declines would have been worth, over the subset that can be priced.

Separated from :mod:`.selective` because the denominators are different, and this one
is not the arm. ``open-work.md`` §4.1: of v4's 73 declines, **62 can be priced and 11
cannot** -- the dataset ships no gold fingerprint for those, so what the engine would
have got is *unknowable*, not zero. Abstention precision is therefore a figure about a
subset the dataset selected, and quoting it as though the denominator were the arm is
the mistake this module is shaped to prevent.

The shape: :class:`PricedAbstention` stores two populations and no float, and the one
thing it derives -- :class:`WouldHaveBeenWrong` -- stores a numerator and a denominator
and no float either. There is no attribute anywhere on either object that hands back the
bare rate, so a caller who wants ``0.7742`` has to divide two integers it is holding at
the time.

**This is the second attempt.** The first stored the populations and returned the rate as
a ``Measured[float]``, and ``.would_have_been_wrong.value`` handed over ``0.7742`` with
nothing attached -- as did ``.render(4)``. The claim "the rate cannot be obtained without
its denominator" was true of the *object* and false of the thing the object returned,
which is the more useful half.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..register.quantity import Measured
from .population import Population
from .selective import DECLINED

__all__ = ["PricedAbstention", "WouldHaveBeenWrong"]


@dataclass(frozen=True)
class WouldHaveBeenWrong:
    """A count of wrong counterfactuals and the priced set it is out of. No rate.

    Two integers and a population description. The rate is not stored, not exposed as a
    property, and not obtainable by attribute access -- :meth:`share` exists but takes
    the denominator's own value as an argument, so a caller has to have read it. That is
    the difference between a type that *documents* a denominator and one that carries it.

    :meth:`render` is the ordinary way to display this, and it prints both numbers beside
    the rate. Formatting goes through ``Measured.render``, which is the only permitted
    formatting site in ``src/`` (``tools/check_measurement_locality.py``).
    """

    #: Priced declines whose re-executed statement missed gold. ``None`` when unmeasured.
    wrong: int | None
    #: Declines the dataset could price at all -- **not** the count of declines.
    priced: int
    #: ``Population.describe()`` of the priced set, so the filter trail travels too.
    population: str
    #: Non-empty when there is no measurement. Both :meth:`render` and :meth:`share` say so.
    why_unmeasured: str = ""

    @property
    def is_measured(self) -> bool:
        return not self.why_unmeasured and self.wrong is not None

    def share(self, of_priced: int) -> Measured[float]:
        """The rate, computed against the denominator **the caller passed in**.

        Refuses a denominator that is not this object's, which is the whole point: the
        figure over 62 priced declines cannot be re-presented as a figure over 73
        declines, or over 1 351 turns, by a caller that never looked at
        :attr:`priced`. ``open-work.md`` §4.1 is about exactly that slippage.
        """
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
        """The rate with both of its numbers, or the reason there is none."""
        if not self.is_measured:
            return Measured.unmeasured(
                self.why_unmeasured or "no priced decline carries a counterfactual grade"
            ).render(places)
        return f"{self.share(self.priced).render(places)} ({self.wrong}/{self.priced})"


@dataclass(frozen=True)
class PricedAbstention:
    """Abstention precision that cannot be quoted without its denominator.

    ``open-work.md`` §4.1: of v4's 73 declines, 62 can be priced and 11 cannot, because
    the dataset ships no gold fingerprint for them -- so what the engine would have got
    is *unknowable*, not zero. The rate is therefore about a subset the dataset
    selected, and the way to stop it travelling alone is to not store it. There is no
    float on this object, only two populations; the rate is derived from the priced one
    on demand, and :meth:`render` prints the denominator with it.

    Build with :meth:`of`. The constructor refuses a ``priced`` population that is not a
    restriction of ``declined``, so a caller cannot assemble a numerator and a
    denominator that never met.
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
    def of(cls, arm: Population) -> PricedAbstention:
        """Split a graded arm into its declines and the priceable subset of them."""
        declined = arm.restrict(lambda r: r.get("outcome") in DECLINED, "declined turns only")
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
        """Priced declines whose re-executed statement missed gold, over the priced set.

        ``computed_correct``, never ``correct``: ``docs/measurement.md`` is explicit that
        the two are never folded, because an engine that would not commit to a statement
        gets no credit for it. This asks the counterfactual question instead, and only
        where the counterfactual exists.

        Returns two integers rather than a rate. ``.value`` used to be a bare
        ``0.7741935483870968`` and ``.render(4)`` a bare ``"0.7742"``; both travelled
        without the 62 they were over, which is the one thing §4.1 asked for.
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
