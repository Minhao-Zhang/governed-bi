"""Custom REST routes mounted by ``langgraph.json``'s ``http.app`` (ADR 0007 §7).

Route shapes follow ``docs/openapi.json``. Capabilities report what is actually built.
Only the two chat routes need a model — corpus browsing and the audit surface are model-free.

**The surface has a constructor** (2026-08-11). :func:`make_app` takes its three dependencies —
the session, the compiled chat graph and the turn log — and returns an app over exactly those.
:func:`app_from_environment` is the adapter the process entry uses, and is the only thing here
that resolves anything from the environment; the module-level :data:`app` is that adapter's
output because ``langgraph.json`` names an attribute rather than a factory.

Before that the app was assembled from process globals: a memoised ``_SESSION`` in
``graph_app``, a module-level ``_GRAPH``, and a module-level LRU list. The cost is on the record
— ``tests/serve/test_chat_transport.py`` states that the routes could not be exercised over HTTP
because they build a Postgres connector and seed a corpus, so ``POST /chat`` was tested by
calling ``_shape`` directly, and seven of the ten specifications in
``tests/api/test_http_contract.py`` were strict-xfail stubs for want of a way to construct this.

The two adapters are what justify the seam: the environment in production, a fake ``Session`` in
tests. Resolution stays **lazy** for the environment one — importing this module must not build
a Postgres session, or every test that imports it needs a database.

**None of these routes asks for a credential** (2026-08-13). Between 2026-08-10 and 2026-08-13 a
middleware here refused every path but ``/livez`` without a shared ``GOVERNED_BI_API_KEY``,
because the platform will not apply its own auth middleware to a custom app (``langgraph.json``'s
``http.enable_custom_route_auth`` raises ``ValueError: Cannot apply middleware: route
_IncludedRouter(...) has no app`` on fastapi 0.141, and the server then never binds). That closed
audit A7. It has been deliberately removed: this is a single-operator dev engine on
``127.0.0.1``, LangGraph Studio's bootstrap fetches carry no custom headers so the key made
Studio unusable, and the maintainer chose reachability over transport auth.

So A7 is open again, knowingly: ``/audit/turns`` and ``/audit/turns/{turn_id}/trace`` hand every
thread's SQL, the full turn records and an absolute log path to anything that can reach the port,
and ``/chat`` will spend model budget for it. The ``_cors_headers`` helper that made a 401 legible
to a browser went with the middleware — with no refusal to head by hand, every response now
passes back through the platform's ``CORSMiddleware`` normally. See ``api/auth.py``, which keeps
the state-write denials that are *not* authentication.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from governed_bi.api import trace_store
from governed_bi.api.browse import DEFAULT_NODE_BUDGET, subgraph
from governed_bi.api.browse_routes import make_router
from governed_bi.api.curation_routes import make_curation_router
from governed_bi.api.visibility import visible
from governed_bi.register.assets import ASSET_REGISTER
from governed_bi.serve.messages import surface_answer_text
from governed_bi.serve.runtime import bool_knob

__all__ = ["make_app", "app_from_environment", "app"]


#: Cap how many `/chat` threads one app's ``InMemorySaver`` retains. Eval already calls
#: ``delete_thread`` per question; without a bound here a long-lived API worker keeps every
#: session (~100 KB+/turn). Threads with a pending clarification are never evicted.
_CHAT_THREAD_CAP = 32


# ── the seam ─────────────────────────────────────────────────────────────────


def make_app(session: Any, graph: Any, turn_log: Any) -> FastAPI:
    """An app over exactly these three dependencies. **The constructor.**

    ``session`` is a :class:`~governed_bi.serve.session.Session` (or anything with its read
    surface: ``assets_by_id``, ``structure``, ``connector``, ``agent_model``, ``knobs_resolved``,
    ``corpus_content_hash``, ``problems``). ``graph`` is a compiled, sync-callable serve graph —
    ``None`` is allowed and means this app has no chat transport, which the chat routes report as
    a refusal rather than a crash. ``turn_log`` is anything exposing ``append_turn``,
    ``list_turns``, ``get_turn``, ``SUMMARY_FIELDS`` and ``TURN_LOG_DIR``;
    :mod:`governed_bi.api.trace_store` is the production one.

    All three are required and none is defaulted. A default would put the environment back in
    the constructor, which is the thing this exists to remove.
    """
    return _build_app(lambda: session, lambda: graph, turn_log)


def app_from_environment() -> FastAPI:
    """The process entry's adapter: the same app, over dependencies resolved on first request.

    Lazy on purpose. ``langgraph.json`` points ``http.app`` at the module attribute below, so
    this runs at import — and ``session_from_environment`` builds a Postgres connector and seeds
    a corpus. Resolving it here would make importing this module require a database.
    """
    from governed_bi.api.graph_app import session_from_environment

    return _build_app(session_from_environment, _chat_graph, trace_store)


#: Process-wide compiled graph + checkpointer for the environment adapter.
#: ``compile_graph()`` builds a fresh saver per call; compiling once keeps ``thread_id``
#: meaningful across turns and interrupts.
_GRAPH: Any = None


def _chat_graph() -> Any:
    """``POST /chat``'s graph, compiled once for the process.

    **The no-``accept`` topology**, deliberately and unlike the streamed surface: this route
    builds the turn in-process through ``Session.turn`` and passes the whole of ``ServeState``,
    where the served graph derives it from a client conversation (``api/graph_app``). Two
    topologies, two input schemas; see ``serve/graph.py::build_graph``.
    """
    global _GRAPH
    if _GRAPH is None:
        from langgraph.checkpoint.memory import InMemorySaver

        from governed_bi.serve.graph import as_sync, build_graph

        # `as_sync`, because every node is `async def` now (the only shape LangGraph attaches a
        # node timeout to) and this route's handlers are sync `def`. Starlette runs those in a
        # worker thread with no running loop, so the facade's `asyncio.run` is safe here.
        _GRAPH = as_sync(build_graph().compile(checkpointer=InMemorySaver()))
    return _GRAPH


def _build_app(
    get_session: Callable[[], Any],
    get_graph: Callable[[], Any],
    turn_log: Any,
) -> FastAPI:
    """Assemble the app from two dependency thunks and a turn log.

    Thunks rather than values, so :func:`make_app` can hand over concrete objects and
    :func:`app_from_environment` can defer. That is the one difference between the two adapters,
    and it lives here rather than in the routes.
    """
    app = FastAPI(title="governed-bi", version="2")

    #: Per-app, not per-process: two apps in one test session must not evict each other's threads.
    chat_threads: list[str] = []

    @app.get("/livez")
    def livez() -> dict[str, Any]:
        """Liveness only — does not touch the session."""
        return {"ok": True}

    @app.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """What this server can actually do. The UI blocks on this response."""
        return capabilities_for(get_session())

    @app.get("/corpus/assets")
    def corpus_assets(type: str | None = None) -> list[dict[str, Any]]:
        """Assets of one type as rows. ``type`` is validated against the register."""
        return asset_rows(visible(get_session()), type)

    @app.post("/chat")
    def chat(body: dict[str, Any]) -> dict[str, Any]:
        """Serve one turn, blocking. Degradation path — streaming is the primary transport.

        Request: ``{question, session_id, history: [{role, text}]}``.
        Response: v2 answer shape ``{outcome, text, failed_stage, error_type, refused_by,
        record}`` plus ``answer_text``. ``session_id`` becomes ``thread_id`` on the config.
        Sync handler so connector/model calls run in FastAPI's threadpool.
        """
        session, compiled = get_session(), get_graph()
        if compiled is None:
            return _error("this app was built with no graph, so it cannot serve a turn")
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
        shaped = _logged(turn_log, _shape(compiled.invoke(turn, config)), question)
        # Evict after the invoke so a pending clarification on this thread is visible to the LRU.
        _touch_chat_thread(compiled, chat_threads, thread_id)
        return shaped

    @app.post("/chat/resume")
    def chat_resume(body: dict[str, Any]) -> dict[str, Any]:
        """Answer a clarification paused by ``ask_user``.

        Request: ``{session_id, clarification_id?, answer | choice_id | declined, identity?}``.
        """
        session, compiled = get_session(), get_graph()
        if compiled is None:
            return _error("this app was built with no graph, so it cannot resume a turn")
        thread_id = str(body.get("session_id") or "")
        if not thread_id:
            return _error("no session_id: a resume needs the thread its question is paused on")

        config = _config(session, None, thread_id)
        pending = _pending_on_thread(compiled, config)
        if pending is None:
            return _error(f"no clarification is pending on session {thread_id!r}")

        wanted = str(body.get("clarification_id") or "")
        if wanted and wanted != pending.get("clarification_id"):
            return _error(
                f"clarification_id {wanted!r} does not match the pending question "
                f"{pending.get('clarification_id')!r}"
            )

        from governed_bi.serve.resume import ResumeRejected, resume_clarification

        reply = _resume_reply(body)
        try:
            out = resume_clarification(
                compiled,
                config=config,
                identity=_identity(body, thread_id),
                answer=reply or str(body.get("answer") or ""),
            )
        except ResumeRejected:
            return _error(
                "resume identity mismatch: the caller answering is not the caller that was asked"
            )
        shaped = _logged(turn_log, _shape(out), str(pending.get("question") or ""))
        _touch_chat_thread(compiled, chat_threads, thread_id)
        return shaped

    @app.get("/graph")
    def er_graph(
        schema: str | None = None,
        focus: str | None = None,
        radius: int = 1,
        node_budget: int = DEFAULT_NODE_BUDGET,
        kinds: str | None = None,
    ) -> dict[str, Any]:
        """Bounded ER relationship view (ADR 0009 D2). ``meta.truncated`` / ``meta.dropped`` are required."""
        return _bounded(
            _graph_payload(visible(get_session())), schema, focus, radius, node_budget, kinds
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
        return _bounded(
            _knowledge_payload(visible(get_session())), schema, focus, radius, node_budget, kinds
        )

    # Audit surface under `/audit` (avoids colliding with LangGraph Server's `/runs`).

    @app.get("/audit/turns")
    def audit_turns(limit: int = 50, thread_id: str | None = None) -> dict[str, Any]:
        """Served turns, newest first. ``incomplete_fields`` is judged against today's register.

        ``thread_id`` narrows to one conversation, which is what a transcript needs: the graph
        checkpoint holds only the newest turn's record (``PER_TURN_RESET``), so this log is the
        only source for the earlier turns of a thread.
        """
        return turns_page(turn_log, limit=limit, thread_id=thread_id)

    @app.get("/audit/turns/{turn_id}/trace")
    def audit_trace(turn_id: str) -> dict[str, Any]:
        """Turn fields grouped by owning stage, derived from ``RECORD_REGISTER``."""
        return trace_for(turn_log, turn_id)

    @app.get("/audit/corpus")
    def audit_corpus() -> dict[str, Any]:
        """Corpus inventory plus problems. ``fatal`` and ``degradations`` stay separate (ADR 0008 D9)."""
        return corpus_audit(visible(get_session()))

    # Mounted last so the app's own paths are declared first; the router's own ordering
    # (`/schema/summary` before `/schema/{table_id}`) is stated in `make_router`.
    app.include_router(make_router(_DeferredSession(get_session)))
    # Same factory shape and the same deferred session as the browse router above. Mounted
    # after it, so a curation route can never shadow a browse one by registration order.
    app.include_router(make_curation_router(_DeferredSession(get_session)))
    return app


class _DeferredSession:
    """A session that resolves on first attribute read.

    :func:`make_router` takes a session and not a thunk, because that is the honest interface
    for a caller that has one — and every caller except the process entry does. This is the
    adapter for the one that does not: ``langgraph.json`` names a module attribute, so
    :data:`app` is built at import, and resolving the environment there would make importing
    this module require a database.

    Attribute reads only, which is all the browse routes do (``assets_by_id``). Under
    :func:`make_app` the thunk is a constant, so this forwards to the object the caller passed
    and adds nothing but one attribute lookup.
    """

    __slots__ = ("_get",)

    def __init__(self, get: Callable[[], Any]) -> None:
        self._get = get

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)


# ── projections: a function of the session, testable without an app ──────────


def capabilities_for(session: Any) -> dict[str, Any]:
    """``/capabilities``' body. Every field is an observation (ADR 0007 §7)."""
    #: Bound once so ``can_clarify`` cannot drift from ``can_stream``.
    can_stream = True
    from governed_bi.serve.runtime_overrides import overrides as _live_overrides

    live_knobs = {**session.knobs_resolved, **_live_overrides()}
    return {
        "environment": "local",
        "dialect": getattr(session.connector, "dialect", "postgres"),
        "can_edit": False,
        "edit_mode": "none",
        "can_stream": can_stream,
        "has_live_model": session.agent_model is not None,
        "model": session.knobs_resolved.get("chat_model"),
        "can_scope": True,
        "can_search": False,
        # Clarification UI mounts only on the streaming transport.
        "can_clarify": can_stream and session.agent_model is not None,
        # Honest durability: `/chat` compiles with InMemorySaver (process-local). LangGraph
        # Server injects its own saver for the stream path; local_dev is still not Postgres.
        # Pause/resume does not survive a process restart on either surface today.
        "checkpoint_durable": False,
        "hitl_survives_process_restart": False,
        # Corpus curation (the Agreed Assumptions / Needs Review admin tabs) is a different
        # question from `can_clarify` above -- that one is "does a live ask_user interrupt
        # fire", this one is "would /corpus/conflicts*, /corpus/assumptions and
        # /corpus/drafts/{id}/approve actually work for this session". Mirrors those routes'
        # own precondition exactly (`corpus_root is None` -> 409), so the UI can gate on this
        # instead of reusing `can_clarify` for a thing it says nothing about.
        "can_curate_corpus": getattr(session, "corpus_root", None) is not None,
        # Read the same way every other knob is: `knobs_resolved` is the flat resolved mapping
        # `bool_knob`'s first precedence tier already checks, so these are the register's
        # declared values unless a deployment overrode them -- never a second literal that
        # could drift from what a turn actually used.
        #
        # Layered with the operator's live switches for the same reason `Session.turn` is:
        # `session.knobs_resolved` was resolved once, at construction, so a switch flipped since
        # then would be reported here as still off while every new turn ran with it on. This
        # endpoint is a live claim about what the engine will do next, not a record of a past run,
        # so it reads the current value -- and `Session.turn` is what puts that same value into the
        # record, so the two cannot disagree.
        "enable_structured_percentage_check": bool_knob(
            live_knobs, "enable_structured_percentage_check"
        ),
        "enable_clarification_to_draft": bool_knob(live_knobs, "enable_clarification_to_draft"),
    }


def _provenance_status(asset: Any) -> str | None:
    """``asset.audit.provenance.status``, or ``None`` when any link is absent.

    Absent ≠ clean: ADR 0005 §6 requires "not measured" to stay distinguishable.
    """
    provenance = getattr(getattr(asset, "audit", None), "provenance", None)
    status = getattr(provenance, "status", None)
    return status.value if status is not None else None


def asset_rows(session: Any, type: str | None = None) -> list[dict[str, Any]]:
    """``/corpus/assets``' body.

    ``provenance_status`` and ``excluded`` are required by the client's ``assetRowSchema``.
    """
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


def _graph_payload(session: Any) -> dict[str, Any]:
    """ER graph: tables as nodes, join relationships as edges.

    Drawn from ``CorpusStructure`` (ADR 0005 §2.8.2), not a second asset walk.
    """
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


def _knowledge_payload(session: Any) -> dict[str, Any]:
    """Semantic graph: every asset kind, edges from the reference closure.

    Columns are re-pointed to their owning table (not drawn as nodes).
    """
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


def _bounded(
    payload: dict[str, Any],
    schema: str | None,
    focus: str | None,
    radius: int,
    node_budget: int,
    kinds: str | None,
) -> dict[str, Any]:
    """The scope contract both graph routes share, applied once."""
    return subgraph(
        nodes=payload["nodes"],
        edges=payload["edges"],
        schema=schema,
        focus=focus,
        radius=radius,
        kinds=[k.strip() for k in kinds.split(",") if k.strip()] if kinds else None,
        node_budget=node_budget,
    )


def corpus_audit(session: Any) -> dict[str, Any]:
    """``/audit/corpus``' body. ``fatal`` and ``degradations`` stay separate (ADR 0008 D9)."""
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


# ── projections of the turn log ───────────────────────────────────────────────


def turns_page(turn_log: Any, *, limit: int = 50, thread_id: str | None = None) -> dict[str, Any]:
    """``/audit/turns``' body."""
    turns = turn_log.list_turns(limit=limit, thread_id=thread_id)
    return {
        "turns": turns,
        "meta": {
            "n": len(turns),
            "log_dir": str(turn_log.TURN_LOG_DIR),
            "columns": list(turn_log.SUMMARY_FIELDS),
        },
    }


def trace_for(turn_log: Any, turn_id: str) -> dict[str, Any]:
    """``/audit/turns/{id}/trace``' body: fields grouped by their register-declared owner."""
    from governed_bi.register.record import RECORD_REGISTER, missing_required, undeclared_keys
    from governed_bi.register.stages import Stage

    entry = turn_log.get_turn(turn_id)
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


# ── chat plumbing: pure over what it is handed ────────────────────────────────


def _touch_chat_thread(compiled: Any, threads: list[str], thread_id: str) -> None:
    """Remember ``thread_id`` as most-recently used; drop the oldest idle threads over the cap.

    **A thread leaves the list only once the saver has actually dropped it.** The first version
    popped the victim before checking whether the checkpointer could delete it, so a saver
    without ``delete_thread`` left the thread retained but untracked — the unbounded growth the
    cap exists to prevent, reported as a bounded LRU. ``InMemorySaver`` does expose the method,
    but the guard is here precisely because the saver gets swapped (LangGraph Server injects its
    own), so the case it was written for is the one that must not fail silently.
    """
    if thread_id in threads:
        threads.remove(thread_id)
    threads.append(thread_id)

    saver = getattr(compiled, "checkpointer", None)
    delete = getattr(saver, "delete_thread", None)
    if not callable(delete):
        return  # nothing can be evicted; keep the list honest rather than forgetting threads

    # Oldest first, newest last; the current thread is never a candidate, because a
    # clarification it just raised may not be visible until the invoke that follows.
    current = threads[-1]
    remaining = len(threads) - _CHAT_THREAD_CAP
    keep: list[str] = []
    for victim in threads[:-1]:
        # Left to right: no checkpointer read at all once enough have been evicted.
        pending = _pending_on_thread(compiled, {"configurable": {"thread_id": victim}})
        if remaining > 0 and pending is None:
            delete(victim)
            remaining -= 1
        else:
            keep.append(victim)  # under the cap, or a paused turn that is never evicted
    keep.append(current)
    threads[:] = keep


def _config(session: Any, question: str | None, thread_id: str) -> dict[str, Any]:
    """Request config. ``thread_id`` goes on the config (what LangGraph checkpoints on)."""
    config = session.configurable(question=question) if question else session.configurable()
    config["configurable"]["thread_id"] = thread_id
    return config


def _resume_reply(body: dict[str, Any]) -> Any:
    """What ``resume_clarification`` receives: the structured reply pulled out of the request
    body, or the bare ``answer`` string when nothing structured was sent.

    ``defer`` sits beside ``declined`` for the reason this fork's Bug 3 exists at all:
    ``governed-bi-ui``'s "I don't know -- ask the admin later" button sends ``{"defer": true}``.
    Without that key in the filter ``reply`` came back ``{}`` -- falsy -- and the bare-``answer``
    fallback produced ``""``, so a live client hitting exactly the path
    ``_clarification_answer``'s ``defer`` alias was fixed for still reached the model as an
    empty-string answer, one HTTP layer above that fix.
    """
    reply = {k: v for k, v in body.items() if k in ("answer", "choice_id", "declined", "defer")}
    return reply or str(body.get("answer") or "")


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


def _pending_on_thread(compiled: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """The clarification paused on this thread, from the checkpoint."""
    tasks = getattr(compiled.get_state(config), "tasks", ()) or ()
    return _clarification([i for task in tasks for i in (getattr(task, "interrupts", ()) or ())])


def _shape(out: dict[str, Any]) -> dict[str, Any]:
    """One response shape for both chat routes, including the paused one.

    Pure over ``out``: a draft consulted ``graph.get_state`` when no ``__interrupt__`` was
    present, which put a checkpoint read — and therefore a session build — on the answered path
    of every request.
    """
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
    answer["answer_text"] = surface_answer_text(answer, out)
    answer.setdefault("clarification", None)
    return answer


def _logged(turn_log: Any, shaped: dict[str, Any], question: str) -> dict[str, Any]:
    """Append the turn to the audit log. Paused turns (no record) are skipped."""
    record = shaped.get("record") or {}
    if not record.get("turn_id"):
        return shaped
    _turn_id, error = turn_log.append_turn(
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


#: What ``langgraph.json``'s ``http.app`` points at. An attribute, not a factory — the platform
#: imports the name — so the environment adapter is what builds it.
app = app_from_environment()
