"""Pin a prior run's schema shortlist so one setting is what the comparison measures.

Channel rewriters are model calls, so two runs can route differently and the
agent is asked a different question. Channels still run inside the pin: pass
two can move ``retrieved_tables``. That residual is measured, not assumed away.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from governed_bi.registry.stored_names import artifact_get

__all__ = [
    "PINNED_SCHEMAS_KEY",
    "routing_from_artifact",
    "attach_pinned_routing",
    "retrieved_tables_baseline",
    "retrieved_tables_drift",
    "drift_against",
    "pin_realised",
]

#: Question and state key for a pinned shortlist. One spelling: three literals would drift, and an unread key still
#: looks pinned.
PINNED_SCHEMAS_KEY = "pinned_schemas"


def routing_from_artifact(path: str | Path) -> dict[str, list[str]]:
    """Empty ``schemas`` are skipped, not pinned to ``[]``. An empty shortlist would freeze a retrieval failure."""
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
    """Write the pinned shortlist onto each question. Count pinned and unpinned.

    A question missing from the artifact stays unpinned and is counted. Routing
    it live without a count would label a partly live run as pinned.
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


def retrieved_tables_baseline(path: str | Path) -> dict[str, list[str]]:
    """``retrieved_tables`` for the rows a pin would cover, and no others.

    Turns with an empty shortlist were never pinned. Counting their movement
    as drift charges the pin for a change it did not claim to prevent.
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
            baseline[str(qid)] = [str(t) for t in (artifact_get(row, "retrieved_tables") or ())]
    return baseline


def retrieved_tables_drift(
    rows: Iterable[Mapping[str, Any]], baseline: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """How far ``retrieved_tables`` moved with the shortlist pinned.

    Pass two is not pinned. Identical rows are left out of the mean Jaccard,
    because averaging them in hides how far the movers went.
    """
    same = moved = missing = 0
    jaccards: list[float] = []
    for row in rows:
        prior = baseline.get(str(row.get("question_id")))
        if prior is None:
            missing += 1
            continue
        before = {str(x) for x in prior}
        after = {str(x) for x in (artifact_get(row, "retrieved_tables") or ())}
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
        "mean_jaccard_when_moved": (sum(jaccards) / len(jaccards)) if jaccards else None,
    }


def drift_against(baseline_path: str | Path, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Drift of ``rows`` against one artifact, through one statistic.

    The mean is over movers only. Averaging in identical rows reports a number
    near 1 and cannot be compared with this one.
    """
    return retrieved_tables_drift(rows, retrieved_tables_baseline(baseline_path))


def pin_realised(
    rows: Iterable[Mapping[str, Any]], pinned: Mapping[str, Sequence[str]]
) -> dict[str, int]:
    """``routing_pinned`` means a shortlist was attached. ``realised`` also needs a non-empty shortlist. ``exact``
    compares ``schemas`` to the pin and ignores the flag."""
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
