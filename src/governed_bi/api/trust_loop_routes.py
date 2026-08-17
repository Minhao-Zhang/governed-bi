"""``GET /threads/{thread_id}/raised``: task B-1's read model (utku-ai-trust-loop-plan.md).

**The question this answers, and only this question.** "Given a thread, what did it raise, and
what became of it?" -- read-only, over ledgers that already exist (``feedback.jsonl``,
``clarifications.jsonl``) and the corpus assets those ledgers fold into. No new write path, and
no new engine field: everything this route reports was already durable before task B touched
anything.

**Why "thread" and not "user".** This engine has no identity concept (``api/routes.py::
_identity`` falls back to the thread id when the caller supplies none, and the UI supplies
none) -- see the plan's own note on this. So "the reader who raised something" is, operationally,
the thread that raised it, and this route is keyed on ``thread_id`` rather than any notion of an
account.

**Why a new file, not ``feedback_routes.py``.** This route reads *both* ledgers -- the report
ledger (task H) and the refusal-clarification slice of the clarification ledger (task A) -- plus
the turn log, plus the corpus. Folding it into ``feedback_routes.py`` would misname the concern
the same way reusing ``draft_from_clarification`` for a report's own draft would have misnamed
provenance (see that module's own docstring): H-b's argument that a report is a different record
type from a clarification, so it gets a different module, applies just as much to a route that is
about *neither* record type specifically but about what a thread did across both. Not added to
``curation_routes.py`` either -- that file is 968/1000 lines against ADR 0005 §6's hard cap, with
no margin left for a route this size.

**How a raised item is traced back to a thread.** Neither ledger stores ``thread_id`` directly.
Both store ``turn_id`` -- ``curator/feedback.py::FeedbackRecord.turn_id`` (required, always
present) and ``curator/clarifications.py::ClarificationRecord.turn_id`` (task B-0, optional,
present only on a refusal-clarification filed after B-0 shipped) -- and the turn log
(``api/trace_store.py``) is the one place that already maps a ``turn_id`` to the ``thread_id``
it was served on. So this route reads both ledgers, looks each candidate row's ``turn_id`` up in
the turn log, and keeps only the rows whose turn belongs to the requested thread. A
refusal-clarification with no ``turn_id`` (it predates B-0) is silently excluded -- not a
different failure mode than "raised on a different thread", because this route has no way to
tell the two apart, and reporting a guess would be exactly the "field the engine does not
observe" defect this project's own docstrings (``/corpus/assumptions``, most recently) keep
naming and refusing to commit.

**"Became of it" means "is a certified asset now", nothing softer.** ``certified``, never
``proposed`` -- the plan's own words: a ``proposed`` draft is not yet a rule an admin stands
behind, and telling a reader their report changed something when it has not been approved is the
kind of claim that costs trust rather than building it. Computed by re-deriving the exact asset
id the fold path would have written (see :func:`_expected_asset_id` below) and reading its
*current* ``audit.provenance.status`` fresh off disk (:func:`~governed_bi.api.curation_routes.
_reload_assets`, the same reload every other admin-facing route in this family already uses, for
the same reason: an approval that happened moments ago in this same process must be visible here
without a restart).

**Silence on "dismissed" and "still open", by choice, not by omission.** This route's own
response *does* carry every raised item's ``status`` (open/answered/dismissed for a report;
always ``answered`` for a refusal-clarification, since ``POST /clarifications/from-refusal``
never leaves one open) -- the read model tells the whole truth. What the reader-facing surface
built on top of this (``ui/components/chat/raised-history.tsx``, task B-2) chooses to *render* is
narrower: only the ``certified`` case. A dismissed report carries no reason field explaining why
(``curator/feedback.py::dismiss_report`` takes none), so surfacing a bare "an admin dismissed
this" would read as an unexplained rejection -- worse than the silence H-3's own "an admin will
see this" already left the reader with. And "still open" adds nothing beyond that same
same-turn acknowledgment. Both are real, inspectable states this route reports; neither is a
state B-2 turns into reader-facing copy.

**No fabricated date.** The plan's own minimum phrasing is "an admin defined it on <date>", and
this route does not produce a ``<date>`` for that half of the sentence: `corpus/drafts.py::
approve_draft`` stamps no timestamp anywhere (``Provenance.built_at`` is declared and never
populated -- confirmed by reading, not assumed), so there is no *observed* certification date to
report. ``raised_at`` on a response row is ``FeedbackRecord.reported_at`` (when the reader filed
it) -- the one honest timestamp either ledger carries -- and is ``None`` for a clarification,
which has no timestamp field at all. Inventing either would be the exact defect
``/corpus/assumptions``'s own docstring already refuses for ``answered_at``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter

from governed_bi.api.curation_routes import _reload_assets

__all__ = ["make_raised_router"]


def _expected_asset_id(prefix: str, question: str, schema: str | None) -> str:
    """The asset id the fold path would have written for ``question``, under ``prefix``.

    **Must match two other formulas exactly, and does not import either.**
    ``curator/clarification.py::draft_from_clarification`` mints
    ``f"clarification.{schema}.{digest}"`` and ``curator/feedback.py::_report_draft`` mints
    ``f"feedback.{schema}.{digest}"``, both with ``digest = sha256(question)[:16]`` -- the answer
    text plays no part in either id, only the question. Recomputed here rather than imported
    because the feedback-side function is private and not exported (unlike
    ``draft_from_clarification``, which is), and duplicating one two-line hash expression is
    cheaper and more surgical than adding a new public export to a file task H already shipped
    and reviewed, for a caller (this route) that did not exist when it was written. **The
    fragility this trades for that:** if either digest formula ever changes, this silently stops
    matching and every row this route reports reads ``certified: false`` forever, with no error
    -- worth a reader's attention if either of those two functions is touched again.
    """
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}.{schema}.{digest}"


def make_raised_router(session: Any, turn_log: Any) -> APIRouter:
    """The one route this file declares, over one ``session`` and the ``turn_log`` that maps a
    turn to the thread it was served on.

    **Takes ``turn_log`` too, unlike every sibling ``make_..._router`` in this package.** Every
    other curation-family router needs only the session; this is the first to also need the turn
    log ``api/routes.py::_build_app`` already threads through as its own third dependency
    (``turn_log.get_turn``/``.list_turns``, the same seam ``/audit/turns`` reads). Importing
    ``governed_bi.api.trace_store`` directly here would have worked too -- its module-level
    ``TURN_LOG_DIR`` is read fresh on every call, so a test's ``monkeypatch.setattr(trace_store,
    "TURN_LOG_DIR", ...)`` would still take effect even without this parameter -- but it would
    quietly drop the swappable-``turn_log`` seam ``make_app``'s own docstring describes ("anything
    exposing ``append_turn``, ``list_turns``, ``get_turn``..."). Taking it as a parameter, the way
    every other reader of the turn log in this codebase already does, keeps that seam real rather
    than theoretical for exactly one more caller.
    """
    router = APIRouter()

    @router.get("/threads/{thread_id}/raised")
    def raised_by_thread(thread_id: str) -> list[dict[str, Any]]:
        """Every report or refusal-clarification traceable to ``thread_id``, and whether each one
        is now a certified asset. See the module docstring for the full argument; this is the
        shape of one row:

        ``{"kind": "feedback" | "clarification", "id", "question", "status", "raised_at",
        "certified"}``.

        ``session.corpus_root is None`` returns an empty list, matching every sibling read route
        in this project's handling of "nothing to read here" (``/clarifications``,
        ``/feedback``, ``/corpus/assumptions``).
        """
        from governed_bi.api.routes import _provenance_status
        from governed_bi.curator.clarifications import load_clarifications
        from governed_bi.curator.feedback import load_feedback

        if session.corpus_root is None:
            return []

        assets_by_id = {a.id: a for a in _reload_assets(session)}

        def _certified(asset_id: str) -> bool:
            asset = assets_by_id.get(asset_id)
            return asset is not None and _provenance_status(asset) == "certified"

        rows: list[dict[str, Any]] = []

        for record in load_feedback(session.corpus_root):
            turn = turn_log.get_turn(record.turn_id)
            if turn is None or (turn.get("record") or {}).get("thread_id") != thread_id:
                continue
            rows.append(
                {
                    "kind": "feedback",
                    "id": record.id,
                    "question": record.question,
                    "status": record.status.value,
                    "raised_at": record.reported_at,
                    "certified": _certified(
                        _expected_asset_id("feedback", record.question, session.db_id)
                    ),
                }
            )

        for record in load_clarifications(session.corpus_root):
            # Only a refusal-clarification: the one kind of clarification row this reader raised
            # themselves (task A). Every other source (curator/live_chat/elicitation_wizard) was
            # raised by an admin or by the agent, never by the reader, so it is not "what this
            # thread raised" no matter whose turn it happens to reference.
            if record.source != "refusal" or not record.turn_id:
                continue
            turn = turn_log.get_turn(record.turn_id)
            if turn is None or (turn.get("record") or {}).get("thread_id") != thread_id:
                continue
            rows.append(
                {
                    "kind": "clarification",
                    "id": record.id,
                    "question": record.question,
                    "status": record.status.value,
                    "raised_at": None,
                    "certified": _certified(
                        _expected_asset_id("clarification", record.question, session.db_id)
                    ),
                }
            )

        return sorted(rows, key=lambda r: (r["raised_at"] or "", r["id"]))

    return router
