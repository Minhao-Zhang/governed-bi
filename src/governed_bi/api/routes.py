"""Custom REST routes mounted by ``langgraph.json``'s ``http.app`` (ADR 0007 §7).

Route shapes follow ``docs/openapi.json``. Capabilities report what is actually built.
No ungated route needs a model — corpus browsing is model-free.
"""

from __future__ import annotations

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
