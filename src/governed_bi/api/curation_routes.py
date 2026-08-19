"""Corpus curation admin routes: drafts, conflicts, assumptions, the offline clarifications
ledger (DetentAI, ported; ADR 0005 §6 file-length cap).

Split out of ``api/routes.py`` once that file reached 997/1000 lines (the commit that added
``POST /clarifications/{id}/answer``'s corpus fold flagged this as its own follow-up). Pure
extraction: every route below kept its exact path, request/response shape, and gating -- this
module only relocates *where the code lives*, mirroring ``browse_routes.py``'s own separate-
``APIRouter``-mounted-via-``include_router`` pattern (not a parallel ``FastAPI`` app).

HTTP shell over ``corpus/drafts.py``, ``curator/clarification.py``, and
``curator/clarifications.py``. See ``detent-ai-v2-porting-spec.md`` for why this admin-facing
write surface exists on v2 at all (v2 otherwise deletes the HTTP corpus-write surface).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import APIRouter

__all__ = ["make_curation_router"]


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
        "turn_id": record.turn_id,
        "category": record.category,
        "ui_modality": record.ui_modality,
        "target_table": record.target_table,
        "target_column": record.target_column,
        "severity": record.severity,
        "audience": record.audience,
        "blocked_by": list(record.blocked_by),
        "unmet_prerequisites_at_answer": (
            list(record.unmet_prerequisites_at_answer)
            if record.unmet_prerequisites_at_answer is not None
            else None
        ),
        "answer_text": resolve_answer_text(record),
    }


def make_curation_router(session: Any) -> APIRouter:
    """The corpus-curation routes over one ``session``.

    A factory, not a module-level ``router``, for the reason ``browse_routes.make_router``
    gives: these handlers used to reach a process-wide session by importing
    :mod:`governed_bi.api.routes` at call time to get ``_session()``, which was both a
    global and an import cycle dodged by deferring it. ``routes.py`` removed that global at
    the 2026-08-11 restructure, so there is nothing left to import; taking the session is
    the honest interface, and it makes two apps in one test session independent.

    **These routes are not narrowed by the access grant, and the browse routes are.**
    ``browse_routes`` reads every session through :func:`~governed_bi.api.visibility.visible`
    (ADR 0012 §8.5); this router reads ``session.assets_by_id`` raw, so a deployment that
    set ``GOVERNED_BI_ACCESS_POLICY`` to deny a column would still see it here. That is a
    real gap and it is left visible rather than papered over: the curation surface is the
    admin's, its whole job is to show assets a business user must not see, and deciding
    whether an admin grant is the same grant is a governance question this fork has not
    settled. Recorded so the next reader finds the question, not a silent asymmetry.
    """
    router = APIRouter()


    @router.post("/corpus/drafts/{asset_id}/approve")
    def approve_draft_route(asset_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Certify one ``proposed`` draft (DetentAI mistake-memory / Enhancer, ported onto v2).

        **Not an upstream route.** v2 deletes the HTTP corpus-write surface entirely (ADR 0005
        §1.6: "the corpus is trusted, the incoming question is not") and has no ``curator/`` layer
        yet to review a draft through. This is the minimal admin-facing half of
        ``corpus/drafts.py`` — see ``detent-ai-v2-porting-spec.md`` for why it lives here rather
        than waiting on upstream.

        Request body: ``{"by": "admin@example.com"}`` (optional — recorded in ``audit.extra``,
        never required).

        Writes to disk only. ``session.assets_by_id``/the index are run constants (ADR 0005) and
        do not observe this write until the corpus is reloaded — the same limitation a live
        ``run_query`` retrieval has for any other out-of-band corpus edit.
        """
        from fastapi import HTTPException

        from governed_bi.api.routes import _provenance_status
        from governed_bi.corpus.drafts import DraftNotFound, DraftNotPending
        from governed_bi.corpus.drafts import approve_draft as approve

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
        rule forbids. ``source`` is no longer hardcoded (fixed 2026-08-16, task C-0): it is read
        off ``audit.extra["source"]``, stamped at fold time by ``curator/clarification.py::
        fold_answered_clarification``'s ``source`` keyword -- ``"live_chat"`` from a live turn,
        or ``record.source`` verbatim from the offline ledger fold, which can be ``"refusal"``
        since task A gave a reader a second, non-``ask_user`` entrance into this same queue. A
        row with no stamp falls back to ``"live_chat"``: not a guess, because that population is
        closed -- every unstamped row predates task A, when ``"live_chat"`` was this route's only
        possible producer, so the fallback recovers a known fact rather than inventing one.
        """
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
                    "source": extra.get("source", "live_chat"),
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
        )
        from governed_bi.corpus.drafts import (
            resolve_conflict as resolve,
        )

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


    @router.get("/clarifications")
    def clarifications(status: str | None = None) -> list[dict[str, Any]]:
        """The offline clarifications ledger (DetentAI, ported). ``status`` filters by exact value
        (e.g. ``"open"``); omitted returns every source/status.

        ``session.corpus_root is None`` returns an empty list rather than raising, matching
        ``/corpus/assets``'s and ``/corpus/assumptions``'s handling of "nothing to read here."
        """
        from governed_bi.curator.clarifications import load_clarifications

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

        **Setup Wizard composition (Phase 2)**, answering a category-tagged (``elicitation_wizard``)
        candidate: the record's own ``scope`` decides how ``choice_id``/``choice_ids``/``answer`` are
        reduced to text (``curator/elicitation_answers.py::compose_elicitation_answer_text``) rather
        than the generic picked-label/freeform concatenation ``resolve_answer_text`` falls back to for
        every other record -- computed here, against the record as it stood *before* this call, and handed
        to ``answer_clarification`` as the ``answer`` it writes so every downstream reader
        (this row, the ledger view, and the fold below, via ``resolve_answer_text``'s own
        ``category is not None`` bypass) sees the same composed sentence.

        **D join-path auto-follow-up (Phase 2)**: right after an A-category answer names a real
        picked column (``choice_id`` set), ``curator/elicitation.py::maybe_generate_join_followup``
        checks whether it lands on a different table than the question expected and, if so, mints a
        new open D-category record -- appended to the ledger (idempotent by scope) for a later
        ``GET /clarifications`` or ``GET /elicitation/candidates`` to pick up.
        """
        from fastapi import HTTPException

        from governed_bi.curator.clarification import fold_ledger_answer_into_corpus
        from governed_bi.curator.clarifications import (
            ClarificationNotFound,
            answer_clarification,
            append_if_new_scope,
            load_clarifications,
            restate_question,
        )
        from governed_bi.curator.elicitation import maybe_generate_join_followup
        from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text
        from governed_bi.curator.elicitation_terms import restate_with_business_definition

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

        existing = next(
            (r for r in load_clarifications(session.corpus_root) if r.id == clarification_id), None
        )
        if existing is not None and existing.category is not None:
            answer = compose_elicitation_answer_text(
                existing, choice_id=choice_id, choice_ids=choice_ids, freeform=answer
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

        if record.category == "A" and choice_id is not None:
            followup = maybe_generate_join_followup(record, choice_id)
            if followup is not None:
                append_if_new_scope(session.corpus_root, followup)

        # A-biz just landed a business definition, so the A-eng question waiting on it stops asking
        # in the abstract and starts quoting what it is meant to map. The engineering half already
        # exists (it is written at scan time, which is what lets a DBA with no business counterpart
        # answer it standalone) -- what arrives now is the quote, so the question is restated rather
        # than minted, and its id, and every ``blocked_by`` edge naming it, are untouched.
        # ``body["answer"]``, not ``record.answer``: this route has already replaced the latter with
        # the composed corpus sentence, and quoting *that* nests one frame inside the other (found
        # live on real ``app_store`` -- "Business defines 'price' as \"In business terms, 'price'
        # means …\"").
        restatement = restate_with_business_definition(
            record, load_clarifications(session.corpus_root), freeform=str(body.get("answer") or "")
        )
        if restatement is not None:
            restate_question(session.corpus_root, *restatement)

        record = fold_ledger_answer_into_corpus(
            record,
            agent_model=session.agent_model,
            corpus_root=session.corpus_root,
            schema=session.db_id,
            known_assets=_reload_assets(session),
            write_model=session.knobs_resolved.get("llm_model"),
        )
        return _clarification_row(record)


    @router.post("/clarifications/from-refusal")
    def clarification_from_refusal_route(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """A reader who was refused submits what they meant (detent-ai-trust-loop-plan.md, task A).

        **The one reader-initiated entrance to this ledger.** Every other write route here is an
        admin acting on a record that already exists; this route is a record's *origin*. It
        exists because ``no_schema_matched`` fires at ``Stage.route``, before ``agent_core`` --
        and therefore before ``ask_user``, an agent tool -- ever runs, so the one moment the
        engine names a semantic-layer gap most precisely is also the one moment it is structurally
        unable to ask about it. The reader who asked the original question is the one filing it
        instead. ``basis`` is hardcoded ``"data_definition"`` rather than accepted from the
        caller: this route is scoped to exactly the shape of gap ``no_schema_matched`` names
        (nothing in the corpus defines a term the reader used), not to ambiguity in general.

        **Decided here: the explanation becomes the record's ``answer`` immediately, rather than
        a freeform pre-fill left on an ``open`` row for an admin to separately confirm.** Both
        reach the identical certification gate either way -- ``corpus/drafts.py::approve_draft``
        is the only function that flips ``proposed`` to ``certified``, and nothing here calls it
        -- so the choice is about the *ledger's* shape, not about who may certify this fact.
        Landing ``open`` with a pre-filled ``answer`` would be a state nothing else on this
        ledger uses (``ClarificationAnswerForm``'s freeform input never reads a starting value),
        so making it visible to an admin would mean changing the shared clarification-queue
        components three other surfaces already render through. Folding immediately through
        :func:`~governed_bi.curator.clarification.fold_ledger_answer_into_corpus` -- the exact
        function every other answer route here already calls, with no branch added for this
        source -- needs none of that: the record is ``answered_by="user"`` the moment it exists,
        the same vocabulary :func:`~governed_bi.curator.clarifications.close_live_clarification`
        already uses for a live turn's *own* asking user, and it surfaces where an admin actually
        reviews unreviewed facts (the drafts queue) rather than swelling the "still owed an
        answer" queue with a row nobody owes an answer to.

        **What "reaches the identical certification gate" does and does not mean, stated plainly
        so the next reader does not have to re-derive it by tracing the session builder.**
        Certification is gated: ``for_analyst``'s certified-only filter (``corpus/analyst.py``)
        still stands between a ``proposed`` term and licensing a *column* in ``check()``, so a
        reader's own words cannot make a column queryable on their say-so alone. **Retrieval
        visibility of a ``proposed`` asset is not gated, and this is true today for every source
        that reaches this fold, not only this one.** ``serve/session.py`` builds
        ``assets_by_id`` -- and the retrieval index built from it -- by filtering only
        ``governance.excluded``; it does not read ``audit.provenance`` at all. So the term this
        route writes is retrievable, and is rendered into the model's context
        (``serve/nodes/assemble.py`` -> ``serve/context.py::render_context``, both reading
        ``assets_by_id`` directly, never ``for_analyst``'s certified view) on the very next turn
        served by a session built over this corpus root -- before any admin has looked at it, let
        alone approved it. This is pre-existing and identical for a ``curator``/``live_chat``/
        ``elicitation_wizard``-sourced draft folded through the same function; this route adds no
        new exposure and, per this initiative's additive-only constraint, does not add a
        provenance filter to close it either. Whether ``_visible`` should also filter on
        provenance is a question about the shared fold path, not about this route, and is
        recorded rather than decided here.

        Request body: ``{"question": "...", "answer": "...", "turn_id"?: "..."}`` -- ``question``/
        ``answer`` are both required, else 422; ``turn_id`` is optional. ``answer`` matches every
        other clarification route's own wire vocabulary for "the text a person provided", not
        because an admin is answering anything here.

        **``turn_id`` (detent-ai-trust-loop-plan.md, task B-0).** The turn whose refusal this
        explanation answers, forwarded onto :attr:`~governed_bi.curator.clarifications.
        ClarificationRecord.turn_id` unchanged. It is not part of this route's own idempotency key
        (the ``question``/``answer`` digest below, unaffected) -- so a second submission of the
        identical text from a *different* turn is still absorbed as the same record, and its
        ``turn_id`` stays whichever turn raised it first. Nothing here reads it back; it exists so
        task B's read model can later answer "what did this thread raise", the same way
        ``curator/feedback.py::FeedbackRecord.turn_id`` already lets it answer that for a report.
        Omitted, it is simply ``None`` -- the client sends it when it has one
        (``AnswerView.record.turn_id``, on every answer card), so this is additive for a caller
        that predates it, not required for the route to keep working.

        **Idempotent by content, not by turn.** No graph interrupt is involved -- the turn already
        ended at ``Stage.route`` -- so there is no replay to guard against the way
        ``serve/tools.py::_log_live_clarification`` guards a live question's id; only an
        accidental double-submit of the identical text, which
        :func:`~governed_bi.curator.clarifications.append_if_new_scope`'s own scope idempotency
        already exists to absorb. Two different explanations for the same question are two
        different records, deliberately -- a second reader's own words are not a duplicate of the
        first reader's.

        Not gated on ``can_curate_corpus`` or ``can_edit`` -- same reasoning as every sibling
        route in this file: the real gate is ``session.corpus_root is not None`` (409), and a
        capability is a client-side rendering signal, not a server-side permission check.
        """
        from fastapi import HTTPException

        from governed_bi.curator.clarification import fold_ledger_answer_into_corpus
        from governed_bi.curator.clarifications import (
            ClarificationRecord,
            ClarificationRecordStatus,
            append_if_new_scope,
            load_clarifications,
        )

        if session.corpus_root is None:
            raise HTTPException(status_code=409, detail="this session has no corpus_root to write back to")

        body = body or {}
        question = str(body.get("question") or "").strip()
        answer = str(body.get("answer") or "").strip()
        if not question or not answer:
            raise HTTPException(status_code=422, detail="both question and answer are required")
        turn_id = str(body.get("turn_id") or "").strip() or None

        digest = hashlib.sha256(f"{question}\x1f{answer}".encode()).hexdigest()[:16]
        scope = f"refusal:{digest}"
        record = ClarificationRecord(
            id=f"refusal-{digest}",
            scope=scope,
            question=question,
            status=ClarificationRecordStatus.answered,
            answer=answer,
            answered_by="user",
            source="refusal",
            basis="data_definition",
            turn_id=turn_id,
        )
        appended = append_if_new_scope(session.corpus_root, record)
        stored = appended or next(
            r for r in load_clarifications(session.corpus_root) if r.scope == scope
        )
        folded = fold_ledger_answer_into_corpus(
            stored,
            agent_model=session.agent_model,
            corpus_root=session.corpus_root,
            schema=session.db_id,
            known_assets=_reload_assets(session),
            write_model=session.knobs_resolved.get("llm_model"),
        )
        return _clarification_row(folded)

    @router.post("/clarifications/{clarification_id}/cancel")
    def cancel_clarification_route(clarification_id: str) -> dict[str, Any]:
        """The user abandoned a question rather than answering it or handing it to an admin.

        **Not a kind of resume.** ``ask_user``'s ``interrupt()`` payload and the resume shape
        (``answer | choice_id | declined | defer``) are untouched, which is deliberate: those two
        are upstream's wire contract, and a fork-local escape hatch that widened them would
        conflict at every merge. Cancelling is a ledger write and nothing else — the paused graph
        thread is simply never resumed, and the LRU evicts it.

        What it costs the admin depends on the record's own ``basis``, decided in one place
        (``curator/clarifications.py::cancel_clarification``): a ``ranking_ambiguity`` question
        lands ``cancelled`` and leaves their queue, anything else stays ``open``. The response
        carries the resulting row so the client can report which happened without a second fetch.

        No body. 404 on an unknown id, 409 on a record that is already answered — its answer may
        be folded into the corpus under an id hashed from this question text, and un-asking it
        would strand that asset behind a ledger no longer claiming the question was put.
        """
        from fastapi import HTTPException

        from governed_bi.curator.clarifications import (
            ClarificationNotFound,
            cancel_clarification,
        )

        if session.corpus_root is None:
            raise HTTPException(
                status_code=409,
                detail="this session has no corpus_root, so there is no ledger to cancel on",
            )

        try:
            record = cancel_clarification(session.corpus_root, clarification_id)
        except ClarificationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        assert record is not None  # ClarificationNotFound is the only no-record path
        return _clarification_row(record)

    return router
