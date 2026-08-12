"""Draw the risk-coverage plane for one or more eval artifacts. No model, no network.

    uv run --frozen python tools/selective_curve.py runs/eval/proxy_v4_corpus30872d3.jsonl
    uv run --frozen python tools/selective_curve.py runs/eval/proxy_v4*.jsonl --coverage 0.9 0.7

Re-analysis only: it reads rows that already exist and spends nothing. Everything it
prints comes from ``governed_bi.measure.selective`` / ``.abstention`` / ``.signals``, so
this file holds no statistics of its own -- the one thing ``tools/`` has repeatedly got
wrong is keeping a second implementation of a number (audit E1-E3, and the rival
``mcnemar`` that ``check_one_implementation.py`` now forbids by name).

Not named ``check_*``: it reports, it does not gate, and
``tests/conformance/test_register_closure.py`` requires every ``tools/check_*.py`` to
be wired into CI or declared manual.

The two comparisons at the bottom are the point of the exercise:

* **engine vs. the best signal at a lower coverage** -- what the trade actually costs,
  priced in answers the reader does not get rather than in precision alone;
* **the token count vs. the governance ledger** -- whether the layer stack's own record
  ranks turns as well as a byte counter does.

Both go through ``measure.stats.mcnemar`` and report the minimum detectable effect
beside the delta, because most differences on 1,351 questions are below the floor
(``docs/open-work.md`` §3.12).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from governed_bi.measure.abstention import PricedAbstention  # noqa: E402
from governed_bi.measure.population import Population  # noqa: E402
from governed_bi.measure.selective import (  # noqa: E402
    MIN_OPERATING_POINT,
    DeliveryPolicy,
    RiskCoverage,
    compare_policies,
    engine_policy,
    graded,
    no_ranking,
    oracle,
    risk_coverage,
)
from governed_bi.measure.signals import SIGNALS  # noqa: E402

#: Coverage levels printed by default. 0.946 is not in the list on purpose: every curve
#: passes through the engine's own operating point, so a column there would print the
#: same number nine times and read as agreement rather than as the tautology it is.
DEFAULT_COVERAGE: tuple[float, ...] = (0.9, 0.8, 0.7, 0.6, 0.5)

#: Accuracy targets. 0.90 is here because it is the number the framing hoped for.
DEFAULT_ACCURACY: tuple[float, ...] = (0.80, 0.85, 0.90)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=pathlib.Path)
    parser.add_argument("--coverage", nargs="*", type=float, default=list(DEFAULT_COVERAGE))
    parser.add_argument("--accuracy", nargs="*", type=float, default=list(DEFAULT_ACCURACY))
    parser.add_argument(
        "--trade-at", type=float, default=0.70,
        help="the coverage at which the engine is compared against the best signal",
    )
    args = parser.parse_args(argv)

    for path in args.artifacts:
        if not path.exists():
            print(f"no such artifact: {path}", file=sys.stderr)
            return 2
        _report(path, args.coverage, args.accuracy, args.trade_at)
    return 0


def _report(
    path: pathlib.Path, coverages: list[float], accuracies: list[float], trade_at: float
) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    arm = graded(Population.of(path.stem, rows))
    print(f"\n{'=' * 100}\n{path}\n{'=' * 100}")
    print(f"population: {arm.describe()}  (dropped {len(rows) - arm.n} of {len(rows)} rows)")

    engine = engine_policy(arm)
    print("\n-- the engine's operating point ---------------------------------------------")
    print(f"  {engine.point().render()}")
    print(f"  useful answers (delivered and correct): {engine.useful}")
    print(f"  {PricedAbstention.of(arm).render()}")

    curves = _curves(arm)
    _print_coincidence(engine, curves)
    _print_curve_table(curves, coverages)
    _print_targets(curves, accuracies)
    _print_partial(arm, curves, coverages)
    _print_trades(arm, engine, curves, trade_at)


def _curves(arm: Population) -> dict[str, RiskCoverage]:
    built = {"oracle": oracle(arm), "no ranking": no_ranking(arm)}
    for name, signal in SIGNALS.items():
        built[name] = risk_coverage(arm, signal)
    return built


def _print_coincidence(engine: DeliveryPolicy, curves: dict[str, RiskCoverage]) -> None:
    """Every curve meets the engine at the engine's coverage. Printed, not assumed.

    It is a structural fact -- a ranking only reorders the turns the engine already
    agreed to answer -- and it is the single most important thing on the page, because
    it says no signal here improves on the engine without withholding more.
    """
    at = engine.point().coverage.or_else(1.0)
    values = {
        curve.accuracy_at(at).accuracy.render(4)
        for curve in curves.values()
        if not curve.unavailable
    }
    print(f"\n  at the engine's own coverage ({at:.4f}), every curve reads: {sorted(values)}")
    print("  a ranking reorders the delivered turns; it cannot un-withhold a declined one.")


def _cell(point) -> str:
    """One curve cell: the accuracy and **the k it was read at**.

    The k is not decoration. A requested coverage of 0.70 on 1 351 turns is
    ``floor(0.7 * 1351) = 945`` for the curve and 944 for the largest realisable policy,
    and those give 0.7952 and 0.7956. Both are correct under their own rule and the two
    were published on one page with the second's consequence attached to the first's
    number. Printing k makes them visibly different quantities.
    """
    if point.why_absent:
        return "-"
    return f"{point.accuracy.render(4)}@{point.delivered}"


def _print_curve_table(curves: dict[str, RiskCoverage], coverages: list[float]) -> None:
    print("\n-- risk-coverage: selective accuracy at each coverage ------------------------")
    print("   rawAUC is in the artifact's own direction, not the declared one: below 0.5 means")
    print("   higher value -> more wrong, so every `lower_first` signal's mechanism claim holds")
    print("   only if its rawAUC is below 0.5. Comparable to risk-coverage-v4.md section 4.")
    head = "".join(f"{c:>14}" for c in coverages)
    print(f"\n  {'signal':<22}{'rawAUC':>8}{'AURC':>8}{'cuts':>6}{head}")
    for name, curve in curves.items():
        if curve.unavailable:
            print(f"  {name:<22}  unavailable: {curve.unavailable}")
            continue
        cells = "".join(f"{_cell(p):>14}" for p in (curve.accuracy_at(c) for c in coverages))
        cuts = len(curve.realisable_coverages())
        print(
            f"  {name:<22}{curve.auc.render(4):>8}{curve.aurc.render(4):>8}{cuts:>6}{cells}"
        )
    n = next((c.n for c in curves.values() if not c.unavailable), 0)
    print(
        f"\n  Cells read `accuracy@k`: k is floor(coverage * {n}) turns delivered, so the realised\n"
        "  coverage is k/n and not the column header. This is the CURVE, averaged through tie\n"
        "  groups -- a number, not a policy. The largest policy a signal can actually realise at\n"
        "  that coverage is a different k and is reported under `what the trade costs` below.\n"
        "  cuts = coverages the signal can express at all. A signal with 5 cuts over 1,278\n"
        "  delivered turns cannot be asked for 70% coverage."
    )


def _print_targets(curves: dict[str, RiskCoverage], accuracies: list[float]) -> None:
    print("\n-- coverage at a target accuracy ---------------------------------------------")
    for target in accuracies:
        reached, absent = [], []
        for name, curve in curves.items():
            if curve.unavailable:
                continue
            point = curve.coverage_for(target)
            (absent if point.why_absent else reached).append((name, point))
        print(f"\n  target accuracy {target}:")
        for _, point in reached:
            print(f"    {point.render()}")
        if absent:
            # Named, not dropped. "No signal reaches 0.90" is the result on this arm, and
            # a table that only lists the winners cannot say it.
            print(f"    no operating point at k >= {MIN_OPERATING_POINT}: "
                  + ", ".join(name for name, _ in absent))


def _print_partial(
    arm: Population, curves: dict[str, RiskCoverage], coverages: list[float]
) -> None:
    """Signals the arm carries on *some* delivered turns, re-run over just those.

    ``measure.selective`` refuses a curve when a signal is missing on any delivered
    turn, which is right: an AURC over 1,268 turns and one over 1,270 are not the same
    number, and putting them in one table invites the subtraction. Opting into the
    smaller population is the caller's decision to make out loud, so it happens here,
    the restriction is recorded in the population line, and ``mcnemar`` will refuse to
    compare these against anything in the table above.
    """
    partial = [name for name, c in curves.items() if c.unavailable and name in SIGNALS]
    printed = False
    for name in partial:
        signal = SIGNALS[name]
        subset = arm.restrict(
            lambda r, s=signal: r.get("outcome") != "answered" or s.read(r) is not None,
            f"delivered turns carrying {name}",
        )
        curve = risk_coverage(subset, signal)
        if curve.unavailable or not curve.ranked:
            continue
        if not printed:
            print("\n-- signals present on only part of the arm ------------------------------------")
            printed = True
        cells = "".join(f"{_cell(c):>14}" for c in (curve.accuracy_at(x) for x in coverages))
        print(f"  {name:<22}{curve.auc.render(4):>8}{curve.aurc.render(4):>8}"
              f"{len(curve.realisable_coverages()):>6}{cells}")
        print(f"    over {subset.describe()} -- NOT comparable to the table above")


def _print_trades(
    arm: Population, engine: DeliveryPolicy, curves: dict[str, RiskCoverage], trade_at: float
) -> None:
    print("\n-- what the trade costs, paired ----------------------------------------------")
    best = _best(curves)
    if best is None:
        print("  no signal is available on this arm, so there is nothing to trade against")
        return
    ranked = best.policy_at_most(trade_at)
    curve_point = best.accuracy_at(trade_at)
    print(f"  A  {engine.point().render()}")
    print(f"  B  {ranked.point().render()}")
    print(f"     {compare_policies(engine, ranked).render()}")
    print(
        "     outcome is `useful_answer` = delivered AND correct, so a policy pays for what it\n"
        "     withheld. The comparison is nested by construction -- B delivers a subset of A's\n"
        "     turns and changes no grade -- so it is reported as the arithmetic it is."
    )
    # The two conventions, side by side, because the page that quoted them mixed them up.
    if not curve_point.why_absent and curve_point.delivered != ranked.point().delivered:
        print(
            f"     NB the curve cell at coverage {trade_at} is "
            f"{curve_point.accuracy.render(4)}@{curve_point.delivered} "
            f"(coverage {curve_point.coverage.render(4)}), a different k from the realisable\n"
            f"     policy above at {ranked.point().accuracy.render(4)}@"
            f"{ranked.point().delivered} (coverage {ranked.point().coverage.render(4)}). "
            "Only the policy has a consequence attached;\n     the curve cell is averaged "
            "through the tie group that k falls inside."
        )

    ledger = curves.get("n_failed_attempts")
    if ledger is None or ledger.unavailable:
        return
    # The ledger takes five values, so it cannot be asked for 70% coverage. Comparing it
    # at a coverage it cannot express would compare it against delivering nothing, which
    # is a fact about its resolution and not about its ranking. So both signals are read
    # at the coarser one's own best cut below the engine's coverage.
    ceiling = engine.point().coverage.or_else(1.0)
    options = [c for c in ledger.realisable_coverages() if c < ceiling]
    if not options:
        print("\n  the ledger expresses no operating point below the engine's coverage at all")
        return
    matched = min(options, key=lambda c: abs(c - trade_at))
    rival = ledger.policy_at_most(matched, label=f"n_failed_attempts @ cov {matched:.4f}")
    twin = best.policy_at_most(matched, label=f"{best.signal} @ cov {matched:.4f}")
    print(f"\n  A  {twin.point().render()}")
    print(f"  B  {rival.point().render()}")
    print(f"     {compare_policies(twin, rival).render()}")
    print(
        f"     the governance ledger against the token count, both at {matched:.4f} -- the "
        f"ledger's own\n     cut nearest the requested {trade_at}. Its whole resolution is "
        f"{[f'{c:.4f}' for c in ledger.realisable_coverages()]},\n     so it cannot withhold "
        "more than a few points beyond what the engine already withheld."
    )


def _best(curves: dict[str, RiskCoverage]) -> RiskCoverage | None:
    """Lowest AURC among the real signals. Reference lines are excluded by name."""
    scored = [
        (c.aurc.value, name, c)
        for name, c in curves.items()
        if name in SIGNALS and c.aurc.is_measured
    ]
    if not scored:
        return None
    return min(scored, key=lambda entry: entry[0])[2]


if __name__ == "__main__":
    raise SystemExit(main())
