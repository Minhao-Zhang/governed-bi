"""Pending clarifications and reader-raised notes.

``GET /clarifications/pending`` stays read-only: answering from here would resume a
thread the operator was not the one asked (ADR 0006 B9). ``POST /turns/{id}/raised``
is a different write — it appends a note onto checkpointed ``ServeState.raised``
through the unattached ``raise_note`` node, and does not resume anything. A paused
thread or an in-flight run is 409: ``as_node="raise_note"`` would clear ``next``.

**What it discloses and what it now accepts, stated because A7 is open.** Nothing on this
surface authenticates — reaching the port is sufficient (``docs/enterprise-fork.md``) — so
the GET hands any caller every unanswered question, and those questions can name assets:
``ask_user`` is one of the four tools that do. That was accepted knowingly, because
``/audit/turns`` already discloses every thread's SQL to the same caller, so it widens
nothing new. **It does not narrow it either.** The POST is the newer half of the same bill,
and it is a write: any caller reaching the port can file a note against any turn, so the
pending queue an operator reads is attacker-writable, and the only bound on how much of a
never-swept store one caller can grow is ``RAISED_NOTE_MAX_CHARS``. What it deliberately
cannot do is *act* — it takes a bounded note and a turn id, resumes nothing, and reaches
neither ``command.update`` nor ``POST /threads/{id}/state``. The consequence to carry
forward is that under a real ``AccessPolicy`` (ADR 0012) both verbs must apply the same
withholding the tools do, or the GET is a read path around a grant and the POST a write
path around one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from governed_bi.api.raised_write import ThreadBusy, ThreadNotFound
from governed_bi.serve.raised import RAISED_KINDS, RAISED_NOTE_MAX_CHARS, raised_row


def make_clarification_router(pending: Any, turn_log: Any) -> APIRouter:
    """Pending-queue plus raised-note routes over the two readers.

    ``pending`` exposes ``pending(limit=, offset=)`` and ``PENDING_FIELDS``.
    ``turn_log`` exposes ``get_turn`` and ``append_raised``.
    """
    router = APIRouter()

    @router.get("/clarifications/pending")
    def pending_clarifications(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Open questions, oldest first.

        Interrupt clarifications union open ``raised`` rows. ``meta.truncated`` is
        load-bearing (ADR 0009). Answering from here is still refused (B9).
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

    @router.post("/turns/{turn_id}/raised")
    def raise_on_turn(turn_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """File a reader note on a finished turn. Does not go through ``command.update``.

        The body is validated before the turn is read, so a rejected note costs one
        comparison and never reaches the writer. The note is stripped first — the cap is
        on what would be persisted, not on the caller's trailing newlines — and the 422
        names the limit, because "too long" without a number is not actionable.
        """
        kind = str((body or {}).get("kind") or "")
        if kind not in RAISED_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"kind must be one of {sorted(RAISED_KINDS)}",
            )
        note = str((body or {}).get("note") or "").strip()
        if len(note) > RAISED_NOTE_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"note must be at most {RAISED_NOTE_MAX_CHARS} characters, "
                    f"not {len(note)}"
                ),
            )
        entry = turn_log.get_turn(str(turn_id))
        if entry is None:
            raise HTTPException(status_code=404, detail="turn not found")
        record = entry.get("record") or {}
        thread_id = str(record.get("thread_id") or "")
        if not thread_id:
            raise HTTPException(status_code=404, detail="turn has no thread_id")
        row = raised_row(
            kind=kind,
            turn_id=str(turn_id),
            thread_id=thread_id,
            note=note,
        )
        try:
            turn_log.append_raised(thread_id, row)
        except ThreadBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ThreadNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "row": row}

    return router
