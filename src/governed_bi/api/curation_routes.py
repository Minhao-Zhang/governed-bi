"""Corpus curation admin routes: drafts, conflicts, assumptions, the offline clarifications
ledger (UtkuAI, ported; ADR 0005 §6 file-length cap).

Split out of ``api/routes.py`` once that file reached 997/1000 lines (the commit that added
``POST /clarifications/{id}/answer``'s corpus fold flagged this as its own follow-up). Pure
extraction: every route below kept its exact path, request/response shape, and gating -- this
module only relocates *where the code lives*, mirroring ``browse_routes.py``'s own separate-
``APIRouter``-mounted-via-``include_router`` pattern (not a parallel ``FastAPI`` app).

HTTP shell over ``corpus/drafts.py``, ``curator/clarification.py``, and
``curator/clarifications.py``. See ``utku-ai-v2-porting-spec.md`` for why this admin-facing
write surface exists on v2 at all (v2 otherwise deletes the HTTP corpus-write surface).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter

from governed_bi.register.assets import ASSET_REGISTER

__all__ = ["router"]

#: Mounted by ``routes.app``.
router = APIRouter()


def _curation_session() -> Any:
    """This request's session (imported lazily to avoid a circular import with ``routes``,
    same reason ``browse_routes.py``'s own ``_request_session`` does the same thing -- named
    differently here so the two do not collide as duplicate top-level names under
    ``tools/check_one_implementation.py`` (ADR 0005 §6)."""
    from governed_bi.api.routes import _session

    return _session()


@router.get("/corpus/assets")
def corpus_assets(type: str | None = None) -> list[dict[str, Any]]:
    """Assets of one type as rows. ``type`` is validated against the register.

    ``provenance_status`` and ``excluded`` are required by the client's ``assetRowSchema``.
    """
    from governed_bi.api.routes import _provenance_status

    session = _curation_session()
    known = {t.value for t in ASSET_REGISTER}
    if type is not None and type not in known:
        return []
    return [
        {
            "id": a.id,
            "asset_type": a.asset_type.value,
            "summary": a.summary,
            "schema": getattr(a, "schema", None),
            "provenance_status": _provenance_status(a),
            "excluded": bool(getattr(getattr(a, "governance", None), "excluded", False)),
        }
        for a in sorted(session.assets_by_id.values(), key=lambda a: a.id)
        if type is None or a.asset_type.value == type
    ]


@router.post("/corpus/drafts/{asset_id}/approve")
def approve_draft_route(asset_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Certify one ``proposed`` draft (UtkuAI mistake-memory / Enhancer, ported onto v2).

    **Not an upstream route.** v2 deletes the HTTP corpus-write surface entirely (ADR 0005
    §1.6: "the corpus is trusted, the incoming question is not") and has no ``curator/`` layer
    yet to review a draft through. This is the minimal admin-facing half of
    ``corpus/drafts.py`` — see ``utku-ai-v2-porting-spec.md`` for why it lives here rather
    than waiting on upstream.

    Request body: ``{"by": "admin@example.com"}`` (optional — recorded in ``audit.extra``,
    never required).

    Writes to disk only. ``session.assets_by_id``/the index are run constants (ADR 0005) and
    do not observe this write until the corpus is reloaded — the same limitation a live
    ``run_query`` retrieval has for any other out-of-band corpus edit.
    """
    from fastapi import HTTPException

    from governed_bi.api.routes import _provenance_status
    from governed_bi.corpus.drafts import DraftNotFound, DraftNotPending, approve_draft as approve

    session = _curation_session()
    if session.corpus_root is None:
        raise HTTPException(status_code=409, detail="this session has no corpus_root to write back to")
    try:
        certified = approve(session.corpus_root, asset_id, by=(body or {}).get("by"))
    except DraftNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DraftNotPending as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": certified.id,
        "asset_type": certified.asset_type.value,
        "provenance_status": _provenance_status(certified),
    }


# ── /corpus/assumptions, /corpus/conflicts: v1 admin curation queues, restored ─────────────
#
# Phase 4 of restoring v1 admin corpus curation onto v2 (Phase 3: enhancer.py's dedup/conflict
# wired into live clarification mining). Read-only listing plus one resolve action over what
# Phase 3 already writes; nothing here writes a *new* candidate.

#: The id namespace ``curator/clarification.py::draft_from_clarification`` mints
#: (``clarification.<schema>.<hash>``) — Problem 1's discriminator, see
#: ``_is_clarification_derived``.
_CLARIFICATION_ID_PREFIX = "clarification."


def _is_clarification_derived(asset: Any) -> bool:
    """True only for a ``TermAsset`` minted by ``draft_from_clarification``.

    **Problem 1: distinguishing a live clarification answer from any other curator-authored
    draft.** ``curator/mistake_memory.py`` goes through the same ``submit_draft``/
    ``store.write`` machinery and is also model-authored/``proposed`` — but it always builds a
    ``FewShotAsset`` (checked: its only caller anywhere is ``scripts/mine_mistakes_v2.py``, an
    offline script with no live route), so ``asset_type == "term"`` already rules it out. What
    it does not rule out is a hand-authored or seeded ``TermAsset`` that happens to be
    ``proposed``/``certified`` through some other path.

    Chosen discriminator: the id namespace ``draft_from_clarification`` already mints
    unconditionally, on every write it produces (novel or conflict-flagged alike) —
    ``clarification.<schema>.<hash>``. That shape is unique to this one producer today, so
    reusing it needs no code change anywhere upstream and cannot drift out of sync with a
    second, parallel "is this a clarification" flag. The alternative the task considered —
    threading an explicit marker through ``enhancer.apply()``'s ``extra`` on every write path
    — would be a second source of truth for a fact the id already states once, which is
    exactly the "flexibility nobody asked for" this project's own guidelines warn against. If
    a future producer ever mints a non-clarification ``TermAsset`` under this same prefix,
    that is a new collision to solve then, not a reason to pre-build a marker nothing needs
    yet.
    """
    return asset.asset_type.value == "term" and asset.id.startswith(_CLARIFICATION_ID_PREFIX)


#: ``draft_from_clarification``'s exact body shape (``f"Q: {question}\nA: {answer}"``).
_QA_BODY_RE = re.compile(r"\AQ: (?P<question>.*?)\nA: (?P<answer>.*)\Z", re.DOTALL)


def _parse_qa(body: str | None) -> tuple[str, str] | None:
    """``(question, answer)`` out of a clarification-derived ``body``, or ``None``.

    Every asset ``_is_clarification_derived`` accepts has a body in exactly this shape (it is
    the only thing ``draft_from_clarification`` ever writes into ``body``), so this only
    returns ``None`` for an asset that is not clarification-derived at all — e.g. the
    "existing" side of a conflict row, which may be any asset type with any ``body``.
    """
    if not body:
        return None
    match = _QA_BODY_RE.match(body)
    return (match.group("question"), match.group("answer")) if match else None


def _reload_assets(session: Any) -> list[Any]:
    """Every asset under this session's corpus root, reloaded fresh from disk.

    Deliberately **not** ``session.assets_by_id``. That mapping is a run constant, frozen at
    session-build time — ``/corpus/drafts/{id}/approve``'s own docstring already documents
    this: a write it makes is invisible to ``/corpus/assets`` until the process restarts, "the
    same limitation a live ``run_query`` retrieval has for any other out-of-band corpus edit".
    That limitation is tolerable for an asset browser. It is not tolerable here: the entire
    point of these two routes is "did the clarification I just answered show up", within the
    same long-running server process and the same request-response cycle a live admin actually
    drives. So this reloads the corpus root straight off disk on every call, scoped to
    ``session.db_id`` the same way ``session.assets_by_id`` itself was originally built
    (``corpus.store.load(root, schemas=[db_id])`` — ``_shared`` is always included, see
    ``identity.corpus_files``). ``session.corpus_root is None`` (no writable corpus at all)
    returns an empty list rather than raising, matching ``/corpus/assets``'s handling of an
    unrecognised ``type``.
    """
    if session.corpus_root is None:
        return []
    from governed_bi.corpus.store import load

    assets, _problems = load(session.corpus_root, schemas=[session.db_id])
    return assets


def _conflict_status(extra: Any) -> str:
    """**Problem 2: what "resolved" means with no dedicated status field.**

    ``Audit.extra`` is the only place additional facts land (``corpus/schema.py``), so
    "resolved" is derived from two keys in it rather than stored directly: ``conflict_with``
    present + no ``conflict_resolution`` -> ``unresolved``; ``conflict_resolution ==
    "kept_existing"`` -> ``resolved_kept_existing``; ``== "replaced"`` -> ``resolved_replaced``.
    ``corpus/drafts.py::resolve_conflict`` is the only writer of ``conflict_resolution``, and
    ``approve_draft`` already preserves ``audit.extra`` across its status flip (verified: it
    rebuilds ``audit`` via ``dataclasses.replace(asset.audit, provenance=...)``, which carries
    every field it does not name forward unchanged) — so a replaced-and-certified conflict
    keeps this marker rather than becoming indistinguishable from a plain approved draft.
    """
    resolution = extra.get("conflict_resolution")
    if resolution == "kept_existing":
        return "resolved_kept_existing"
    if resolution == "replaced":
        return "resolved_replaced"
    return "unresolved"


@router.get("/corpus/assumptions")
def corpus_assumptions() -> list[dict[str, Any]]:
    """Every answered live clarification folded into the corpus, that nothing disputes.

    v1's "agreed assumptions" log, restored. A conflict-flagged clarification — whether
    resolved or not — belongs to ``/corpus/conflicts`` instead and is excluded here
    permanently: this is a read-only history of the answers nobody disagreed with, not a
    superset of every clarification-derived asset. Includes both ``proposed`` and
    ``certified`` clarification-derived terms — an admin certifying it via
    ``/corpus/drafts/{id}/approve`` is a separate, later action this log does not require
    first: the assumption was already agreed to the moment it was answered without
    contradiction.

    ``answered_by``/``answered_at`` are read from ``audit.extra`` and are ``null`` on every
    row today: nothing in the write path (``curator/clarification.py``,
    ``curator/enhancer.py``) captures caller identity or a timestamp yet, and inventing either
    here would be exactly the "field the engine does not observe" this module's own docstring
    rule forbids. ``source`` is always ``"live_chat"``: every row this route can produce came
    through an answered ``ask_user`` interrupt, mined by ``serve/nodes/mine_corpus.py`` --
    reached identically whether the resume arrived over ``POST /chat/resume`` or LangGraph
    Server's own ``/threads/{id}/runs/stream``, since both resume by invoking the same
    compiled graph.
    """
    session = _curation_session()
    rows: list[dict[str, Any]] = []
    for asset in _reload_assets(session):
        if not _is_clarification_derived(asset):
            continue
        if bool(getattr(getattr(asset, "governance", None), "excluded", False)):
            # Found live (2026-08-08): a "replace" conflict resolution excludes the asset it
            # superseded (corpus/drafts.py::resolve_conflict), but does not touch
            # audit.extra["conflict_with"] on the *other* side of the conflict it resolved --
            # so absent this check, a definition a later conflict overturned kept reporting
            # here as a currently-agreed assumption. "Agreed" means "not currently disputed
            # and not currently superseded", not just "not conflict-flagged at write time".
            continue
        extra = asset.audit.extra if asset.audit is not None else {}
        if "conflict_with" in extra:
            continue
        parsed = _parse_qa(asset.body)
        if parsed is None:
            continue
        question, answer = parsed
        rows.append(
            {
                "id": asset.id,
                "question": question,
                "answer": answer,
                "answered_by": extra.get("answered_by"),
                "answered_at": extra.get("answered_at"),
                "source": "live_chat",
            }
        )
    return sorted(rows, key=lambda r: r["id"])


@router.get("/corpus/conflicts")
def corpus_conflicts(status: str | None = None) -> list[dict[str, Any]]:
    """Clarifications whose Enhancer decision contradicted an existing certified asset.

    ``status`` (``unresolved`` / ``resolved_kept_existing`` / ``resolved_replaced``) narrows
    the list; omitted, every conflict is returned regardless of resolution.

    A row whose ``conflict_with`` names an asset not found in this reload is skipped rather
    than synthesising the required non-nullable ``existing_asset_type``/``existing_text``
    fields with nothing behind them — this should not happen (Phase 3 only ever sets
    ``conflict_with`` to an id drawn from ``session.assets_by_id`` at mining time), so a miss
    here means the referenced asset left the corpus scope some other way, not a shape this
    route should paper over.
    """
    session = _curation_session()
    assets = _reload_assets(session)
    by_id = {a.id: a for a in assets}
    rows: list[dict[str, Any]] = []
    for asset in assets:
        extra = asset.audit.extra if asset.audit is not None else {}
        conflict_with = extra.get("conflict_with")
        if not conflict_with:
            continue
        row_status = _conflict_status(extra)
        if status is not None and row_status != status:
            continue
        existing = by_id.get(conflict_with)
        if existing is None:
            continue
        new_question, _ = _parse_qa(asset.body) or (None, None)
        existing_question, _ = _parse_qa(existing.body) or (None, None)
        rows.append(
            {
                "id": asset.id,
                "status": row_status,
                "existing_asset_id": existing.id,
                "existing_asset_type": existing.asset_type.value,
                "existing_text": existing.summary,
                "existing_question": existing_question,
                "new_question": new_question,
                "new_text": asset.summary,
                "answered_by": extra.get("answered_by"),
                "created_at": extra.get("created_at"),
                "source": "live_chat",
            }
        )
    return sorted(rows, key=lambda r: r["id"])


@router.post("/corpus/conflicts/{asset_id}/resolve")
def resolve_conflict_route(asset_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve one flagged conflict. **Not gated on ``can_edit``** — mirrors
    ``/corpus/drafts/{id}/approve``'s existing pattern exactly (that route checks only
    ``session.corpus_root is None``; ``can_edit`` gates the unrelated free-form corpus editor
    surface, and this route has nothing to do with it).

    Request body: ``{"resolution": "keep_existing" | "replace", "answered_by"?: "..."}``.
    ``resolution`` is validated before anything else: an unrecognised value is a 422
    regardless of whether ``asset_id`` also happens to be wrong.

    404 when ``asset_id`` names no asset, or one with no ``conflict_with`` flag. 409 when it
    was already resolved — matching v1: a second resolve call is an error, not a silent
    no-op.
    """
    from fastapi import HTTPException

    from governed_bi.corpus.drafts import (
        ConflictAlreadyResolved,
        ConflictNotFound,
        resolve_conflict as resolve,
    )

    session = _curation_session()
    if session.corpus_root is None:
        raise HTTPException(status_code=409, detail="this session has no corpus_root to write back to")
    resolution = str((body or {}).get("resolution") or "")
    if resolution not in ("keep_existing", "replace"):
        raise HTTPException(
            status_code=422,
            detail=f"resolution must be 'keep_existing' or 'replace', got {resolution!r}",
        )
    by = (body or {}).get("answered_by")
    try:
        candidate, _existing = resolve(session.corpus_root, asset_id, resolution, by=by)
    except ConflictNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictAlreadyResolved as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    status = "resolved_kept_existing" if resolution == "keep_existing" else "resolved_replaced"
    return {
        "resolved": True,
        "conflict_id": candidate.id,
        "status": status,
        "detail": f"resolved {candidate.id} ({resolution})",
    }


# ── /clarifications: v1's offline Clarifications queue, restored (Phase 1a) ────────────────
#
# Phase 1a of restoring v1's offline Clarifications queue + Setup Wizard onto v2. Pure CRUD
# over `curator/clarifications.py`'s ledger -- nothing here writes into the ledger from a live
# `ask_user` turn (Phase 1b) or folds an answer into the corpus (Phase 1c).


def _clarification_row(record: Any) -> dict[str, Any]:
    """One ``ClarificationRecord`` as a response row.

    ``answer_text`` is ``resolve_answer_text``'s output, distinct from the record's own
    ``answer`` field -- a choice-only answer leaves ``answer`` null, and a caller rendering
    the ledger needs something to show for it. The underlying record is unchanged.
    """
    from governed_bi.curator.clarifications import resolve_answer_text

    return {
        "id": record.id,
        "scope": record.scope,
        "question": record.question,
        "status": record.status.value,
        "raised_by": list(record.raised_by),
        "choices": [dict(c) for c in record.choices] if record.choices is not None else None,
        "allow_freeform": record.allow_freeform,
        "answer": record.answer,
        "answer_choice_id": record.answer_choice_id,
        "answer_choice_ids": (
            list(record.answer_choice_ids) if record.answer_choice_ids is not None else None
        ),
        "answered_by": record.answered_by,
        "converted_to_corpus": record.converted_to_corpus,
        "source": record.source,
        "basis": record.basis,
        "category": record.category,
        "ui_modality": record.ui_modality,
        "target_table": record.target_table,
        "target_column": record.target_column,
        "answer_text": resolve_answer_text(record),
    }


@router.get("/clarifications")
def clarifications(status: str | None = None) -> list[dict[str, Any]]:
    """The offline clarifications ledger (UtkuAI, ported). ``status`` filters by exact value
    (e.g. ``"open"``); omitted returns every source/status.

    ``session.corpus_root is None`` returns an empty list rather than raising, matching
    ``/corpus/assets``'s and ``/corpus/assumptions``'s handling of "nothing to read here."
    """
    from governed_bi.curator.clarifications import load_clarifications

    session = _curation_session()
    if session.corpus_root is None:
        return []
    records = load_clarifications(session.corpus_root)
    if status is not None:
        records = [r for r in records if r.status.value == status]
    return [_clarification_row(r) for r in records]


@router.post("/clarifications/{clarification_id}/answer")
def answer_clarification_route(clarification_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record one admin answer to a ledger record. **Not gated on ``can_edit``** — mirrors
    ``/corpus/drafts/{id}/approve``'s existing pattern exactly (only requires
    ``session.corpus_root is not None``; ``can_edit`` gates the unrelated free-form corpus
    editor surface).

    Request body: ``{"choice_id"?, "choice_ids"?, "answer"?, "answered_by"?: "admin"}`` — at
    least one of ``choice_id``/``choice_ids``/``answer`` is required, else 422. 404 on an
    unknown id.

    **Folds into the corpus (Phase 1c)** via ``curator/clarification.py::
    fold_ledger_answer_into_corpus`` -- the offline entry point into
    ``fold_answered_clarification``, the Enhancer logic factored out of
    ``serve/nodes/mine_corpus.py`` so a live resume and this route reach identical behavior
    (basis gate + ``converted_to_corpus`` idempotency both live on that helper; see its own
    docstring). ``known_assets`` is a fresh ``_reload_assets`` disk read, not the frozen
    ``session.assets_by_id`` -- same reason ``/corpus/conflicts`` reloads rather than trusts it.
    """
    from fastapi import HTTPException

    from governed_bi.curator.clarification import fold_ledger_answer_into_corpus
    from governed_bi.curator.clarifications import ClarificationNotFound, answer_clarification

    session = _curation_session()
    if session.corpus_root is None:
        raise HTTPException(status_code=409, detail="this session has no corpus_root to write back to")

    body = body or {}
    choice_id = body.get("choice_id")
    choice_ids = body.get("choice_ids")
    answer = body.get("answer")
    if choice_id is None and choice_ids is None and answer is None:
        raise HTTPException(
            status_code=422, detail="one of choice_id, choice_ids, or answer is required"
        )
    try:
        record = answer_clarification(
            session.corpus_root,
            clarification_id,
            choice_id=choice_id,
            choice_ids=choice_ids,
            answer=answer,
            answered_by=str(body.get("answered_by") or "admin"),
        )
    except ClarificationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record = fold_ledger_answer_into_corpus(
        record,
        agent_model=session.agent_model,
        corpus_root=session.corpus_root,
        schema=session.db_id,
        known_assets=_reload_assets(session),
        write_model=session.knobs_resolved.get("llm_model"),
    )
    return _clarification_row(record)

