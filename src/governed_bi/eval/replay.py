"""Pin one run's routing decisions onto the next, so a single-knob A/B measures the knob.

``route_node`` itself is deterministic — it ranks facet hits and takes ``route_top_n``. What
is not deterministic is the four **facet rewriters** upstream of it (``FACET_EXTRACTS``;
``facet_schema`` does not rewrite): each is a utility-model
call, and two runs of the same question over the same corpus can hand ``route`` two different
sets of hits. The schema shortlist then differs, ``licensed`` differs, and the agent is asked
a different question. A prompt A/B run that way cannot separate "the new prompt helped" from
"routing happened to land better this time".

This module replays the ``schemas`` field of a prior artifact. The facets still run — they
also drive pass-two retrieval **within** the chosen schemas, and switching them off would
measure a system nobody serves. Only the shortlist is frozen.

**It therefore does not freeze everything, and does not pretend to.** Pass two still re-searches
inside the pinned schemas, so ``licensed`` can still move. :func:`licensed_drift` measures how
much it moved and the driver reports it, because an unquantified "mostly deterministic" is the
claim this module exists to stop anyone from making.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "PINNED_SCHEMAS_KEY",
    "routing_from_artifact",
    "attach_pinned_routing",
    "licensed_baseline",
    "licensed_drift",
    "drift_against",
    "pin_realised",
]

#: The question-dict / state key carrying a pinned shortlist. One spelling, imported by
#: ``route_node``, ``harness`` and the driver — three string literals would drift and the
#: failure would be silent (an unread key is an unpinned run that still says ``--replay-routing``).
PINNED_SCHEMAS_KEY = "pinned_schemas"


def routing_from_artifact(path: str | Path) -> dict[str, list[str]]:
    """``{question_id -> schemas}`` from a prior run's JSONL.

    Rows whose ``schemas`` is absent or empty are **skipped rather than pinned to ``[]``**: an
    empty shortlist is the ``no_schema_matched`` decline, and replaying it would freeze a
    retrieval failure into the treatment arm as though it were a decision. Eight rows of the
    2026-08-09 full run licensed nothing at all; pinning those would carry that defect forward
    and make the next arm look like it reproduced a bug it never ran.
    """
    pinned: dict[str, list[str]] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            qid = row.get("question_id")
            schemas = row.get("schemas")
            if not qid or not isinstance(schemas, list) or not schemas:
                continue
            pinned[str(qid)] = [str(s) for s in schemas]
    return pinned


def attach_pinned_routing(
    questions: Sequence[Any], pinned: Mapping[str, Sequence[str]]
) -> dict[str, int]:
    """Write the pinned shortlist onto each question. Returns ``{pinned, unpinned}`` counts.

    Mutates in place, as :func:`~governed_bi.eval.datalake.attach_quality_flags` does, so the
    harness needs no second argument threaded through every call site.

    A question absent from the artifact is left alone and **counted**. Silently routing it
    live would mean an arm described as pinned had an unmeasured fraction that was not — the
    driver prints the count so the label on the run is true.
    """
    counts = {"pinned": 0, "unpinned": 0}
    for question in questions:
        qid = str(question.get("question_id"))
        shortlist = pinned.get(qid)
        if shortlist:
            question[PINNED_SCHEMAS_KEY] = [str(s) for s in shortlist]
            counts["pinned"] += 1
        else:
            counts["unpinned"] += 1
    return counts


def licensed_baseline(path: str | Path) -> dict[str, list[str]]:
    """``{question_id -> licensed}`` for the rows :func:`routing_from_artifact` would pin.

    The baseline :func:`licensed_drift` measures against must cover **the same rows the pin
    covered** and no others. The driver built it from every row of the replayed artifact,
    including the ones ``routing_from_artifact`` deliberately skips for having an empty
    shortlist — turns that declined with ``no_schema_matched``. Those were never pinned, so
    whatever the next arm does with them is drift the pin never claimed to prevent, and
    counting it deflated the residual.

    Measured on the v4 arm against ``proxy_v3_fold``: the six excluded rows move the mean
    Jaccard over the movers from 0.7020 to 0.7049 and the identical rate from 0.0940 to 0.0937.
    Small — and in the direction that flatters the pin, which is the direction a measurement
    must not be wrong in.

    One pass over the file, so the two functions cannot be given different files by accident.
    """
    baseline: dict[str, list[str]] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            qid = row.get("question_id")
            schemas = row.get("schemas")
            if not qid or not isinstance(schemas, list) or not schemas:
                continue
            baseline[str(qid)] = [str(t) for t in (row.get("licensed") or ())]
    return baseline


def licensed_drift(
    rows: Iterable[Mapping[str, Any]], baseline: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """How far ``licensed`` moved despite the shortlist being pinned.

    ``baseline`` is ``{question_id -> licensed}`` from the artifact being replayed. Pinning the
    schemas does not pin pass two, so this is the residual: the fraction of turns whose licensed
    table set is not identical to the one the agent saw last time, and the mean Jaccard over
    those that moved.

    Reported, not asserted. A non-zero drift is expected and fine; an *unreported* one turns
    "we pinned routing" into a claim wider than what was done.
    """
    same = moved = missing = 0
    jaccards: list[float] = []
    for row in rows:
        prior = baseline.get(str(row.get("question_id")))
        if prior is None:
            missing += 1
            continue
        before = {str(x) for x in prior}
        after = {str(x) for x in (row.get("licensed") or ())}
        if before == after:
            same += 1
            continue
        moved += 1
        union = before | after
        jaccards.append(len(before & after) / len(union) if union else 1.0)
    compared = same + moved
    return {
        "compared": compared,
        "identical": same,
        "moved": moved,
        "not_in_baseline": missing,
        "identical_rate": (same / compared) if compared else None,
        # Over the moved rows only: averaging in the identical ones reports a number near 1.0
        # that hides how far the movers went.
        "mean_jaccard_when_moved": (sum(jaccards) / len(jaccards)) if jaccards else None,
    }


def drift_against(baseline_path: str | Path, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """:func:`licensed_drift` of ``rows`` against the artifact at ``baseline_path``.

    **One function, so a contrast cannot difference two different statistics.** The published
    sentence "mean residual Jaccard is 0.7049 on v4 and 0.7029 on v5 against 0.579 for the
    unpinned run1/run2 pair" did exactly that. The two pinned figures are
    ``mean_jaccard_when_moved``; ``0.579`` is the mean over *every* compared row including the
    33 identical ones — the quantity :func:`licensed_drift` deliberately does not compute,
    because averaging in rows that scored 1.0 by definition reports a number near 1.0 and hides
    how far the movers went. The like-for-like value for run1/run2 is **0.5719** through this
    function, and 0.5689 if the baseline is widened to every row rather than the rows a pin
    would have covered. The error flattered the unpinned baseline, so the conclusion survives
    and the printed comparison did not.

    The pinned side of the contrast (the driver) and the unpinned reference now come out of the
    same two calls, which is the only structural defence against the next one.
    """
    return licensed_drift(rows, licensed_baseline(baseline_path))


def pin_realised(
    rows: Iterable[Mapping[str, Any]], pinned: Mapping[str, Sequence[str]]
) -> dict[str, int]:
    """How many turns actually ran on the pinned shortlist, readable on an old artifact.

    ``routing_pinned`` has meant two things. Under the corrected semantics it is an *outcome* —
    the turn's shortlist **is** the pinned one — and a turn that ended before ``route_node``
    carries ``False``. Every artifact in ``runs/eval/`` predates that and was written under the
    old *intent* semantics, where the flag recorded that the driver had attached a shortlist,
    whether or not the turn ever used it. So ``sum(r["routing_pinned"] is True)`` returns 1 345
    on v4, v5 and v4-reflect alike — the count of questions the pin *offered*, not the count it
    reached.

    The corrected figures were published (1 342 / 1 340 / 1 333) with no producer: they were
    arithmetic somebody did once, not output any run had emitted. This is the producer, and it
    reads both semantics:

    * ``flagged`` — what the shipped one-liner returned;
    * ``realised`` — flagged **and** the turn recorded a non-empty shortlist, which is the
      corrected reading applied to an old-semantics row;
    * ``exact`` — an independent check that does not read the flag at all: the turn's
      ``schemas`` equals the pin source's, in order;
    * ``same_set_out_of_order`` — turns whose shortlist holds the pinned schemas in a different
      order. It is 0 on all three arms, which is what makes ``exact`` and ``realised`` agreeing
      a real corroboration rather than two spellings of one comparison.

    ``flagged - realised`` is the gap: clarifications that ended before routing.
    """
    flagged = realised = exact = same_set = 0
    for row in rows:
        shortlist = [str(s) for s in (row.get("schemas") or ())]
        if row.get("routing_pinned") is True:
            flagged += 1
            if shortlist:
                realised += 1
        want = pinned.get(str(row.get("question_id")))
        if want is None or not shortlist:
            continue
        if shortlist == [str(s) for s in want]:
            exact += 1
        elif set(shortlist) == {str(s) for s in want}:
            same_set += 1
    return {
        "flagged": flagged,
        "realised": realised,
        "exact": exact,
        "same_set_out_of_order": same_set,
    }
