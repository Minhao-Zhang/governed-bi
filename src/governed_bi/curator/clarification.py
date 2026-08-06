"""Turn an answered live clarification into a corpus candidate (UtkuAI, ported).

**What v2 already has, and what it does not.** ``serve/tools.py``'s ``ask_user`` +
``serve/resume.py``'s identity-bound resume + ``POST /chat/resume`` are the full
pause/resume mechanics, built and tested on this branch already — see
``utku-ai-v2-porting-spec.md``. What has no home yet is the other half: turning an answered
question into a corpus fact, so the next question that hits the same ambiguity does not have
to ask again. This module is exactly that missing half, and nothing else — it does not touch
how a turn is served, paused, or declined.

**Decline/defer behavior is deliberately untouched.** v2 fails closed on a decline (the turn
refuses rather than guessing); UtkuAI v1 fell back to a heuristic-tagged guess. That is a
serve-behavior product decision the v2 authors already made on purpose, not a gap this port is
scoped to fill — :func:`resolved_answer_text` returns ``None`` on a decline so a caller mines
nothing, and the turn's own refusal is untouched.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from governed_bi.corpus.schema import TermAsset
from governed_bi.register.knobs import knob_default

__all__ = ["resolved_answer_text", "draft_from_clarification"]


def resolved_answer_text(body: Mapping[str, Any]) -> str | None:
    """The client's structured resume payload (``{answer}`` / ``{choice_id}`` / ``{declined}``)
    reduced to answer text, or ``None`` on a decline -- distinct from
    ``serve/tools.py::_clarification_answer``, which turns the same payload into what the
    *model* sees mid-turn (a sentence, even on decline, since the agent needs to know a
    disambiguation was refused). This is "is there anything to mine", not "what does the model
    read", and the two must not collapse into one string a caller then has to pattern-match.
    """
    if body.get("declined"):
        return None
    for key in ("answer", "choice_id", "text"):
        value = body.get(key)
        if value:
            return str(value)
    return None


def _truncated(text: str) -> str:
    cap = int(knob_default("summary_max_chars"))
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def draft_from_clarification(question: str, answer: str, *, schema: str) -> TermAsset:
    """One clarification Q&A as a :class:`TermAsset` draft.

    ``TermAsset`` over the other seven types because it is the one asset whose contract
    ("a phrase, and what it refers to") does not presuppose a formula, a join, or a bound
    column -- a live clarification answer can be any of those, and guessing which without a
    model call would misfile more often than a generic term captures correctly. The admin
    reviewing the drafts queue (corpus/drafts.py::approve_draft) is exactly where a
    misclassified draft gets corrected before it ever serves.
    """
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    return TermAsset(
        id=f"clarification.{schema}.{digest}",
        name=_truncated(question),
        summary=_truncated(f"{question} — {answer}"),
        body=f"Q: {question}\nA: {answer}",
    )
