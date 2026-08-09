"""Custom REST routes mounted by ``langgraph.json``'s ``http.app`` (ADR 0007 §7).

Route shapes follow ``docs/openapi.json``. Capabilities report what is actually built.
No ungated route needs a model — corpus browsing is model-free.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import FastAPI

from governed_bi.api.browse import DEFAULT_NODE_BUDGET, subgraph
from governed_bi.api.browse_routes import router as browse_router
from governed_bi.api.graph_app import session_from_environment
from governed_bi.api.trace_store import (
    SUMMARY_FIELDS,
    TURN_LOG_DIR,
    append_turn,
    get_turn,
    list_turns,
)
from governed_bi.register.assets import ASSET_REGISTER
from governed_bi.serve.messages import last_ai_text
from governed_bi.serve.runtime import bool_knob

__all__ = ["app"]

app = FastAPI(title="governed-bi", version="2")

app.include_router(browse_router)


def _session() -> Any:
    return session_from_environment()


#: Process-wide compiled graph + checkpointer. ``compile_graph()`` builds a fresh saver per
#: call; compiling once keeps ``thread_id`` meaningful across turns and interrupts.
_GRAPH: Any = None


def _graph() -> Any:
    global _GRAPH
    if _GRAPH is None:
        from langgraph.checkpoint.memory import InMemorySaver

        from governed_bi.serve.graph import as_sync, build_graph

        # `as_sync`, because every node is `async def` now (the only shape LangGraph attaches a
        # node timeout to) and this route's handlers are sync `def`. Starlette runs those in a
        # worker thread with no running loop, so the facade's `asyncio.run` is safe here.
        _GRAPH = as_sync(build_graph().compile(checkpointer=InMemorySaver()))
    return _GRAPH


@app.get("/livez")
def livez() -> dict[str, Any]:
    """Liveness only — does not touch the session."""
    return {"ok": True}


@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """What this server can actually do. The UI blocks on this response."""
    session = _session()
    #: Bound once so ``can_clarify`` cannot drift from ``can_stream``.
    can_stream = True
    return {
        "environment": "local",
        "dialect": getattr(session.connector, "dialect", "postgres"),
        "can_edit": False,
        "edit_mode": "none",
        "can_stream": can_stream,
        "has_live_model": session.agent_model is not None,
        "model": session.knobs_resolved.get("llm_model"),
        "can_scope": True,
        "can_search": False,
        # Clarification UI mounts only on the streaming transport.
        "can_clarify": can_stream and session.agent_model is not None,
        # Corpus curation (the Agreed Assumptions / Needs Review admin tabs) is a different
        # question from can_clarify above -- that one is "does a live ask_user interrupt
        # fire", this one is "would /corpus/conflicts*, /corpus/assumptions, and
        # /corpus/drafts/{id}/approve actually work for this session". Mirrors those routes'
        # own precondition exactly (session.corpus_root is None -> 409), so the UI can gate on
        # this instead of reusing can_clarify for a thing it says nothing about.
        "can_curate_corpus": session.corpus_root is not None,
        # UtkuAI, ported (utku-ai-v2-porting-spec.md), not upstream. Read the same way every
        # other knob is: session.knobs_resolved is the flat resolved mapping bool_knob's first
        # precedence tier already checks, so this is the register's declared value unless a
        # deployment overrode it -- never a second literal that could drift from what a turn
        # actually used.
        "enable_structured_percentage_check": bool_knob(
            session.knobs_resolved, "enable_structured_percentage_check"
        ),
        "enable_clarification_to_draft": bool_knob(session.knobs_resolved, "enable_clarification_to_draft"),
    }


def _provenance_status(asset: Any) -> str | None:
    """``asset.audit.provenance.status``, or ``None`` when any link is absent.

    Absent ≠ clean: ADR 0005 §6 requires "not measured" to stay distinguishable.
    """
    provenance = getattr(getattr(asset, "audit", None), "provenance", None)
    status = getattr(provenance, "status", None)
    return status.value if status is not None else None


@app.get("/corpus/assets")
def corpus_assets(type: str | None = None) -> list[dict[str, Any]]:
    """Assets of one type as rows. ``type`` is validated against the register.

    ``provenance_status`` and ``excluded`` are required by the client's ``assetRowSchema``.
    """
    session = _session()
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


@app.post("/corpus/drafts/{asset_id}/approve")
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

    from governed_bi.corpus.drafts import DraftNotFound, DraftNotPending, approve_draft as approve

    session = _session()
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


@app.get("/corpus/assumptions")
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
    session = _session()
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


@app.get("/corpus/conflicts")
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
    session = _session()
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


@app.post("/corpus/conflicts/{asset_id}/resolve")
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

    session = _session()
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


def _graph_payload() -> dict[str, Any]:
    """ER graph: tables as nodes, join relationships as edges.

    Drawn from ``CorpusStructure`` (ADR 0005 §2.8.2), not a second asset walk.
    """
    session = _session()
    structure = session.structure
    by_id = session.assets_by_id

    edges: list[dict[str, Any]] = []
    for left, right in sorted(structure.join_edges):
        join_ids = list(structure.joins_by_edge.get((left, right), ()))
        # Count distinct ON digests: bidirectional declarations yield two join assets.
        distinct_relationships = len({str(j).rsplit("_", 1)[-1] for j in join_ids})
        first = by_id.get(join_ids[0]) if join_ids else None
        confidence = getattr(first, "confidence", None)
        edges.append(
            {
                "id": join_ids[0] if join_ids else f"{left}->{right}",
                "source": left,
                "target": right,
                "on": str(getattr(first, "on", "") or ""),
                "cardinality": getattr(getattr(first, "cardinality", None), "value", None),
                "confidence": confidence,
                "low_confidence": bool(confidence is not None and confidence < 0.5),
                "join_ids": join_ids,
                "n_relationships": distinct_relationships,
            }
        )

    nodes: list[dict[str, Any]] = []
    for asset in sorted(by_id.values(), key=lambda a: a.id):
        if asset.asset_type.value != "table":
            continue
        columns = [by_id.get(cid) for cid in (getattr(asset, "columns", ()) or ())]
        columns = [c for c in columns if c is not None]
        nodes.append(
            {
                "id": asset.id,
                "label": getattr(asset, "physical_name", asset.id),
                "physical_name": getattr(asset, "physical_name", asset.id),
                "kind": "table",
                "schema": structure.schema_tags.get(asset.id),
                "row_count": getattr(asset, "row_count", None),
                "n_columns": len(columns),
                "excluded": bool(getattr(getattr(asset, "governance", None), "excluded", False)),
                "has_suspect": any(
                    getattr(getattr(c, "reliability", None), "status", None) is not None
                    and getattr(getattr(c, "reliability", None), "status").value == "suspect"
                    for c in columns
                ),
                "provenance_status": _provenance_status(asset),
            }
        )
    return {"nodes": nodes, "edges": edges, "meta": {"n_nodes": len(nodes), "n_edges": len(edges)}}


#: Edge relation label by source asset type (client vocabulary).
_SEMANTIC_NODE_KINDS: frozenset[str] = frozenset(
    {"table", "join", "metric", "term", "note", "few_shot", "negative_example"}
)

_RELATION_BY_SOURCE: dict[str, str] = {
    "join": "join",
    "metric": "measures",
    "term": "grounds",
    "few_shot": "exemplifies",
    "column": "belongs_to",
    "table": "has_column",
}


def _knowledge_payload() -> dict[str, Any]:
    """Semantic graph: every asset kind, edges from the reference closure.

    Columns are re-pointed to their owning table (not drawn as nodes).
    """
    session = _session()
    structure = session.structure
    by_id = session.assets_by_id

    def _semantic_id(asset_id: str) -> str | None:
        """The node this id draws as: itself, its table, or nothing."""
        kind = structure.asset_types.get(asset_id, "")
        if kind in _SEMANTIC_NODE_KINDS:
            return asset_id
        if kind == "column":
            parent = getattr(by_id.get(asset_id), "parent_table", None)
            return parent if parent in by_id else None
        return None

    nodes = [
        {
            "id": asset.id,
            "kind": asset.asset_type.value,
            "label": getattr(asset, "physical_name", None) or getattr(asset, "name", None) or asset.id,
            "excluded": bool(getattr(getattr(asset, "governance", None), "excluded", False)),
            "provenance_status": _provenance_status(asset),
            "confidence": getattr(asset, "confidence", None),
            "schema": structure.schema_tags.get(asset.id),
        }
        for asset in sorted(by_id.values(), key=lambda a: a.id)
        if asset.asset_type.value in _SEMANTIC_NODE_KINDS
    ]

    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for source, targets in sorted(structure.references.items()):
        kind = structure.asset_types.get(source, "")
        relation = _RELATION_BY_SOURCE.get(kind, "related")
        drawn_source = _semantic_id(source)
        if drawn_source is None:
            continue
        for target in sorted(targets):
            drawn_target = _semantic_id(target)
            if drawn_target is None or drawn_target == drawn_source:
                continue
            if (drawn_source, drawn_target) in seen_edges:
                continue
            seen_edges.add((drawn_source, drawn_target))
            confidence = getattr(by_id.get(source), "confidence", None)
            edges.append(
                {
                    "id": f"{drawn_source}->{drawn_target}",
                    "source": drawn_source,
                    "target": drawn_target,
                    "relation": relation,
                    "confidence": confidence,
                    "low_confidence": bool(confidence is not None and confidence < 0.5),
                }
            )
    return {"nodes": nodes, "edges": edges, "meta": {"n_nodes": len(nodes), "n_edges": len(edges)}}


@app.post("/chat")
def chat(body: dict[str, Any]) -> dict[str, Any]:
    """Serve one turn, blocking. Degradation path — streaming is the primary transport.

    Request: ``{question, session_id, history: [{role, text}]}``.
    Response: v2 answer shape ``{outcome, text, failed_stage, error_type, refused_by, record}``
    plus ``answer_text``. ``session_id`` becomes ``thread_id`` on the config.
    Sync handler so connector/model calls run in FastAPI's threadpool.
    """
    session = _session()
    question = str(body.get("question") or "").strip()
    if not question:
        return _error("no question")

    thread_id = str(body.get("session_id") or "") or uuid.uuid4().hex[:16]
    turn_index = 1 + sum(1 for h in body.get("history") or [] if (h or {}).get("role") == "user")
    turn = session.turn(
        question,
        turn_index=turn_index,
        thread_id=thread_id,
        identity=_identity(body, thread_id),
    )
    config = _config(session, question, thread_id)
    return _logged(_shape(_graph().invoke(turn, config)), question)


@app.post("/chat/resume")
def chat_resume(body: dict[str, Any]) -> dict[str, Any]:
    """Answer a clarification paused by ``ask_user``.

    Request: ``{session_id, clarification_id?, answer | choice_id | declined, identity?}``.
    """
    session = _session()
    thread_id = str(body.get("session_id") or "")
    if not thread_id:
        return _error("no session_id: a resume needs the thread its question is paused on")

    config = _config(session, None, thread_id)
    pending = _pending_on_thread(config)
    if pending is None:
        return _error(f"no clarification is pending on session {thread_id!r}")

    wanted = str(body.get("clarification_id") or "")
    if wanted and wanted != pending.get("clarification_id"):
        return _error(
            f"clarification_id {wanted!r} does not match the pending question {pending.get('clarification_id')!r}"
        )

    from governed_bi.serve.resume import ResumeRejected, resume_clarification

    reply = {k: v for k, v in body.items() if k in ("answer", "choice_id", "declined")}
    try:
        out = resume_clarification(
            _graph(),
            config=config,
            identity=_identity(body, thread_id),
            answer=reply or str(body.get("answer") or ""),
        )
    except ResumeRejected:
        return _error("resume identity mismatch: the caller answering is not the caller that was asked")

    # Mining an answered clarification into a corpus draft (UtkuAI, ported) now happens inside
    # the compiled graph itself -- `serve/nodes/mine_corpus.py`, wired in right after
    # `agent_core` -- which `resume_clarification`'s own `graph.invoke()` call above already
    # triggered. A second call here would double-mine: `resume_clarification` and LangGraph
    # Server's native `/threads/{id}/runs/stream` both resume by invoking this same compiled
    # graph, so a route-level call was one of two places doing the same thing, and the other
    # was the one every real end-user interaction actually reaches. See `mine_corpus.py`'s
    # module docstring for why the trigger moved rather than being duplicated.

    # Logged here too, and with the *clarification* as the question. A resumed turn is the
    # one that produces the record, so leaving it out would make every clarified
    # conversation invisible to the audit surface — which is the half of the traffic most
    # worth auditing.
    return _logged(_shape(out), str(pending.get("question") or ""))


def _config(session: Any, question: str | None, thread_id: str) -> dict[str, Any]:
    """Request config. ``thread_id`` goes on the config (what LangGraph checkpoints on)."""
    config = session.configurable(question=question) if question else session.configurable()
    config["configurable"]["thread_id"] = thread_id
    return config


def _identity(body: dict[str, Any], thread_id: str) -> dict[str, str]:
    """Caller identity for ``resume_authorised``. Falls back to thread id when none supplied."""
    supplied = body.get("identity")
    if isinstance(supplied, str) and supplied:
        return {"token": supplied}
    if isinstance(supplied, dict):
        token = next((str(v) for v in supplied.values() if v), "")
        if token:
            return {"token": token}
    return {"token": thread_id}


def _clarification(interrupts: Any) -> dict[str, Any] | None:
    """The ``ask_user`` payload (ADR 0007 §6) among interrupts, or ``None``."""
    for item in interrupts or ():
        value = getattr(item, "value", item)
        if isinstance(value, dict) and value.get("kind") == "clarification":
            return value
    return None


def _pending_on_thread(config: dict[str, Any]) -> dict[str, Any] | None:
    """The clarification paused on this thread, from the checkpoint."""
    tasks = getattr(_graph().get_state(config), "tasks", ()) or ()
    return _clarification([i for task in tasks for i in (getattr(task, "interrupts", ()) or ())])


def _shape(out: dict[str, Any]) -> dict[str, Any]:
    """One response shape for both chat routes, including the paused one."""
    pending = _clarification(out.get("__interrupt__"))
    if pending is not None:
        return {
            "outcome": "clarification",
            "text": pending.get("question"),
            "failed_stage": None,
            "error_type": None,
            "refused_by": None,
            "record": {},
            "answer_text": None,
            "clarification": pending,
        }
    answer = dict(out.get("answer") or {})
    answer.setdefault("answer_text", None)
    if answer.get("answer_text") is None:
        answer["answer_text"] = last_ai_text(out)
    answer.setdefault("clarification", None)
    return answer


def _logged(shaped: dict[str, Any], question: str) -> dict[str, Any]:
    """Append the turn to the audit log. Paused turns (no record) are skipped."""
    record = shaped.get("record") or {}
    if not record.get("turn_id"):
        return shaped
    _turn_id, error = append_turn(
        record,
        question=question,
        answer_text=shaped.get("answer_text"),
        outcome=shaped.get("outcome"),
    )
    shaped["audit_logged"] = error is None
    if error is not None:
        shaped["audit_error"] = error
    return shaped


def _error(detail: str) -> dict[str, Any]:
    """A refusal a client can read, in the same shape as every other reply."""
    return {
        "outcome": "crashed",
        "text": detail,
        "failed_stage": "resume",
        "error_type": "ValueError",
        "refused_by": None,
        "record": {},
        "answer_text": None,
        "clarification": None,
    }


@app.get("/graph")
def er_graph(
    schema: str | None = None,
    focus: str | None = None,
    radius: int = 1,
    node_budget: int = DEFAULT_NODE_BUDGET,
    kinds: str | None = None,
) -> dict[str, Any]:
    """Bounded ER relationship view (ADR 0009 D2). ``meta.truncated`` / ``meta.dropped`` are required."""
    payload = _graph_payload()
    return subgraph(
        nodes=payload["nodes"],
        edges=payload["edges"],
        schema=schema,
        focus=focus,
        radius=radius,
        kinds=[k.strip() for k in kinds.split(",") if k.strip()] if kinds else None,
        node_budget=node_budget,
    )


@app.get("/knowledge-graph")
def knowledge_graph(
    schema: str | None = None,
    focus: str | None = None,
    radius: int = 1,
    node_budget: int = DEFAULT_NODE_BUDGET,
    kinds: str | None = None,
) -> dict[str, Any]:
    """Bounded semantic graph. Same scope contract as ``/graph``."""
    payload = _knowledge_payload()
    return subgraph(
        nodes=payload["nodes"],
        edges=payload["edges"],
        schema=schema,
        focus=focus,
        radius=radius,
        kinds=[k.strip() for k in kinds.split(",") if k.strip()] if kinds else None,
        node_budget=node_budget,
    )


# Audit surface under `/audit` (avoids colliding with LangGraph Server's `/runs`).


@app.get("/audit/turns")
def audit_turns(limit: int = 50, thread_id: str | None = None) -> dict[str, Any]:
    """Served turns, newest first. ``incomplete_fields`` is judged against today's register.

    ``thread_id`` narrows to one conversation, which is what a transcript needs: the graph
    checkpoint holds only the newest turn's record (``PER_TURN_RESET``), so this log is the only
    source for the earlier turns of a thread.
    """
    turns = list_turns(limit=limit, thread_id=thread_id)
    return {
        "turns": turns,
        "meta": {
            "n": len(turns),
            "log_dir": str(TURN_LOG_DIR),
            "columns": list(SUMMARY_FIELDS),
        },
    }


@app.get("/audit/turns/{turn_id}/trace")
def audit_trace(turn_id: str) -> dict[str, Any]:
    """Turn fields grouped by owning stage, derived from ``RECORD_REGISTER``."""
    from governed_bi.register.record import RECORD_REGISTER, missing_required, undeclared_keys
    from governed_bi.register.stages import Stage

    entry = get_turn(turn_id)
    if entry is None:
        return {"found": False, "turn_id": turn_id}
    record = entry.get("record") or {}
    absent = missing_required(record)

    by_stage: dict[str, list[dict[str, Any]]] = {}
    for field in RECORD_REGISTER:
        by_stage.setdefault(field.owner.value, []).append(
            {
                "name": field.name,
                "tier": field.tier.value,
                "value": record.get(field.name),
                "present": field.name in record and record.get(field.name) is not None,
                "required_and_absent": field.name in absent,
                "why": field.why,
            }
        )

    order = [stage.value for stage in Stage]
    stages = [
        {"stage": name, "fields": by_stage[name]}
        for name in sorted(by_stage, key=lambda n: (order.index(n) if n in order else len(order), n))
    ]
    return {
        "found": True,
        "turn_id": turn_id,
        "question": entry.get("question"),
        "answer_text": entry.get("answer_text"),
        "outcome": entry.get("outcome"),
        "asked_at": entry.get("asked_at"),
        "stages": stages,
        "ledger": (record.get("execution") or {}).get("attempts") or [],
        "terminal": (record.get("execution") or {}).get("terminal"),
        "missing_required": sorted(absent),
        "record": record,
        "undeclared_keys": sorted(undeclared_keys(record)),
    }


@app.get("/audit/corpus")
def audit_corpus() -> dict[str, Any]:
    """Corpus inventory plus problems. ``fatal`` and ``degradations`` stay separate (ADR 0008 D9)."""
    session = _session()
    counts: dict[str, int] = {}
    for asset in session.assets_by_id.values():
        counts[asset.asset_type.value] = counts.get(asset.asset_type.value, 0) + 1
    structure = session.structure
    return {
        "corpus_content_hash": session.corpus_content_hash,
        "assets": {"total": len(session.assets_by_id), "by_type": dict(sorted(counts.items()))},
        "schemas": sorted(
            {s for s in structure.table_schemas.values() if s},
        ),
        "structure": {
            "join_edges": len(structure.join_edges),
            "references": len(structure.references),
            "schema_tags": len(structure.schema_tags),
            "untagged_assets": len(session.assets_by_id) - len(structure.schema_tags),
            "table_pairs_with_joins": len(structure.joins_by_edge),
        },
        "problems": {
            "fatal": [str(p) for p in session.fatal_problems],
            "degradations": [str(p) for p in session.degradations],
            "n_fatal": len(session.fatal_problems),
            "n_degradations": len(session.degradations),
        },
        "servable": not session.fatal_problems,
    }
