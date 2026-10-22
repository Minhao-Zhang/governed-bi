"""A hint after a statement has executed. Not a governance layer: it cannot refuse.

Fires when the question says percent and the SQL has no ``* 100`` or ``/ 100`` factor,
either way round. A hint on an already-correct query is worse than none.
"""

from __future__ import annotations

import re

__all__ = ["percentage_scale_suffix"]

_PERCENT_QUESTION_RE = re.compile(r"\bpercent(age)?\b", re.IGNORECASE)

#: Both orders. A suffix-only match contradicted queries that were already scaled.
_HAS_PERCENT_SCALING_RE = re.compile(r"(\*|/)\s*100(\.0)?\b|\b100(\.0)?\s*(\*|/)", re.IGNORECASE)


def percentage_scale_suffix(question: str | None, sql: str | None) -> str:
    """``""`` when no statement ran, so callers can append this on every path."""
    if not question or not _PERCENT_QUESTION_RE.search(question):
        return ""
    if not sql or _HAS_PERCENT_SCALING_RE.search(sql):
        return ""
    return (
        "\n\n[structured check] this question asks for a PERCENTAGE (0-100 scale), "
        "but your query's final result does not appear to be scaled by 100 (no `* 100` "
        "or `/ 100`-style factor found). If your query computes a 0-1 ratio, multiply "
        "the final value by 100."
    )
