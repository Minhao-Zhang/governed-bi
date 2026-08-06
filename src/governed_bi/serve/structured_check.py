"""Deterministic post-execution result checks, distinct from ADR 0006's governance layers.

**Not a governance layer.** ``govern/check.py``'s stack (PARSE..COST) decides whether a
statement may run at all, before it runs. This module runs *after* a statement has already
passed every layer and executed — it looks at whether the answer it produced actually matches
what the question asked for, which ``govern/`` has no vocabulary for.

Ported from UtkuAI's v1 line (``analyst/middleware.py::_structured_percentage_check``,
Experiment 007 Round H) rather than redesigned, because the check itself — a real,
previously-diagnosed failure mode, not a hypothesis — transfers unchanged: v2 deleted the data
model underneath it, not the finding.
"""

from __future__ import annotations

import re

__all__ = ["percentage_scale_suffix"]

# Matches "X * 100" and "100 * X" (the first version of this check, tested only as a
# throwaway eval script, matched only the former and over-triggered on already-correct
# queries written the other way round).
_PERCENT_QUESTION_RE = re.compile(r"\bpercent(age)?\b", re.IGNORECASE)
_HAS_PERCENT_SCALING_RE = re.compile(r"(\*|/)\s*100(\.0)?\b|\b100(\.0)?\s*(\*|/)", re.IGNORECASE)


def percentage_scale_suffix(question: str | None, sql: str | None) -> str:
    """Flag a 'percentage' question whose SQL never scales by 100.

    Deterministic, not open-ended: fires only when the question text contains
    "percent"/"percentage" AND the executed SQL has no ``*100``/``/100``-shaped factor
    anywhere in it. Returns ``""`` (no-op) otherwise — callers append the result to the
    tool reply text unconditionally.
    """
    if not question or not _PERCENT_QUESTION_RE.search(question):
        return ""
    if _HAS_PERCENT_SCALING_RE.search(sql or ""):
        return ""
    return (
        "\n\n[structured check] this question asks for a PERCENTAGE (0-100 scale), "
        "but your query's final result does not appear to be scaled by 100 (no `* 100` "
        "or `/ 100`-style factor found). If your query computes a 0-1 ratio, multiply "
        "the final value by 100."
    )
