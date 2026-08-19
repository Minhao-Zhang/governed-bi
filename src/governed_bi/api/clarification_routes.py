"""``GET /clarifications/pending`` — the questions the engine asked and nobody answered.

**Its own module rather than a block in ``routes.py``.** That file is 646 lines against a soft cap
of 400 (ADR 0005 §6), and the fork this surface is adopted from let its equivalent grow to 984
before splitting it into three — their conclusion, worth taking rather than rediscovering, is that
another router file is the expected move and not a smell.

**Read-only, and that is a decision rather than a first step.** Answering from here would mean
resuming a thread the operator was not the one asked, which ``serve/resume.py::authorise_resume``
refuses by design (ADR 0006 B9). The owner's 2026-08-19 decision routes an operator's answer into
the semantic layer instead — a write path gated on a provenance check this repository does not have
yet, since ``serve/session.py``'s ``_visible`` filters ``governance.excluded`` alone and a
``proposed`` asset therefore already reaches the model's context. So this router has no POST.

**What it discloses, stated because A7 is open.** Nothing here authenticates: reaching the port is
sufficient (``docs/enterprise-fork.md``), so this hands any caller every unanswered question. Those
questions can name assets — ``ask_user`` is one of the four tools that do. That was accepted
knowingly: ``/audit/turns`` already discloses every thread's SQL to the same caller, so this widens
nothing new. **It does not narrow it either**, and the consequence to carry forward is that under a
real ``AccessPolicy`` (ADR 0012) this route must apply the same withholding as the tools do, or it
becomes a read path around a grant.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query


def make_clarification_router(pending: Any) -> APIRouter:
    """The pending-queue route over one reader.

    A factory taking its dependency, for the reason ``browse_routes.make_router`` records: the
    alternative is a process-wide global reached through a backwards import. ``pending`` is
    anything exposing ``pending(limit=, offset=)`` and ``PENDING_FIELDS``;
    :class:`governed_bi.api.thread_turns.PendingClarifications` is the production one.

    **Named for its own surface, not ``make_router``.** ``tools/check_one_implementation.py``
    refuses a second ``make_router`` under ``src/`` -- one name, one concept -- and the honest
    resolution is a distinct name rather than a ``KNOWN_DUPLICATES`` waiver, because these two
    factories mount different surfaces over different dependencies. The fork this surface came
    from reached the same answer the same way: it carries five router modules and gives each a
    ``make_*_router`` of its own, with a half-written waiver comment left behind as evidence that
    the exemption was tried first. So the convention for a sixth router module is
    ``make_<surface>_router``, and ``browse_routes.make_router`` is the one that predates it.
    """
    router = APIRouter()

    @router.get("/clarifications/pending")
    def pending_clarifications(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Open questions, oldest first.

        ``meta.truncated`` is the load-bearing field. ADR 0009 D2/D9 exist because a silently
        short list reads as "this is everything", and the thing being under-reported here is a
        person waiting for an answer — so the count the caller did not get is on the wire beside
        the ones it did.

        ``meta.threads_scanned`` is reported for the same reason one level down: it distinguishes
        "no open questions" from "the store was not read", which otherwise look identical.
        """
        page = pending.pending(limit=limit, offset=offset)
        return {
            "rows": list(page.rows),
            "meta": {
                "n": len(page.rows),
                "truncated": bool(page.truncated),
                "threads_scanned": int(page.threads_scanned),
                "limit": limit,
                "offset": offset,
                "columns": list(pending.PENDING_FIELDS),
            },
        }

    return router
