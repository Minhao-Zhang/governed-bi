"""Deterministic post-execution result check. **Not a governance layer.**

``govern/check.py``'s stack (PARSE..COST) decides whether a statement may run at all, before
it runs. This module runs *after* a statement has already passed every layer and executed --
it looks at whether the answer it produced actually matches what the question asked for, which
``govern/`` has no vocabulary for. Nothing here can refuse, so nothing here belongs in that
stack: the only thing it does is append a hint to the text the model reads next.

Hand-ported from the downstream fork ``utkuai/detentai-fork``
(``src/governed_bi/serve/structured_check.py``). The fork attributes the finding to its own v1
line -- ``analyst/middleware.py::_structured_percentage_check``, Experiment 007 Round H --
which is a **previously diagnosed failure mode rather than a hypothesis**: a question asking
for a percentage, answered with a 0-1 ratio. Neither that module nor that experiment exists in
this tree, so the citation is kept as *the fork's evidence*, not restated as ours. It is kept
rather than dropped because a rule whose reason has been deleted is a rule the next reader
deletes.
"""

from __future__ import annotations

import re

__all__ = ["percentage_scale_suffix"]

_PERCENT_QUESTION_RE = re.compile(r"\bpercent(age)?\b", re.IGNORECASE)

#: Matches ``X * 100`` **and** ``100 * X``. The fork records its own regression here: the first
#: version of this check (a throwaway eval script) matched only the suffix form, so it fired on
#: already-correct queries written the other way round -- a hint that contradicts a correct
#: query is worse than no hint, because the model's cheapest response is to "fix" it.
_HAS_PERCENT_SCALING_RE = re.compile(r"(\*|/)\s*100(\.0)?\b|\b100(\.0)?\s*(\*|/)", re.IGNORECASE)


def percentage_scale_suffix(question: str | None, sql: str | None) -> str:
    """Flag a 'percentage' question whose executed SQL never scales by 100. ``""`` when it does not.

    Deterministic and narrow, not open-ended: fires only when the question text contains
    "percent"/"percentage" **and** ``sql`` carries no ``*100`` / ``/100``-shaped factor
    anywhere in it. Callers append the result to the tool reply unconditionally, so the
    no-op has to be the empty string rather than ``None``.

    **A falsy ``sql`` returns ``""``, where the fork returns the hint** ("no SQL to scan for a
    scaling factor is not evidence of one"). Deliberate divergence, decided by what the one
    caller can pass: ``serve/tools.py`` hands over ``attempt_field(attempt, "executed_sql")``,
    which is ``None`` exactly when **no statement ran** -- a governance refusal, or a checker
    that broke before a verdict existed. The reply the model is holding in that case is a
    verdict, and appending engine advice to a verdict mixes two surfaces this repository keeps
    apart (``_fetch`` decides a tool's status by comparing the payload against
    ``OUT_OF_SCOPE_MESSAGE`` by identity; the audit surface shows the reply verbatim). There is
    also nothing to check: a statement that never ran produced no answer to be mis-scaled.
    Deciding it here rather than at the call site keeps the whole rule in the tested unit
    instead of splitting it across a caller's ``if``.
    """
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
