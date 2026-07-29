"""Train-vs-test gap: the overfitting measure for a corpus-as-treatment run.

Scoring the train split is not a second result. The curator reads train gold SQL —
:func:`governed_bi.curator.seed.seed_from_train_sql` extracts joins and metrics from
it, and ``_mark_columns_absent_from_gold`` derives the decoy mask from it — so a
curated arm's train EX is partly recall of statements it was built from.
:func:`governed_bi.eval.index.quotable` already refuses a train-scored run for
exactly this reason ("a diagnostic, not a result").

What the pair buys is the **gap**. ``ex(train) - ex(test)`` per arm is how much of an
arm's score does not survive being asked something new, and it is the one number
that separates the two readings of a positive curated delta:

- a *small* gap says the corpus encodes something reusable;
- a *large* gap says it encodes the training statements.

**The raw per-arm gap overstates memorisation, and by an unknown amount.** The two
splits are different questions, so part of every gap is simply that one split is
easier — and ``baseline`` proves it: that arm reads no train gold SQL at all, so it
has nothing to memorise and its gap is *pure split-composition difficulty*. Every
other arm's gap is that same composition term plus whatever it memorised. The
honest quantity is therefore the **difference of the two gaps**
(``ex_excess_gap`` = this arm's gap minus the control arm's), which cancels the
composition term to first order. It is a difference-in-differences, with the
control arm's gap standing in for the counterfactual.

Both splits must be scored against the **same** corpora. The curator is stochastic,
so a rebuild between splits mixes overfitting with curator variance and the gap stops
meaning either — which is why ``run_datalake`` takes ``corpus_dir`` separately from
``out_dir``, reuses the first split's build for the second, and why
:func:`write_split_gap` refuses to report a gap across two different
``corpus_content_hash`` values instead of printing a number that mixes the two.

The gap is a within-arm quantity and needs no shared question ids, so unlike the
ladder deltas it is not paired and carries no p-value. What it does carry is a
**standard error**: at this eval's split sizes (~2k test, ~8k train) a binomial SE on
an EX near 0.3 is about 0.010 / 0.005, so a gap much under 0.03 is indistinguishable
from sampling noise. ``ex_gap_noise_floor`` is that threshold per arm, computed from
the arm's own n's rather than quoted from these numbers, and
``ex_gap_within_noise`` applies it — a sign is informative only above it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .arms import ARM_ORDER

#: Rates worth gapping. Every one is an accuracy-like quantity where "train is
#: higher" means "did not transfer". Deliberately not every rate in the summary:
#: gapping ``crash_rate`` or ``refusal_rate`` invites reading operational noise as
#: overfitting.
#:
#: ``routing_recall`` and ``schema_pick_accuracy`` are here on purpose, and they are
#: not the same kind of quantity as the EX rates. The router index is identical
#: across the two splits — one corpus, one embedding — so nothing about *routing*
#: differs between the passes. What differs is the questions, and the index is built
#: from train material: a schema description or note that echoes train question
#: vocabulary retrieves better for train questions. So a routing gap is a
#: retrieval-side overfitting channel (the corpus text favours what it was written
#: from), never a generation-side one. Read it as "does the index also transfer",
#: and read a large one as a reason to look at the corpus prose.
GAPPED_RATES: tuple[str, ...] = (
    "ex_lenient",
    "ex_strict",
    "ex_gradeable",
    "conditional_ex_lenient",
    "cond_ex_given_routing",
    "routing_recall",
    "schema_pick_accuracy",
)

#: The ladder's control arm, taken from :data:`governed_bi.eval.arms.ARM_ORDER`
#: rather than spelled again. It is the arm that reads no train gold SQL, which is
#: what makes its gap the composition-difficulty baseline the excess gap subtracts.
CONTROL_ARM: str = ARM_ORDER[0]

#: How many standard errors a gap must clear before its sign is worth reading. Two
#: is a rule of thumb, not a test: the gap is unpaired and family-wise error is not
#: controlled here, so this is a floor for "look at it", not a significance claim.
_SE_MULTIPLE = 2.0


def _arms_block(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    arms = summary.get("arms")
    return arms if isinstance(arms, dict) else {}


def _gap(train: Any, test: Any) -> float | None:
    """``train - test``, or ``None`` unless both sides were measured.

    ``None`` on either side is not zero: an arm that measured nothing on one split
    has no gap, and rendering that as 0.0 reads as "transferred perfectly".

    Deliberately NOT merged with ``harness._delta``, which is the same subtraction:
    that one takes ``float | None`` from rates computed in-process and can trust its
    types, while this reads two ``summary.json`` files off disk, where anything can be
    in a key — hence the ``isinstance`` guards, and the bool guard in particular
    (``isinstance(True, int)`` is ``True`` in Python, so a flag that drifted into a
    gapped key would subtract as a 100-point gap). Widening ``_delta`` to ``Any`` to
    share nine lines would cost the type check at its own call sites.
    """
    if not isinstance(train, (int, float)) or not isinstance(test, (int, float)):
        return None
    if isinstance(train, bool) or isinstance(test, bool):
        return None
    return float(train) - float(test)


def _rate_se(rate: Any, n: Any) -> float | None:
    """Binomial standard error of ``rate`` over ``n`` rows, or ``None``.

    ``None`` whenever the inputs cannot support one — an unmeasured rate, an absent
    or empty ``n``, a value outside [0, 1] (so a mean or a count that drifted into a
    gapped key cannot be dressed up as a proportion). Same bool exclusion as
    :func:`_gap`, for the same reason.
    """
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return None
    if not isinstance(n, (int, float)) or isinstance(n, bool) or n <= 0:
        return None
    p = float(rate)
    if not 0.0 <= p <= 1.0:
        return None
    return math.sqrt(p * (1.0 - p) / float(n))


def _quadrature(*ses: float | None) -> float | None:
    """SE of a difference of independent estimates. ``None`` if any side is unknown.

    Unknown must not degrade to zero: an SE of 0.0 declares a difference certain,
    which is the opposite of what a missing denominator means.
    """
    if any(se is None for se in ses):
        return None
    return math.sqrt(sum(se * se for se in ses if se is not None))


def _fmt(value: Any) -> str:
    """A rate for stdout, or ``n/a``.

    Rejects bools like :func:`_gap` does. ``isinstance(True, int)`` is True, so
    without this a flag that drifted into a gapped key would print as ``1.000``
    beside a ``None`` gap — a rate and its gap disagreeing about whether the thing
    was even measurable.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "n/a"
    return f"{value:.3f}"


def split_gap(
    train_summary: dict[str, Any], test_summary: dict[str, Any]
) -> dict[str, Any]:
    """Per-arm train-minus-test gaps, plus what could not be compared.

    Arms are intersected rather than unioned: a gap needs both sides, and an arm
    present on one split only is reported in ``arms_not_in_both`` instead of being
    silently dropped.

    Each arm also carries the SE of its EX gap and, for every arm but the control,
    the excess over the control arm's gap — see the module docstring for why the raw
    gap alone reads high.
    """
    train_arms, test_arms = _arms_block(train_summary), _arms_block(test_summary)
    shared = sorted(set(train_arms) & set(test_arms))
    only = sorted(set(train_arms) ^ set(test_arms))

    by_arm: dict[str, dict[str, Any]] = {}
    for arm in shared:
        tr, te = train_arms[arm], test_arms[arm]
        gaps = {
            rate: _gap(tr.get(rate), te.get(rate))
            for rate in GAPPED_RATES
            if rate in tr or rate in te
        }
        block: dict[str, Any] = {
            "n_train": tr.get("n"),
            "n_test": te.get("n"),
            "train": {r: tr.get(r) for r in GAPPED_RATES if r in tr},
            "test": {r: te.get(r) for r in GAPPED_RATES if r in te},
            "gap": gaps,
        }
        # SE for ``ex_lenient`` and nothing else. It is the one gapped rate whose
        # denominator IS ``n``; every other one is conditional on something (rows
        # that produced SQL, rows the router placed, gradeable rows) and its
        # denominator is not in this block, so an SE computed from ``n`` there would
        # claim a precision the rate does not have.
        se = _quadrature(
            _rate_se(tr.get("ex_lenient"), tr.get("n")),
            _rate_se(te.get("ex_lenient"), te.get("n")),
        )
        ex_gap = gaps.get("ex_lenient")
        if se is not None:
            block["ex_gap_se"] = se
            block["ex_gap_noise_floor"] = _SE_MULTIPLE * se
            if ex_gap is not None:
                block["ex_gap_within_noise"] = abs(ex_gap) <= _SE_MULTIPLE * se
        by_arm[arm] = block

    control = by_arm.get(CONTROL_ARM)
    control_gap = control["gap"].get("ex_lenient") if control else None
    if control is not None and control_gap is not None:
        for arm, block in by_arm.items():
            gap = block["gap"].get("ex_lenient")
            if arm == CONTROL_ARM or gap is None:
                continue
            excess = gap - control_gap
            block["ex_excess_gap"] = excess
            se = _quadrature(block.get("ex_gap_se"), control.get("ex_gap_se"))
            if se is not None:
                block["ex_excess_gap_se"] = se
                block["ex_excess_gap_noise_floor"] = _SE_MULTIPLE * se
                block["ex_excess_gap_within_noise"] = abs(excess) <= _SE_MULTIPLE * se

    out: dict[str, Any] = {
        "reading": (
            "gap = train - test, per arm. Positive means the arm scored higher on the "
            "questions the curator was built from than on held-out ones, i.e. that "
            "much of its score did not transfer. The RAW gap overstates memorisation: "
            "the two splits are different questions, so part of it is composition "
            f"difficulty, which is all the {CONTROL_ARM} arm's gap can be (it reads no "
            "train gold SQL). Prefer ex_excess_gap = this arm's gap minus "
            f"{CONTROL_ARM}'s, which cancels that term. Read a sign only above "
            "ex_gap_noise_floor (2 binomial SE); below it the gap is sampling noise. "
            "Not paired and not significance tested: a within-arm diagnostic, never a "
            "headline. The train split is never quotable as performance "
            "(eval.index.quotable refuses it)."
        ),
        "arms": by_arm,
        "arms_not_in_both": only,
    }
    # Omitted rather than ``None`` when the control arm did not score on both splits:
    # every excess-gap key is then absent too, and a named control with no excess
    # anywhere reads as a computation that failed rather than one that was not asked.
    if control is not None and control_gap is not None:
        out["control_arm"] = CONTROL_ARM
    return out


def _corpus_hash(run_dir: Path) -> str | None:
    """The corpus digest a split was scored against, or ``None`` if unrecorded.

    ``None`` covers three cases that are all "cannot verify": no manifest, an
    unreadable one, and one predating ``corpus_content_hash``. None of them is
    evidence of a mismatch, so none of them may degrade the report — see
    :func:`write_split_gap`.
    """
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("corpus_content_hash")
    return value if isinstance(value, str) else None


def write_split_gap(base_dir: Path, train_dir: Path, test_dir: Path) -> dict[str, Any]:
    """Read both summaries, write ``split_gap.json`` under ``base_dir``, return it.

    Returns a ``{"error": ...}`` block rather than raising if either summary is
    missing: the two scored splits are already on disk at this point, and losing them
    to a reporting fault would be the expensive failure.

    The same degraded shape is returned when the two splits name **different**
    corpora. Same-corpora is the load-bearing invariant of the whole gap (see the
    module docstring), and until this check existed nothing here could see a
    violation — this function read only ``summary.json``, so a rebuilt corpus between
    the passes produced a confident number that mixed overfitting with curator
    variance. A silent wrong number is the failure mode worth degrading for; an
    *unverifiable* one is not, so an absent hash reports itself and the gap is still
    computed.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any]
    train_hash, test_hash = _corpus_hash(train_dir), _corpus_hash(test_dir)
    if train_hash is not None and test_hash is not None and train_hash != test_hash:
        out = {
            "error": (
                f"the two splits were scored against different corpora "
                f"(train {train_hash} != test {test_hash}). A gap across two curator "
                "draws mixes overfitting with curator variance and measures neither, "
                "so it is not computed. Re-score both splits off one build."
            )
        }
    else:
        try:
            train = json.loads((train_dir / "summary.json").read_text(encoding="utf-8"))
            test = json.loads((test_dir / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            out = {"error": f"{type(err).__name__}: {err}"}
        else:
            out = split_gap(train, test)
            out["train_dir"] = str(train_dir).replace("\\", "/")
            out["test_dir"] = str(test_dir).replace("\\", "/")
            if train_hash is not None and train_hash == test_hash:
                out["corpus_content_hash"] = train_hash
            else:
                out["corpus_hash_unverified"] = (
                    "no manifest corpus_content_hash on "
                    + " and ".join(
                        name
                        for name, value in (("train", train_hash), ("test", test_hash))
                        if value is None
                    )
                    + ": the same-corpora invariant could not be checked"
                )
    (base_dir / "split_gap.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out


def format_split_gap(report: dict[str, Any]) -> str:
    """One line per arm for stdout. A report nobody prints is a report nobody reads."""
    if "error" in report:
        return f"  split gap unavailable: {report['error']}"
    lines: list[str] = []
    for arm, block in report.get("arms", {}).items():
        ex_gap = block["gap"].get("ex_lenient")
        tr = block["train"].get("ex_lenient")
        te = block["test"].get("ex_lenient")
        # The floor and the excess ride on the same line as the gap they qualify. A
        # caveat one line away from the number is a caveat nobody copies.
        floor = block.get("ex_gap_noise_floor")
        noise = f" (floor={_fmt(floor)})" if floor is not None else ""
        excess = block.get("ex_excess_gap")
        vs_control = (
            f"  excess_vs_{CONTROL_ARM}={_fmt(excess)}" if excess is not None else ""
        )
        lines.append(
            f"  [{arm}] EX train={_fmt(tr)}(n={block['n_train']}) "
            f"test={_fmt(te)}(n={block['n_test']})  gap={_fmt(ex_gap)}{noise}"
            f"{vs_control}"
        )
    if lines:
        lines.append(
            f"  gap below its floor is noise; prefer excess_vs_{CONTROL_ARM} "
            "(raw gap includes split-composition difficulty)"
        )
    if report.get("arms_not_in_both"):
        lines.append(f"  not in both splits: {report['arms_not_in_both']}")
    if report.get("corpus_hash_unverified"):
        lines.append(f"  ! {report['corpus_hash_unverified']}")
    return "\n".join(lines) or "  no arm scored on both splits"
