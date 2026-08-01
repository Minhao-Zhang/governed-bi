"""A circuit breaker for the serve loop: stop paying for a run that is already lost.

The BUILD phase has two gates that refuse to spend on a pool that cannot produce a
quotable number — :func:`~governed_bi.eval.run_datalake._assert_build_coverage` and
:func:`~governed_bi.eval.run_datalake._quarantine_curator_failures`. The SERVE phase,
which is where all the money goes, had none. Its only crash signal was
``crash_rate > 0 -> not quotable``, computed from ``summary.json`` **after the last
question**. On 2026-07-31 that meant two hours and roughly thirty dollars spent on a run
that was 48% crashed by row 655, plus a second, unrelated run that inherited 39 crashed
rows from the same exhausted token budget with nothing anywhere linking the two.

The verdict was never the problem. ``quotable()`` correctly refused the run. The problem
is that it refused it at the end.

Design constraints, both of which have teeth:

**A legitimate arm crashes a little.** The 2026-08-01 luna-max ladder's ``curated`` arm
finished with 2 crashes in 1351 rows and is a perfectly good arm. A breaker that fires
there is worse than no breaker, because the first thing an operator does with a gate that
cries wolf is pass ``--no-...`` to it forever.

**Every row is a fresh look.** Testing "is the crash rate too high yet?" after each of
1351 rows is 1351 chances to be unlucky. A fixed-sample test — an exact binomial tail
against α — is simply the wrong instrument: its α is a per-look guarantee and the
family-wise error over a whole arm is not something you can then state. Picking a very
small α and hoping is how a threshold ends up unpinned to anything, which is the class of
defect this module exists inside of.

So the test is **Page's CUSUM** over the per-row log-likelihood ratio between

    H0: crash rate <= P_TOLERABLE      H1: crash rate >= P_RUNAWAY

with the accumulator floored at zero, tripping when it reaches ``log(1 / ALPHA)``. Two
properties earn it its place here, and one claim it does NOT support is worth spelling
out because the first draft of this module made it.

**It detects a failure that starts late.** The floor at zero is what makes this a
*changepoint* detector rather than a fixed-start test: the floored cumulative sum is
identically the maximum, over every possible start row, of the unfloored sequential test
begun there. That is the incident's exact shape — an arm that ran clean and then lost its
embedding channel to a shared token budget partway through. Simulated at 600 clean rows
followed by a 48% crash rate, this trips at row 610. An unfloored test would first have to
work off 600 rows of accumulated negative evidence (about 92 nats, ~100 crashed rows) and
would not fire until row 700.

**It is O(1) per row: two adds.** An exact binomial tail recomputed per row is O(n) and
puts a growing arithmetic cost on the serve loop's critical path.

**:data:`ALPHA` is NOT a false-alarm probability here.** Ville's inequality bounds the
un-restarted sequential test at ``ALPHA``; flooring at zero restarts it at every row and
that bound does not survive. The honest characterisation is measured, seeded and pinned by
``tests/test_serve_breaker.py::test_the_operating_characteristics_are_what_the_docstring_claims``
— which fails if any constant here moves, so this table cannot go stale in silence:

====================  ============  ==================================================
true crash rate       P(ever trips)  median trip row (of 1351)
====================  ============  ==================================================
0.15% (2 of 1351)     0%            never — :data:`MIN_CRASHES` is unreachable
0.5%                  0%            never
1%                    0.75%         634
2% (P_TOLERABLE)      5.8%          656
3%                    29%           630
5%                    91%           364
10%                   100%          70
48% (the incident)    100%          20  — under 1% of the arm's spend
====================  ============  ==================================================

Read the middle of that table against what a trip actually costs. An arm at 3% crash is 40
crashed rows, and ``quotable()`` refuses any arm with a non-zero crash rate — so a "false"
alarm there stops a run whose number nobody could have published. The rate that must never
trip is the one a real healthy arm has: the 2026-08-01 luna-max ladder finished ``curated``
at 2 crashes in 1351 (0.15%) and ``baseline`` at 0, and neither can reach
:data:`MIN_CRASHES` at all.

This module is pure: no IO, no clock, no globals. The driver owns writing the marker and
raising. That is deliberate — the inline versions of the two build-phase gates had tests
that asserted arithmetic about their thresholds and never called them, so they would have
passed with the gate deleted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ALPHA",
    "MIN_CRASHES",
    "MIN_ROWS",
    "P_RUNAWAY",
    "P_TOLERABLE",
    "ServeCircuitBreaker",
    "ServeCircuitBreakerTripped",
]

#: The crash rate a healthy arm is allowed to have. Not "zero": a hosted provider hiccups,
#: and ``quotable()`` already refuses any arm with a non-zero crash rate, so this is not
#: the integrity line — it is the line between "an arm with a few bad rows" and "a run
#: that is failing". 2% of 1351 is 27 rows.
P_TOLERABLE = 0.02

#: The crash rate that means the run is gone. The incident arm was at 48%; the co-running
#: run that inherited the exhausted budget was lower but still nothing anyone would pay to
#: finish. Set an order of magnitude above :data:`P_TOLERABLE` so the two hypotheses are
#: well separated and the test converges in tens of rows rather than hundreds.
P_RUNAWAY = 0.20

#: The evidence threshold, expressed as ``1 / ALPHA`` in likelihood-ratio units. It would
#: be a false-alarm bound for the un-restarted sequential test; the zero floor restarts the
#: test at every row, so here it is a tuning constant whose consequences are the measured
#: table in the module docstring and the seeded test that pins it. Named ``ALPHA`` because
#: that is what it is in the underlying test, and the docstring says plainly what it is
#: not — an unlabelled 6.9 in the code would be worse in every way.
ALPHA = 0.001

#: A floor under the evidence threshold, in raw crashes. The sequential test alone would
#: permit a trip at 5 crashes in the first 20 rows, which is correct on the arithmetic and
#: still the wrong call on a serve loop whose first few questions can hit a cold
#: connection pool. Both floors must be cleared as well as the evidence bound.
MIN_CRASHES = 5
MIN_ROWS = 20

#: Half-way to the trip, in log-evidence: an operator-visible warning that the arm is on a
#: trajectory, emitted once. ``sqrt`` of the trip threshold in the natural scale, so it
#: sits at the geometric midpoint of the evidence rather than at an arbitrary row count.
_WARN_LOG_EVIDENCE = 0.5 * math.log(1.0 / ALPHA)
_TRIP_LOG_EVIDENCE = math.log(1.0 / ALPHA)

#: Log-likelihood ratio contributed by one crashed row and by one clean row.
_LLR_CRASH = math.log(P_RUNAWAY / P_TOLERABLE)
_LLR_CLEAN = math.log((1.0 - P_RUNAWAY) / (1.0 - P_TOLERABLE))


class ServeCircuitBreakerTripped(RuntimeError):
    """Raised by the driver when the serve breaker has decided to stop paying."""


@dataclass
class ServeCircuitBreaker:
    """Accumulates crash evidence for ONE arm, one row at a time.

    Feed it :meth:`observe` per scored row. It never raises and never prints; it answers
    :attr:`tripped` and :attr:`should_warn`, and :meth:`state` produces the block the
    driver files in the artifact. Keeping the decision separable from the consequence is
    what makes the decision testable at all.

    Not thread-safe by design: the pooled serve path calls ``on_result`` on the
    submitting thread in submission order (see
    :func:`governed_bi.eval.parallel.run_ordered_pool`), so the breaker sees exactly the
    sequence the serial loop would and needs no lock. Anything that starts calling
    :meth:`observe` from worker threads has to add one.
    """

    arm: str
    #: Total rows this arm intends to serve in this attempt, for the message only.
    total: int | None = None
    n_rows: int = 0
    n_crashed: int = 0
    log_evidence: float = 0.0
    tripped_at_row: int | None = None
    warned: bool = field(default=False, repr=False)

    @property
    def crash_rate(self) -> float | None:
        """``None`` — never ``0.0`` — before a single row, matching the summariser."""
        if not self.n_rows:
            return None
        return self.n_crashed / self.n_rows

    @property
    def tripped(self) -> bool:
        return self.tripped_at_row is not None

    def observe(self, *, crashed: bool) -> None:
        """Record one scored row. Idempotent once tripped: the verdict does not move.

        Freezing the counts at the trip is deliberate. The driver raises immediately, but
        a pooled run has tasks already in flight whose rows land afterwards, and letting
        them push ``n_rows`` up would make the marker describe a sample the decision was
        not made on.
        """
        if self.tripped:
            return
        self.n_rows += 1
        if crashed:
            self.n_crashed += 1
            self.log_evidence += _LLR_CRASH
        else:
            self.log_evidence += _LLR_CLEAN
        # The zero floor is what makes this a CHANGEPOINT detector: a long clean prefix
        # must not buy credit against a later storm. Simulated, an arm that runs 600 clean
        # rows and then loses its embedding endpoint at 48% trips at row 610 with the
        # floor and around row 700 without it. It is also what costs the Ville bound —
        # see the module docstring; do not re-derive a false-alarm probability from ALPHA.
        self.log_evidence = max(0.0, self.log_evidence)
        if not self._floors_cleared():
            return
        if self.log_evidence >= _TRIP_LOG_EVIDENCE:
            self.tripped_at_row = self.n_rows

    @property
    def should_warn(self) -> bool:
        """True once, at the geometric midpoint of the evidence. Consumes the flag.

        Deliberately NOT gated on :data:`MIN_ROWS` / :data:`MIN_CRASHES`, unlike the
        trip. Those floors exist because stopping a run is expensive to get wrong; the
        warning costs one line of output, so it should be the cheap early signal, not a
        second copy of the same conservatism. Gated on both floors it was near-dead
        code: an arm failing fast enough to matter clears the floors and the trip
        threshold on the SAME row, so the warning would never have printed on the very
        incident it exists for. Two crashes is the minimum that can carry any evidence
        at all.
        """
        if self.warned or self.tripped or self.n_crashed < 2:
            return False
        if self.log_evidence < _WARN_LOG_EVIDENCE:
            return False
        self.warned = True
        return True

    def _floors_cleared(self) -> bool:
        return self.n_rows >= MIN_ROWS and self.n_crashed >= MIN_CRASHES

    def state(self) -> dict[str, Any]:
        """The block the driver files in ``summary.json`` and in the abort marker.

        Written whether or not it tripped. A breaker that only leaves a trace when it
        fires cannot be distinguished, afterwards, from a breaker that was never wired
        up — which is the failure mode half this repo's gates have had.
        """
        return {
            "arm": self.arm,
            "n_rows_observed": self.n_rows,
            "n_crashed": self.n_crashed,
            "crash_rate": self.crash_rate,
            "tripped": self.tripped,
            "tripped_at_row": self.tripped_at_row,
            "log_evidence": round(self.log_evidence, 4),
            # The policy, beside the verdict, so an archived marker can be read without
            # the source at the revision that wrote it.
            "policy": {
                "test": "Page CUSUM on the crash/clean log-likelihood ratio",
                "p_tolerable": P_TOLERABLE,
                "p_runaway": P_RUNAWAY,
                "alpha": ALPHA,
                "min_rows": MIN_ROWS,
                "min_crashes": MIN_CRASHES,
            },
        }

    def message(self) -> str:
        """Why it stopped, and what to do — the whole text an operator gets."""
        rate = self.crash_rate or 0.0
        of_total = f" of {self.total}" if self.total else ""
        return (
            f"[{self.arm}] serve circuit breaker TRIPPED at row {self.tripped_at_row}"
            f"{of_total}: {self.n_crashed} crashed of {self.n_rows} ({rate:.1%}). "
            f"Under the run's own rules a crashed row is not a measurement and any arm "
            f"with crash_rate > 0 is refused by eval.index.quotable, so finishing this "
            f"arm buys an artifact nobody may quote. Evidence for a crash rate at or "
            f"above {P_RUNAWAY:.0%} passed 1/alpha={1 / ALPHA:.0f} against a tolerable "
            f"{P_TOLERABLE:.0%} (CUSUM; an arm at 0.15% crash — the real healthy rate on "
            f"this benchmark — cannot reach this at all). "
            f"The rows already scored are on disk and --resume will keep them. "
            f"Check the provider first — the 2026-07-31 incident was a shared token "
            f"budget exhausted by per-worker duplicate embedding, and it took down a "
            f"SECOND, unrelated run at the same time, so check what else is running "
            f"before you restart. Lower --workers, or pass --no-serve-breaker to run to "
            f"the end anyway."
        )
