"""Reader-reported wrong answers (utku-ai-trust-loop-plan.md, task H): a second admin inbox,
beside the offline clarifications ledger, over its own ``feedback.jsonl``.

**Why this is a new file and not a fourth section of ``curation_routes.py``.** That file is
already 968/1000 lines against ADR 0005 §6's hard cap (``tools/check_file_length.py``) --
``drafts_routes.py``'s own docstring records the same measurement one commit earlier, at 965. But
the file-length cap is not the only reason: H-b's decision that a report is a *different record
type* than a clarification -- different actor (the reader objecting, not the engine asking),
different lifecycle (a report cannot be deferred), different meaning -- extends naturally to
where its routes live. Splitting it out is the better factoring even with room to spare, the same
point the trust-loop plan's own execution log makes about this exact file: "H's feedback routes
get their own ``api/feedback_routes.py``, which is also the better factoring."

Mirrors ``browse_routes.py``'s / ``drafts_routes.py``'s ``make_..._router(session)`` factory
shape (``curation_routes.py:163-171`` gives the reason: no process-wide session to close over,
so the session is taken, not imported). Reaches into ``curation_routes.py`` for
:func:`~governed_bi.api.curation_routes._reload_assets` rather than duplicating it -- the same
precedent ``drafts_routes.py`` already set for the identical reason (that helper is called from
several places in an already-large file; moving it buys nothing ``tools/check_imports.py``
needs, since that tool layers by package and both files are in ``api``).

**Not gated on ``can_curate_corpus`` or ``can_edit``.** Same reasoning as every route in
``curation_routes.py``: the real precondition is ``session.corpus_root is not None`` (409
otherwise), and a capability flag is a client-side rendering signal, never a server-side
permission check. ``can_edit`` is hardcoded ``False`` (``api/routes.py``'s
``capabilities_for``) -- gating any of these three routes on it would build a fourth control
that can never render, the exact defect this fork already paid for three times.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from governed_bi.api.curation_routes import _reload_assets

__all__ = ["make_feedback_router"]


def _feedback_row(record: Any) -> dict[str, Any]:
    """One ``FeedbackRecord`` as a response row -- every declared field, no derived ones (unlike
    ``_clarification_row``'s ``answer_text``, there is no second reduction to compute here: the
    reader's ``answer_text`` and the admin's ``correction`` are already two distinct fields)."""
    return {
        "id": record.id,
        "turn_id": record.turn_id,
        "question": record.question,
        "answer_text": record.answer_text,
        "status": record.status.value,
        "reason": record.reason,
        "reported_at": record.reported_at,
        "correction": record.correction,
        "answered_by": record.answered_by,
        "converted_to_corpus": record.converted_to_corpus,
    }


def make_feedback_router(session: Any) -> APIRouter:
    """The report-ledger routes over one ``session``. A factory, not a module-level ``router``,
    for the reason every sibling router in ``api/`` already gives (``curation_routes.py:163-171``,
    ``drafts_routes.py``'s own docstring)."""
    router = APIRouter()

    @router.post("/feedback")
    def file_feedback_route(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """A reader (H-a: available at ``business`` tier) says one turn's answer is wrong
        (``components/answer/wrong-answer-report.tsx``, H-3).

        Request body: ``{"turn_id": "...", "question": "...", "answer_text": "...", "reason"?:
        "..."}`` -- ``turn_id``/``question``/``answer_text`` are all required, else 422; ``reason``
        is the reader's optional one-line explanation. 409 when this session has no
        ``corpus_root`` to write the ledger under.

        **Idempotent by content** (``curator/feedback.py::file_report``'s own docstring): filing
        the identical report twice (a network retry, not a second complaint) returns the existing
        row rather than doubling the admin's queue.
        """
        from fastapi import HTTPException

        from governed_bi.curator.feedback import file_report

        if session.corpus_root is None:
            raise HTTPException(
                status_code=409, detail="this session has no corpus_root to write back to"
            )

        body = body or {}
        turn_id = str(body.get("turn_id") or "").strip()
        question = str(body.get("question") or "").strip()
        answer_text = str(body.get("answer_text") or "").strip()
        reason = body.get("reason")
        reason = str(reason).strip() or None if reason is not None else None
        if not turn_id or not question or not answer_text:
            raise HTTPException(
                status_code=422, detail="turn_id, question and answer_text are all required"
            )

        record = file_report(
            session.corpus_root,
            turn_id=turn_id,
            question=question,
            answer_text=answer_text,
            reason=reason,
        )
        return _feedback_row(record)

    @router.get("/feedback")
    def list_feedback(status: str | None = None) -> list[dict[str, Any]]:
        """The admin's report queue (H-4). ``status`` filters by exact value (e.g. ``"open"``);
        omitted returns every report. ``session.corpus_root is None`` returns an empty list,
        matching ``GET /clarifications``'s own handling of "nothing to read here."
        """
        from governed_bi.curator.feedback import load_feedback

        if session.corpus_root is None:
            return []
        records = load_feedback(session.corpus_root)
        if status is not None:
            records = [r for r in records if r.status.value == status]
        return [_feedback_row(r) for r in records]

    @router.post("/feedback/{feedback_id}/answer")
    def answer_feedback_route(feedback_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """The admin corrects a reported wrong answer (H-4). Request body:
        ``{"correction": "...", "answered_by"?: "admin"}`` -- ``correction`` is required (422
        without it, matching every other admin-answer route's validate-before-write shape); 404 on
        an unknown id.

        **Folds through the existing Enhancer path** (``curator/feedback.py::
        fold_report_into_corpus``) into a ``proposed`` corpus draft -- the same
        dedup/conflict-then-write pipeline ``POST /clarifications/{id}/answer`` already uses, over
        this ledger instead of that one. ``known_assets`` is a fresh ``_reload_assets`` disk read,
        same reason that route reloads rather than trusts ``session.assets_by_id``: an admin
        answering two reports back-to-back in one request cycle must see the first draft when the
        second is deduplicated against existing facts.
        """
        from fastapi import HTTPException

        from governed_bi.curator.feedback import (
            FeedbackNotFound,
            answer_report,
            fold_report_into_corpus,
        )

        if session.corpus_root is None:
            raise HTTPException(
                status_code=409, detail="this session has no corpus_root to write back to"
            )

        body = body or {}
        correction = str(body.get("correction") or "").strip()
        if not correction:
            raise HTTPException(status_code=422, detail="correction is required")

        try:
            record = answer_report(
                session.corpus_root,
                feedback_id,
                correction=correction,
                answered_by=str(body.get("answered_by") or "admin"),
            )
        except FeedbackNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        record = fold_report_into_corpus(
            record,
            agent_model=session.agent_model,
            corpus_root=session.corpus_root,
            schema=session.db_id,
            known_assets=_reload_assets(session),
            write_model=session.knobs_resolved.get("llm_model"),
        )
        return _feedback_row(record)

    @router.post("/feedback/{feedback_id}/dismiss")
    def dismiss_feedback_route(feedback_id: str) -> dict[str, Any]:
        """The admin decided this report needs no corpus change (H-4's dismiss decision -- see
        ``curator/feedback.py``'s module docstring for why this is ``dismissed`` and not a form of
        ``answer``).

        **No body**, mirroring ``POST /clarifications/{id}/cancel``: dismissing carries no
        information beyond "this one". 404 on an unknown id, 409 on an already-answered
        record -- its correction may already be folded into the corpus under an id hashed from
        this report's own text, and dismissing it now would strand that fact behind a ledger no
        longer claiming the report was ever answered.
        """
        from fastapi import HTTPException

        from governed_bi.curator.feedback import FeedbackNotFound, dismiss_report

        if session.corpus_root is None:
            raise HTTPException(
                status_code=409, detail="this session has no corpus_root, so there is no ledger to dismiss on"
            )

        try:
            record = dismiss_report(session.corpus_root, feedback_id)
        except FeedbackNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _feedback_row(record)

    return router
