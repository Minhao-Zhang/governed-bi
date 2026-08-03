"""Provider token usage priced in dollars, or a stated reason there is no price.

**The v1 incident, quoted from ``docs/lessons-from-v1.md`` §5 rather than
paraphrased:**

    **A stale price entry overstated a measured run nine-fold** (``gpt-5.6-luna``
    at ``(2.0, 8.0)``, matching neither the new price nor the old). Both 2026-07  [retired]
    Anthropic ladders produced **no USD at all** because no Claude models were in
    the table. Unknown model → ``None``, never 0.

Two failures in one sentence, pulling opposite ways: a number that was wrong, and no
number where one was wanted. Being more careful with the table answers neither, so:

* **Wrong number** → every row carries the date it was read and where from, both
  mandatory at construction, and a row outside a validity window around the date
  being priced refuses instead of answering.
* **No number** → :class:`~governed_bi.register.quantity.Measured` carries the
  reason, so "no USD at all" reaches the artifact as ``not measured (no price for
  'Claude-Opus-4.8': ...)`` rather than as a null nobody can act on. Never ``0.0``:
  an unknown rate makes a cost unknown, not free.

**The staleness decision.** Three options, and the reason for the one chosen.

*Do nothing* is v1. The luna row had no date, so nothing could tell a current rate
from one two repricings old, and the overstatement survived because staleness had no
representation at all.

*Report the age beside the cost* is available — :func:`price_age_days` exists for
exactly that, and a report showing both is better than one showing only dollars. It
cannot be the whole answer: it puts the decision in whichever report remembers to
call it, and *"the fix landed where it was found and never reached the adjacent
copies"* is v1's own stated reason its numeric defects recurred.

*Refuse outside a window* is what is implemented, with :func:`price_age_days`
alongside. Two properties earn their keep:

The window is **symmetric**, not a one-sided age cap, because a rate observed long
*after* a run is as wrong as one observed long before — and this repo has the proof.
gpt-5.6 Luna was repriced **-80% on 2026-07-30**
(``docs/v1/experiments/20260801-three-model-ladder.md``), so today's rate understates
the week before it by a factor of five. A backward-only cap would have priced July's
ladder at August's rate and called it measured.

The window is **operational, not empirical**. 180 days is not a claim about how often
providers reprice; one of the two repricings observed here moved 80% in a day, so no
interval is safe. It is a claim about how long a number may go unverified before it
stops being a measurement — two quarters, so a row survives at most one missed
review. Nothing derives it from data, and pretending otherwise would be its own
fabricated number.

**The wall clock is not consulted.** ``asof`` is required and has no default. A
``date.today()`` default would make one run cost different amounts depending on the
day it was scored — v1's own recorded objection to modelling DeepSeek's peak-hour
surcharge — and would make every test here time-dependent. The date a run happened is
a property of the run.

**Cache reads, and the subset relation as checked rather than assumed.**
``cache_read_tokens`` is a *subset* of ``input_tokens``, so pricing both at full rate
double-charges the cached share. Over the **4,214** usage records in
``runs/**/generations*.jsonl`` that report the field (2026-08-03), ``cache_read <=
input`` holds in **4,214 of 4,214** and ``total == input + output`` in all of them —
the total does not add the cached count on top, which is what settles it. So the fresh
share is ``input - cache_read``.

``cache_read > input`` is **unmeasured**, not clamped. v1 clamped at zero because a
negative cost "reads as a credit" — true, but clamping keeps answering after the
identity this function rests on has been contradicted, and the answer is then wrong
by an unknown amount in an unknown direction.

An **absent** ``cache_read_tokens`` field prices as nothing cached, which is a
measurement here rather than an assumption: 14,881 of the 19,095 records on disk omit
it, they are the Anthropic and early OpenAI ladders, and ``register.citations``
records ``cache_read 0 across 49,401,157 input tokens`` on the Anthropic path — no
``cache_control`` breakpoint existed in v1's source at all.

**Every estimate is a floor and says so**, carrying
:attr:`~governed_bi.register.quantity.Relation.at_least` so it renders as ``>= 12.17``
and cannot be read as an invoice. Two billed terms are unmodelled and both push the
same way: **cache writes** bill above the fresh input rate (1.25x on the Anthropic
path) and are indistinguishable from fresh input in a payload that reports only
``cache_read``; **surcharge tiers** such as DeepSeek's peak-hour doubling are left out
for the clock-dependence reason above. List prices, one currency per row, no batch or
fast-mode tiers — a crude estimator, with each row's ``note`` saying what it omits.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final

from ..register.quantity import Measured, Relation, State

__all__ = [
    "Price", "PRICE_TABLE", "PRICE_VALIDITY_DAYS",
    "price_age_days", "estimate_cost", "estimate_run_cost",
]

#: Half a year, in days. See the module docstring for why this is an operational
#: choice and not a measured one, and why the window is symmetric.
PRICE_VALIDITY_DAYS: Final[int] = 180

_TOKENS_PER_UNIT: Final[int] = 1_000_000

_INPUT: Final[str] = "input_tokens"
_OUTPUT: Final[str] = "output_tokens"
_CACHE_READ: Final[str] = "cache_read_tokens"

#: A usage record as the provider hands it over and as it lands in
#: ``generations*.jsonl``. A mapping and not a dataclass on purpose: the field set
#: differs by provider, so a dataclass would have to pick a default for whatever a
#: provider omits — and a default is what this file exists to refuse. An absent field
#: must reach :meth:`Measured.unmeasured` under its own name.
Usage = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Price:
    """One model's published rates, with the date and the place they were read.

    ``__post_init__`` makes an undated or unsourced row **impossible to construct**
    rather than merely discouraged, and enforcing it here rather than in a scan over
    :data:`PRICE_TABLE` covers the case a table scan cannot: a row built by a caller
    and injected through the ``table`` argument.

    Each rate is a ``Measured``, never a bare ``float``, so a rate nobody sourced has
    somewhere to go. ``unmeasured`` means *nobody established it*, and the cost is
    then unknown; ``inapplicable`` means *this model provably has no such charge* —
    an embeddings response has no completion tokens — and only the latter may
    contribute zero to a total.
    """

    #: The provider's model id, exactly as it appears in a run manifest.
    model: str
    input_per_mtok: Measured[float]
    output_per_mtok: Measured[float]
    cache_read_per_mtok: Measured[float]
    #: ISO 4217. Rows in different currencies are never summed — every entry point
    #: here prices one model at a time.
    currency: str
    #: ISO date the rate was read from :attr:`source`. Not the date the provider
    #: announced it: what is being recorded is when *this repo* last looked.
    observed: str
    #: A URL, or a repo-relative artifact path. Same rule as
    #: ``register.citations.Citation.artifact``, for the same reason.
    source: str
    note: str = ""

    def __post_init__(self) -> None:
        missing = [f for f in ("model", "currency", "observed", "source") if not getattr(self, f)]
        if missing:
            raise ValueError(
                f"Price({self.model!r}) is missing {missing}. A rate with no date and "
                "no source is the v1 price table: unfalsifiable, and wrong by 9x for "
                "as long as nobody happened to check."
            )
        try:
            date.fromisoformat(self.observed)
        except ValueError as err:
            raise ValueError(
                f"Price({self.model!r}).observed={self.observed!r} is not an ISO date: "
                f"{err}. An unparseable date cannot be compared, so the validity "
                "window would silently never fire."
            ) from err

    def observed_on(self) -> date:
        return date.fromisoformat(self.observed)


def _usd(rate: float) -> Measured[float]:
    return Measured.of(rate)


_NO_COMPLETION = Measured.inapplicable("an embeddings response has no completion tokens")
_NO_CACHE_TIER = Measured.inapplicable("the embeddings endpoint publishes no cached-input rate")
_READ_ON = "2026-08-03"

#: Every model this repo has actually run, from the ``model`` field in
#: ``runs/index.jsonl`` and ``runs/**/manifest.json``. Nothing speculative: a model
#: never served has no row, and asking for one returns ``unmeasured``, which is both
#: correct and cheaper than a guess.
PRICE_TABLE: Final[Mapping[str, Price]] = {
    p.model: p
    for p in (
        Price(
            "gpt-5.6-luna", _usd(0.20), _usd(1.20), _usd(0.02), "USD", _READ_ON,
            "https://benchlm.ai/openai/api-pricing",
            note="The row v1 got wrong. Cross-checked against this repo's own record of the 2026-07-30 "
                 "repricing (-80%, from $1/$6) in docs/v1/experiments/20260801-three-model-ladder.md, which "
                 "independently states $0.20/$1.20. Caching is automatic on the OpenAI-compatible path, where "
                 "v1 measured 55-58% hit rates.",
        ),
        Price(
            "deepseek-v4-flash", _usd(0.14), _usd(0.28), _usd(0.0028), "USD", _READ_ON,
            "https://api-docs.deepseek.com/quick_start/pricing/",
            note="A cache hit is a fiftieth of a miss here, so the cached share dominates this estimate's "
                 "accuracy. EXCLUDES the peak-hour surcharge (2x in two Beijing-time windows): a "
                 "clock-dependent price would make one run cost different amounts depending on when it was "
                 "scored, so the estimate is a floor instead.",
        ),
        Price(
            "Claude-Opus-4.8", _usd(5.00), _usd(25.00), _usd(0.50), "USD", _READ_ON,
            "https://platform.claude.com/docs/en/about-claude/models/overview",
            note="Absent from v1's table entirely, which is why both 2026-07 Anthropic ladders reported no "
                 "USD. v1's later fix wrote 15.00/75.00/1.50 — a THIRD stale entry, 3x over the "  # [retired]
                 "published rate, and commit 4567eeb left it wrong while editing that very line to add "
                 "the cache rate, under the message 'use real prices'. Standard tier only: fast mode "
                 "and the Batch API bill differently.",
        ),
        Price(
            "text-embedding-3-large", _usd(0.13), _NO_COMPLETION, _NO_CACHE_TIER, "USD", _READ_ON,
            "https://developers.openai.com/api/docs/pricing",
            note="Every ladder embeds through this model, so omitting it understates every run. Output and "
                 "cache rates are INAPPLICABLE rather than unknown: no completion tokens are billed and no "
                 "cached tier is published. OpenAI's model card and pricing page have been reported to "
                 "disagree on this figure, so this is the likeliest row here to be wrong; the batch tier "
                 "(half price) is not modelled.",
        ),
    )
}


def _row(model: str, table: Mapping[str, Price] | None) -> Price | None:
    """The row for ``model``, matched exactly or case-insensitively.

    **No prefix matching.** v1 fell back to ``model.startswith(key)``: with both
    ``gpt-5.6-luna`` and a hypothetical ``gpt-5.6`` present, an unrecognised sibling
    silently acquires whichever row iteration reached first. A wrong price is the
    failure this module exists to prevent and ``unmeasured`` is not, so refusing a
    dated variant id is the cheaper mistake.
    """
    rows = PRICE_TABLE if table is None else table
    if not model:
        return None
    row = rows.get(model)
    if row is not None:
        return row
    folded = model.casefold()
    for key, candidate in rows.items():
        if key.casefold() == folded:
            return candidate
    return None


def price_age_days(
    model: str, *, asof: date, table: Mapping[str, Price] | None = None
) -> Measured[int]:
    """How long before ``asof`` this model's rate was observed.

    Negative when the rate was observed *after* the date being priced — a reportable
    condition, not an error; see the module docstring on why the window is symmetric.
    Publish this beside any cost going into a report someone will quote.
    """
    row = _row(model, table)
    if row is None:
        return Measured.unmeasured(f"no price row for model {model!r}, so it has no age")
    return Measured.of((asof - row.observed_on()).days)


def _outside_window(row: Price, asof: date) -> Measured[float] | None:
    days = (asof - row.observed_on()).days
    if abs(days) <= PRICE_VALIDITY_DAYS:
        return None
    direction = "before" if days > 0 else "after"
    return Measured.unmeasured(
        f"the rate for {row.model!r} was observed {row.observed}, "
        f"{abs(days)} days {direction} the {asof} being priced — outside the "
        f"{PRICE_VALIDITY_DAYS}-day validity window. Re-read {row.source} and date "
        "the row, or price this run against a row contemporary with it. gpt-5.6 Luna "
        "moved 80% in one day, so an unverified rate is not a measured one."
    )


def _count(usage: Usage, field: str, *, required: bool) -> Measured[int]:
    """One token count, or the reason it is not a count.

    ``required=False`` is only for ``cache_read_tokens``, and yields a measured zero
    when the field is absent — justified by what the artifacts show, not convenience.
    """
    if field not in usage:
        if required:
            return Measured.unmeasured(f"the usage record has no {field!r}")
        return Measured.of(0)
    raw = usage[field]
    if isinstance(raw, Measured):
        # The producer already said there was no count, with a reason. Carrying that reason
        # through is the whole contract of the type: re-deriving one here would replace
        # "the provider returned no usage_metadata" with "not an integer", and the second
        # sentence sends the reader to the wrong system.
        return raw if raw.is_measured else Measured.unmeasured(raw.why)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return Measured.unmeasured(
            f"{field!r} is {type(raw).__name__} {raw!r}, not an integer token count"
        )
    if raw < 0:
        return Measured.unmeasured(f"{field!r} is negative ({raw}), so it is not a count")
    return Measured.of(raw)


def _charge(tokens: Measured[int], rate: Measured[float], *, what: str) -> Measured[float]:
    """``tokens * rate``, with absence propagated by :meth:`Measured.combine`.

    An ``inapplicable`` rate contributes ``0.0`` and the token count is not read at
    all. This is the one place a zero is produced deliberately, and it is sound only
    because ``inapplicable`` is *declared* in the table with a reason: "this charge
    provably does not exist" is not "we do not know it", which is the whole point of
    the third state.
    """
    if rate.state is State.not_applicable:
        return Measured.of(0.0)
    return tokens.combine(  # type: ignore[return-value]
        rate, lambda n, r: n * r / _TOKENS_PER_UNIT, what=what
    )


def _add(left: object, right: object) -> float:
    return float(left) + float(right)  # type: ignore[arg-type]


def estimate_cost(
    model: str, usage: Usage, *, asof: date, table: Mapping[str, Price] | None = None
) -> Measured[float]:
    """What one provider call cost, or why that is not known.

    ``asof`` is the date the call was made; see the module docstring on why it has no
    default. ``table`` exists so a caller — in practice a test — can drive this
    against rates it chose, which is the only way to assert the arithmetic without
    re-deriving it from :data:`PRICE_TABLE`.
    """
    row = _row(model, table)
    if row is None:
        return Measured.unmeasured(
            f"no price for model {model!r}: it is absent from the dated price table, "
            "and a call at an unknown rate cost an unknown amount, not nothing. Add a "
            "row with the rate, the date you read it and where — or leave the rate "
            "absent, which is still an honest table."
        )
    stale = _outside_window(row, asof)
    if stale is not None:
        return stale

    fresh_and_cached = _count(usage, _INPUT, required=True)
    completion = _count(usage, _OUTPUT, required=True)
    cached = _count(usage, _CACHE_READ, required=False)

    if (
        fresh_and_cached.is_measured and completion.is_measured
        and fresh_and_cached.value == 0 and completion.value == 0
    ):
        return Measured.unmeasured(
            "the usage record reports zero input and zero output tokens, which is a "
            "provider that reported nothing rather than a call that cost nothing. v1 "
            "priced this shape at 0.0 on live turns making two real model calls, and "
            "the measured zero then passed every null check downstream and dragged the "
            "arm's cost total down as an observation."
        )
    if (
        fresh_and_cached.is_measured and cached.is_measured
        and cached.value > fresh_and_cached.value
    ):
        return Measured.unmeasured(
            f"{_CACHE_READ!r} ({cached.value}) exceeds {_INPUT!r} "
            f"({fresh_and_cached.value}), so cache reads are not a subset of input here "
            "and the fresh share cannot be derived. All 4,214 records on disk that "
            "report both satisfy the subset relation; this one does not, and a clamped "
            "difference would answer confidently while wrong by an unknown amount."
        )

    fresh = fresh_and_cached.combine(cached, lambda i, c: i - c, what="fresh input tokens")
    charges = (
        _charge(fresh, row.input_per_mtok, what="fresh input charge"),
        _charge(cached, row.cache_read_per_mtok, what="cache-read charge"),
        _charge(completion, row.output_per_mtok, what="output charge"),
    )
    total: Measured[float] = Measured.of(0.0)
    for charge in charges:
        total = total.combine(charge, _add, what="the total charge")  # type: ignore[assignment]
    return total.bounded(Relation.at_least) if total.is_measured else total


def estimate_run_cost(
    model: str,
    usages: Iterable[Usage],
    *,
    asof: date,
    table: Mapping[str, Price] | None = None,
) -> Measured[float]:
    """What a whole run of calls cost, or why that is not known.

    One unpriceable record makes the run unpriceable — :meth:`Measured.combine`'s
    behaviour, and the behaviour wanted: v1 summed a cost table where one model was
    missing and published the total, which was the sum of the models it knew.
    """
    total: Measured[float] = Measured.of(0.0)
    seen = 0
    for usage in usages:
        seen += 1
        one = estimate_cost(model, usage, asof=asof, table=table)
        total = total.combine(one, _add, what=f"the run total (record {seen})")  # type: ignore[assignment]
    if seen == 0:
        return Measured.unmeasured(
            "no usage records: a run with nothing recorded has no measured cost. v1's "
            "sum_token_usage([]) returned a dict of zeros and priced a whole run as "
            "free, and the dict is truthy, so the guard beside it never fired."
        )
    return total.bounded(Relation.at_least) if total.is_measured else total


def _assert_every_price_is_dated_and_sourced() -> None:
    """Import-time invariants over the table. None of them definitional.

    :meth:`Price.__post_init__` already makes a row without a date or a source
    unconstructable, so that is not re-checked here — it would be asserting the module
    against its own constructor. These are the properties a constructor cannot see,
    because each is about the table rather than a row.
    """
    for key, row in PRICE_TABLE.items():
        # A hand-written entry would be looked up under a name whose row describes a
        # different model, and the lookup is keyed.
        if key != row.model:  # pragma: no cover - import-time guard
            raise AssertionError(f"price keyed {key!r} holds rates for {row.model!r}")
        for field in ("input_per_mtok", "output_per_mtok", "cache_read_per_mtok"):
            rate = getattr(row, field)
            if not isinstance(rate, Measured):  # pragma: no cover - import-time guard
                raise AssertionError(
                    f"{key}.{field} is {type(rate).__name__}, not Measured — a bare "
                    "float cannot express a rate nobody sourced, and this is the very "
                    "field where v1 needed to"
                )

    # Lookup is case-insensitive, so two rows differing only in case would resolve by
    # iteration order — a silent choice between two prices.
    folded = [k.casefold() for k in PRICE_TABLE]
    collisions = sorted({k for k in folded if folded.count(k) > 1})
    if collisions:  # pragma: no cover - import-time guard
        raise AssertionError(f"price keys collide case-insensitively: {collisions}")


_assert_every_price_is_dated_and_sourced()
