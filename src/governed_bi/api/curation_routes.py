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
        rule forbids. ``source`` is always ``"live_chat"``: every row this route can produce came
        through an answered ``ask_user`` interrupt, mined by ``serve/nodes/mine_corpus.py`` --
        reached identically whether the resume arrived over ``POST /chat/resume`` or LangGraph
        Server's own ``/threads/{id}/runs/stream``, since both resume by invoking the same
        compiled graph.
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
        """The offline clarifications ledger (UtkuAI, ported). ``status`` filters by exact value
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

    @router.get("/settings/toggles")
    def list_toggles() -> list[dict[str, Any]]:
        """Every knob an operator may flip, its effective value, and **where that value came from**.

        The `source` field is the load-bearing one. Without it a client cannot tell an operator
        that a switch is pinned by an exported variable, and would render a control that silently
        does nothing — which is the class of defect this pair of routes exists to end. This fork
        shipped three such controls: `/settings/allow-user-clarification` (no route, on either
        branch), `can_edit` (hardcoded `False`, so what it gated could never appear), and
        `ui_display_mode` (declared in the client's schema, never populated).

        Deliberately **not** named after `allow_user_clarification`: that is a v1 name with no knob
        behind it — absent from `KNOB_REGISTER`, and the `[serve]` block in
        `governed_bi.local.toml` that sets it is read by nothing.
        """
        from governed_bi.serve.runtime_overrides import describe

        return describe()

    @router.post("/settings/toggles/{name}")
    def set_toggle(name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Set one knob, or clear it back to its default with ``{"value": null}``.

        **Not gated on a capability, and that is a decision rather than an oversight.** The obvious
        candidate is `can_edit`, which is hardcoded `False` — gating on it is precisely how this
        fork acquired a control that could never render; `/clarifications/{id}/answer` already
        records the same choice. The real boundary is the one `api/auth.py` describes: this engine
        binds to loopback and reaching the port is sufficient. The Settings UI shows these switches
        to the engineer tier only, which is an affordance, not a security boundary.

        404 on a name outside the allowlist — including `git_sha`, which is `operational` and would
        be writable under a role-based rule, and `enable_structured_percentage_check`, which is
        `comparability` and belongs in `arms.toml` rather than behind a switch. 422 on the wrong
        type, never a coercion: `bool("false")` is `True`, so a coerced string would switch a
        feature on and record it as off. 409 when the environment owns the value, because
        accepting a write the engine will not use is the same lie in a new place.
        """
        from fastapi import HTTPException

        from governed_bi.register.knobs import env_override
        from governed_bi.serve.runtime_overrides import (
            TOGGLEABLE,
            clear_override,
            describe,
            set_override,
        )

        if name not in TOGGLEABLE:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"knob {name!r} is not runtime-toggleable. Toggleable: {sorted(TOGGLEABLE)}. "
                    "Being `operational` is not enough — that role also carries the fields a "
                    "measurement's provenance is made of."
                ),
            )
        if env_override(name) is not None:
            from governed_bi.register.knobs import env_overrides

            var = env_overrides().get(name)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{var} pins {name!r} for this process, and an exported variable is how an "
                    "eval arm pins a run. Unset it to make this switch effective."
                ),
            )

        value = (body or {}).get("value")
        try:
            if value is None:
                clear_override(name)
            else:
                set_override(name, value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return next(row for row in describe() if row["name"] == name)

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


    @router.post("/elicitation/generate")
    def elicitation_generate(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run both candidate generators against this session's current tables and append any newly
        proposed candidates to the offline clarifications ledger.

        **Two generators, additive, not one replacing the other.** ``curator/gaps.py``'s structural
        detectors are here because the keyword generator returns an **empty list** on the German
        ``beer_factory`` corpus this backend actually serves — every one of its gates is an English
        substring match. But the keyword path finds real traps on English schemas (``app_store``'s
        ``price`` A-question is a genuine ambiguity between two real columns), so it keeps running:
        the two read different signals and neither subsumes the other.

        Order is forced, not chosen. ``detect_structural_gaps`` runs first because its near-duplicate
        output is what ``apply_cluster_dependencies`` gates the keyword candidates *with*: certifying
        a value mapping on a decoy column makes the wrong column authoritative and nobody shown a
        value checklist can tell (``utku-ai-setup-wizard-gap-model.md`` § "Presentation
        consequences"). ``blocked_by`` is stamped on the new records before they are written, so the
        dependency is persisted on the record rather than recomputed per read — which is what lets
        ``GET /elicitation/candidates`` derive ``blocked`` from the ledger alone.

        Gated the same way the ledger's own write route is (``session.corpus_root is not None``,
        no ``can_edit`` -- this is a read/propose action over the semantic layer, not the unrelated
        free-form corpus editor surface ``can_edit`` gates).

        Idempotent: a candidate whose ``scope`` already exists among prior
        ``source="elicitation_wizard"`` ledger records (open or answered) is never proposed twice.
        That filter now runs once, in ``curator/scan_report.diff_scan_against_ledger``, over both
        generators' output -- it used to run twice, inside the keyword generator for its own half and
        here for the structural half, which is two implementations of one rule over one ledger.

        **``report`` is the account a re-run owes, and it is why the filter moved.** The owner's third
        standing decision (``utku-ai-setup-wizard-gap-model.md`` § "Three owner decisions") is that a
        re-run diffs against already-confirmed content and *says so in words when nothing is new*.
        ``n_generated: 0`` does not say that: it is the same number a structurally blind detector
        returns, which is the defect ``coverage`` already answers for the other half of the sentence.
        So the response now carries ``new`` / ``still_open`` / ``settled`` / ``stranded`` counts, an
        explicit ``nothing_new`` boolean, and the sentence the wizard prints -- composed on this side
        rather than in the client, so ``curl`` and the UI read the same words. Producing it needs the
        *unfiltered* candidate set (a generator that pre-filters cannot say what it re-derived) and
        the records the corpus dedup dropped (``drop_already_answered`` returns both halves now), and
        that is the whole of the change to this route's pipeline.

        **Categories B and E read the live database, through the governed path.** Both are about a
        column's real value vocabulary, and both used to gate on ``ColumnAsset.sample_values``, which
        ``corpus/seed.py`` never populates -- so neither could fire on any live-seeded corpus.
        ``curator/elicitation.py::read_observed_values`` supplies the values instead, one
        ``serve/fetch.sample_rows`` call per keyword-gated column (bounded by ``MAX_VALUE_READS``),
        which is the same ``prepare()``-checked, ledgered executor path the live agent's own
        ``sample_rows`` tool takes. ``session.connector``/``.corpus``/``.policy`` are exactly what a
        served turn gets (``serve/session.py::Session.configurable`` hands the same three objects to
        every node), so nothing is constructed here that a turn would not also have.

        **The structural scan reads rows too**, through the same governed path: one
        ``serve/fetch.compare_column_pair`` per name-alike column pair (bounded by
        ``gaps.MAX_PAIR_COMPARISONS``), which is a row-wise ``IS DISTINCT FROM`` count and not a
        value-set read -- the two columns that made this detector necessary hold the *identical* 554
        distinct customer ids and disagree on 6 305 of 6 312 rows -- plus one
        ``serve/fetch.count_distinct_values`` per candidate join key (bounded by
        ``gaps.MAX_KEY_PROBES``), because whether a column identifies a row is the one thing the
        seeded corpus cannot say and ``pg_rename_decoy`` declares no constraint to read it from.

        ``ledger`` in the response is every attempt row from both -- named as ``GET /turns/{id}``
        already names the same thing (``routes.py``: ``"ledger": execution["attempts"]``). It is
        returned rather than appended to ``runs/serve/*.jsonl``, because that log holds *turn* records
        judged by ``register/record.py``'s required fields and a generate call is not a turn;
        synthesising the fields to make one fit would be the "field the engine does not observe"
        defect. Returning the rows keeps the property that matters -- a governed statement is never
        issued from here without its verdict being visible to the caller who caused it.

        ``coverage`` is ``GapScan.coverage``: one "ran / skipped, and why" line per structural
        detector. It exists because an empty result is otherwise indistinguishable from a
        structurally blind detector, which is exactly what this route returned on ``beer_factory``
        before -- ``n_generated: 0`` with no way to tell that every gate had been evaluated in the
        wrong language. Only the structural detectors report it: the keyword generator has no
        equivalent (its "considered" set is a word list, not a measured population), and inventing
        rows for it here would be a coverage claim nothing computed.

        **Reporting caps are absent by construction, not by ordering.** Neither generator drops a
        finding to fit a quota (``limit_per_category`` is gone), and no two detectors share a budget,
        so 93 undescribed columns cannot crowd out one disagreeing join key.
        """
        from fastapi import HTTPException

        from governed_bi.curator.candidate_rules import (
            drop_already_answered,
            enforce_audience_language,
        )
        from governed_bi.curator.clarifications import load_clarifications, write_clarifications
        from governed_bi.curator.elicitation import (
            generate_candidate_questions,
            read_observed_values,
        )
        from governed_bi.curator.elicitation_terms import read_term_cardinalities
        from governed_bi.curator.gaps import apply_cluster_dependencies, detect_structural_gaps
        from governed_bi.curator.scan_report import diff_scan_against_ledger, scan_report_payload

        if session.corpus_root is None:
            raise HTTPException(status_code=409, detail="this session has no corpus_root to write back to")

        tables = [a for a in session.assets_by_id.values() if a.asset_type.value == "table"]
        existing = load_clarifications(session.corpus_root)
        # The value read runs first because *both* halves consume it now: the keyword generator's B,
        # E and S6, and the structural scan's join detector, which asks whether two look-alike
        # columns of two tables draw on the same domain. One read per column, shared -- reading them
        # twice would double the governed statements for one fact.
        observed, value_ledger = read_observed_values(
            tables,
            session.assets_by_id,
            connector=session.connector,
            corpus=session.corpus,
            policy=session.policy,
        )
        # One governed ``count(*) / count(distinct c)`` per column whose *name* carries an ambiguous
        # business term -- 2 on ``app_store``, 0 on German ``beer_factory``. It is a second statement
        # per column rather than a wider value read because the capped distinct-value list can never
        # say how many rows a column has, and "one value per record" vs "42 values across 6 312
        # records" is the grain distinction the business half of A is built on.
        cardinalities, cardinality_ledger = read_term_cardinalities(
            tables,
            session.assets_by_id,
            connector=session.connector,
            corpus=session.corpus,
            policy=session.policy,
        )
        scan = detect_structural_gaps(
            tables,
            session.assets_by_id,
            connector=session.connector,
            corpus=session.corpus,
            policy=session.policy,
            # ``retrieve/structure.py``'s canonical, endpoint-reconciled edges -- the session already
            # holds them, and reconciling a join's ``left_table`` spelling to a table id a second
            # time here would bind an edge to the wrong table rather than merely lose it.
            join_edges=session.structure.join_edges,
            observed_values=observed,
        )
        keyword_records = generate_candidate_questions(
            tables,
            session.assets_by_id,
            observed_values=observed,
            cardinalities=cardinalities,
        )
        # Then the rules about the *presented set* rather than about any one candidate, in the order
        # they have to run: dependency stamping, then "is this already answered", then "can its
        # audience read it", then "have we asked this before". The corpus dedup runs *after* the
        # stamp because a prerequisite that is already answered is a prerequisite that is met, and
        # only the stamped list knows which records were waiting on the one being dropped --
        # `drop_already_answered` clears those edges as it goes (found live: without that,
        # suppressing an answered cluster question left two E cards permanently "Waiting" on an id in
        # no ledger).
        #
        # The scope filter runs *last*, inside the diff, and that ordering is what the report is made
        # of: everything upstream of it now sees the whole re-derived set, so "16 carried forward
        # from an earlier scan" is a measurement rather than an absence.
        #
        # `_reload_assets`, not `session.assets_by_id`: the frozen mapping is a run constant, and the
        # whole point of the dedup is that an answer folded a minute ago on this same server should
        # already have settled its question (`/corpus/conflicts` reloads for the same reason).
        kept, settled_by_corpus = drop_already_answered(
            apply_cluster_dependencies([*scan.records, *keyword_records], scan.gated_columns),
            {a.id: a for a in _reload_assets(session)},
            schema=session.db_id,
        )
        report = diff_scan_against_ledger(
            enforce_audience_language(kept), settled_by_corpus, existing
        )
        new_records = list(report.new)
        if new_records:
            write_clarifications(session.corpus_root, [*existing, *new_records])
        return {
            "generated": [_clarification_row(r) for r in new_records],
            "n_generated": len(new_records),
            "report": scan_report_payload(report),
            "ledger": [dict(row) for row in (*scan.ledger, *value_ledger, *cardinality_ledger)],
            "coverage": [
                {
                    "detector": c.detector,
                    "gap_type": c.gap_type,
                    "considered": c.considered,
                    "measured": c.measured,
                    "found": c.found,
                    "note": c.note,
                }
                for c in scan.coverage
            ],
        }


    @router.get("/elicitation/candidates")
    def elicitation_candidates() -> list[dict[str, Any]]:
        """Every Setup Wizard candidate (``source == "elicitation_wizard"``), open **and**
        answered -- the wizard needs both to show onboarding progress, unlike ``/clarifications``'s
        own optional ``status`` filter.

        ``session.corpus_root is None`` returns an empty list, matching ``/clarifications``'s own
        handling of "nothing to read here."

        **Adds a derived ``blocked``** on top of ``_clarification_row``'s persisted fields:
        ``curator/clarifications.py::unmet_prerequisites(record, records) != ()``, i.e. this
        candidate's ``blocked_by`` names a question that is not answered yet. Derived rather than
        stored for the reason ``answer_text`` beside it is — it is a fact about the ledger as a whole
        at read time, not about the row.

        Those edges are real as of the commit that wired ``curator/gaps.py`` into
        ``POST /elicitation/generate``: a near-duplicate-cluster question on a contested column is
        written with the A/B/E questions naming that column pointing at it, so ``blocked`` flips to
        ``false`` for them the moment the cluster question is answered through
        ``POST /clarifications/{id}/answer``. Before that they could only be hand-seeded.

        Computed here and not on ``_clarification_row``/``GET /clarifications`` because the
        dependency order is a constraint on the *wizard's* sequencing
        (``utku-ai-setup-wizard-gap-model.md`` § "Presentation consequences", point 2): no A/B/E
        question may be presented before the near-duplicate-cluster question that decides which of
        two look-alike columns is authoritative, or the admin is invited to certify a value mapping
        onto a decoy. ``/clarifications`` is the raw ledger view and has no ordered flow to gate.
        The client renders a blocked candidate as not-yet-answerable rather than hiding it, and
        resolves the ``blocked_by`` ids against this same list to say what it is waiting for.
        """
        from governed_bi.curator.clarifications import load_clarifications, unmet_prerequisites

        if session.corpus_root is None:
            return []
        records = load_clarifications(session.corpus_root)
        return [
            {**_clarification_row(r), "blocked": bool(unmet_prerequisites(r, records))}
            for r in records
            if r.source == "elicitation_wizard"
        ]

    return router
