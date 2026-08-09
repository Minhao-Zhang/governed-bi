"""Pin one run's routing decisions onto the next, so a single-knob A/B measures the knob.

``route_node`` itself is deterministic — it ranks facet hits and takes ``route_top_n``. What
is not deterministic is the five **facet rewriters** upstream of it: each is a utility-model
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
    "licensed_drift",
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
