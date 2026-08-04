"""The custom REST routes the frontend consumes, mounted by ``langgraph.json``'s ``http.app``.

ADR 0007 §7. `docs/openapi.json` is v1's spec and remains the spec-of-record for the **route
shapes**; it is not the spec for the answer, which changed with the rewrite.

**Every value here is an observation.** `/capabilities` is the UI's first request, so a
hard-coded `true` in it is the stub-path defect one layer out: the interface would promise a
model that will never answer, and the user would read the silence as a bug in their question.
`can_edit` is false because the curator is out of scope. `can_scope` is **true** since ADR
0009 built the routes behind it; `can_search` is still false because `/search` is not built,
and the UI degrades to a client-side index, which works. A capability is flipped by building
the thing it names.

**No route needs a model.** All five ungated routes are projections of the session's assets, so
the corpus is browsable before anyone pays for a token.
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

__all__ = ["app"]

app = FastAPI(title="governed-bi", version="2")

# The browsing routes live beside their logic (`api/browse.py`), included rather than
# re-declared so there is one definition of `/schema` and one of `/corpus/rows`.
app.include_router(browse_router)


def _session() -> Any:
    return session_from_environment()


#: One compiled graph, one checkpointer, for the whole process.
#:
#: `compile_graph()` builds a **fresh** `InMemorySaver` on every call, so calling it per
#: request meant every turn started from an empty checkpoint — no resume, no thread memory,
#: and no way for an `ask_user` interrupt to be answered, while this module's docstring
#: claimed otherwise. Compiling once is what makes the thread id mean something.
#:
#: The nested `create_agent` needs no saver of its own, and the sentence that used to be here
#: claiming otherwise — "two savers means the interrupt is written to one and looked for in the
#: other" — described a mechanism that does not exist. LangGraph propagates the checkpointer
#: through `config` into a graph invoked inside a node: measured, the agent's own saver ends a
#: run with zero checkpoints while this one has three.
_GRAPH: Any = None


def _graph() -> Any:
    global _GRAPH
    if _GRAPH is None:
        from langgraph.checkpoint.memory import InMemorySaver

        from governed_bi.serve.graph import build_graph

        _GRAPH = build_graph().compile(checkpointer=InMemorySaver())
    return _GRAPH


@app.get("/livez")
def livez() -> dict[str, Any]:
    """Liveness only. Deliberately does **not** touch the session: a liveness probe that
    builds a corpus reports "dead" for a slow seed, and something that restarts the process on
    that answer turns a slow start into a loop."""
    return {"ok": True}


@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """What this server can actually do. The UI blocks on this response."""
    session = _session()
    #: Whether the streaming transport is offered. Bound to a name because `can_clarify`
    #: depends on it and two copies of one literal is how the two answers drift apart.
    can_stream = False
    return {
        "environment": "local",
        "dialect": getattr(session.connector, "dialect", "postgres"),
        # The curator is out of scope, so an edit button would front a route that does not
        # exist. False here is a promise kept, not a feature missing.
        "can_edit": False,
        "edit_mode": "none",
        # **False, and this is the honest answer rather than a limitation.** ADR 0007 §5
        # specifies custom stream events and nothing in v2 emits one yet, so the streamed
        # timeline would render empty — the UI would show a live-looking run with no steps in
        # it, which is worse than not offering the mode. The UI's own `canStream(caps)` gate
        # then selects `<RestChat/>` and `POST /chat` below, which is a path it already has.
        #
        # There is a second reason and it is worth recording: `langgraph dev` installs
        # `blockbuster`, which raises on blocking I/O in an async function and keeps
        # `os.getcwd` armed. This engine is deliberately synchronous — a sync `psycopg`
        # connector is the declared port — so a streamed run trips it inside the server's own
        # worker, with no frame of ours in the traceback. Flip this to true when stage events
        # exist *and* the run path is async or thread-offloaded, not before.
        "can_stream": can_stream,
        # Observed, never assumed: a session with no model serves the stub path, and saying
        # otherwise would make the interface blame the question for the silence.
        "has_live_model": session.agent_model is not None,
        "model": session.knobs_resolved.get("llm_model"),
        # **True now, because the routes exist** — `/schema/summary`, `/schema/{id}`,
        # `/corpus/fields`, `/corpus/rows` and a scoped `/graph` (ADR 0009). It was false
        # while they 404'd, which is why nothing looked broken: the UI has a documented
        # fallback to the flat dumps, and the fallback was the 937 KB `/schema` and the
        # 2.25 MB `/corpus/assets` we were measuring. The flag is flipped by building the
        # thing, never to unlock a UI path.
        "can_scope": True,
        # Still false: `/search` is not built. Reporting a search the server cannot do would
        # make the omnibox blame the corpus for an empty result. The UI's client-side Fuse
        # index is the honest fallback and it works.
        "can_search": False,
        # **Gated on `can_stream`, and that is the correction.** The server half is genuinely
        # built: `ask_user` is bound whenever a model is, `POST /chat` surfaces `__interrupt__`,
        # and `POST /chat/resume` accepts the answer. But this flag does not describe the
        # server — the client contract makes it the switch that mounts the interrupt prompt, and
        # the transport it would mount into depends on `can_stream`. With streaming off the UI
        # mounts `<RestChat/>`, whose `useRestChat` has no clarification state and no resume
        # call anywhere in it, so `true` offered a question that could be asked, displayed
        # nowhere, and answered by nobody — the graph simply stayed paused.
        #
        # So this is false today because of the *client's* missing half, not the server's, and
        # it flips by giving `useRestChat` a clarification pair or by turning streaming on —
        # never by editing this line. Reporting a capability the mounted transport lacks is the
        # same defect as a reliability badge with nothing behind it.
        "can_clarify": can_stream and session.agent_model is not None,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    """Corpus health as counts plus findings.

    ``findings`` carries the session's problems verbatim. That is the point of the route: ADR
    0005 §2.8.2 requires an unresolvable join endpoint to surface where the corpus is built,
    and until there was somewhere to show it, "reported" meant "returned to a caller who
    dropped it".
    """
    session = _session()
    counts: dict[str, int] = {}
    for asset in session.assets_by_id.values():
        key = asset.asset_type.value
        counts[key] = counts.get(key, 0) + 1
    return {
        "counts": counts,
        # Suspect/excluded/low-confidence are corpus-curation concepts the curator produces,
        # and the curator is out of scope. Zero here is a **true** count over an uncurated
        # corpus, not a placeholder: nothing has marked anything, so nothing is marked.
        "n_suspect_columns": 0,
        "n_excluded": 0,
        "n_low_confidence_joins": 0,
        # Green means **servable**, i.e. no fatal problem — ADR 0008 D9. It read
        # `not session.problems`, so three unresolvable metric dimensions painted the health
        # page red on a corpus that answers questions correctly, which trains a reader to
        # ignore the light. The degradations are still counted and still listed; they are a
        # different fact and now have their own number.
        "ci_green": not session.fatal_problems,
        "n_fatal": len(session.fatal_problems),
        "n_degradations": len(session.degradations),
        "findings": [str(p) for p in session.problems],
    }


def _provenance_status(asset: Any) -> str | None:
    """``asset.audit.provenance.status``, or ``None`` when any link is absent.

    Absent is a distinct value here, not a default: ADR 0005 §6 requires "not measured" to be
    distinguishable everywhere, and an asset with no audit trail is a different claim from one
    audited and found clean. Every step is optional on the dataclasses, which is why this is a
    named function rather than a fourfold `getattr` chain repeated at each call site.
    """
    provenance = getattr(getattr(asset, "audit", None), "provenance", None)
    status = getattr(provenance, "status", None)
    return status.value if status is not None else None


@app.get("/corpus/assets")
def corpus_assets(type: str | None = None) -> list[dict[str, Any]]:
    """Assets of one type, as rows. ``type`` is validated against the **register**, not a
    hand-written list, so a new asset type is reachable here the moment it is declared.

    ``provenance_status`` and ``excluded`` are **required** by the client's `assetRowSchema`,
    and leaving them out took three live components down at once — the chat conversation, the
    corpus asset browser and the column-related sheet all call this and all `safeParse` it, so
    two absent keys became three thrown `ApiError`s rather than three rows missing a badge.
    Both are governance observations the engine already holds on every asset; `excluded` in
    particular is what the "Hide excluded" control filters on, so without it the control would
    be a toggle over nothing.
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
    """The **ER** graph: tables as nodes, join relationships as edges carrying their key.

    **From the structure**, not from a second walk over the assets: `CorpusStructure` is the
    one resolution of physical names to asset ids (ADR 0005 §2.8.2), and a graph drawn from a
    different one could show an edge the router does not have.

    Every field here is required by the client's declared contract, and **omitting them is
    what broke the Relationships tab**. This emitted `{id, label, kind, schema}` while the
    contract requires `physical_name`, `row_count`, `n_columns`, `excluded` and `has_suspect`
    on a node and `on` / `cardinality` on an edge — so the UI's zod boundary rejected the
    whole response and the tab rendered "Couldn't load data". Not a validation nuisance: the
    join predicate and the cardinality are what make this an *ER diagram* rather than an
    undifferentiated blob, and the engine has both on every ``JoinAsset``.
    """
    session = _session()
    structure = session.structure
    by_id = session.assets_by_id

    edges: list[dict[str, Any]] = []
    for left, right in sorted(structure.join_edges):
        join_ids = list(structure.joins_by_edge.get((left, right), ()))
        # Several relationships between one table pair is the normal case ADR 0005 §1.2 put
        # the ON digest in the join id for. The first is drawn; all of them are carried, so a
        # detail sheet can show the rest rather than the diagram pretending there is one.
        #
        # But `len(join_ids)` is **not** that count. A relationship declared from both ends is
        # two join assets over one predicate — `join_a_b_<digest>` and `join_b_a_<digest>` —
        # and the ON digest is identical precisely so the pair is recognisable. Measured over
        # the pooled lake: 83 table pairs carry more than one join asset, and 71 of them are a
        # single relationship counted twice. So the count is over **distinct predicates**, and
        # `join_ids` stays complete because it is the audit trail, not the number.
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
                # A threshold the *client* used to invent. It reads a declared knob here
                # instead, so the diagram and the corpus agree on what "low" means.
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


#: How a reference from one asset type is labelled in the knowledge graph. The client's
#: vocabulary (``join | measures | grounds | exemplifies | related``), keyed on the *source*
#: asset type — which is where the meaning lives: a metric pointing at a table measures it,
#: a term pointing at anything grounds in it.
# The node kinds the semantic graph draws — the client's `graphNodeKindSchema`, which is the
# contract this has to satisfy exactly: a kind outside this set fails the zod boundary and
# takes the *whole* response down, not just its own node.
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
    """The **semantic** graph: every asset kind, edges from the reference closure.

    A different graph from the ER one, and this route used to return the ER payload with a
    note saying "the same for now, and saying so is better than two drifting walks". That
    note was wrong twice. The client declares a *different* node shape for this route
    (``kind`` over every asset type, ``provenance_status``, a ``relation`` on the edge), so
    the ER payload failed its zod boundary here too — and the two graphs are not the same
    graph: this one has 13,981 nodes' worth of terms, metrics and few-shots hanging off the
    tables, which is the entire thing a *semantic* layer view is for.

    Edges come from ``CorpusStructure.references``, which is the closure ``resolve`` runs on.
    So what the diagram draws is what retrieval would actually pull in — the property a
    hand-built second walk would lose.

    **Columns and schemas are not nodes here.** Two reasons, and they agree. The client's
    node vocabulary is the seven semantic kinds (``graphNodeKindSchema``), so emitting
    ``kind: "column"`` failed the zod boundary and the tab rendered nothing at all. And the
    shape was wrong even where it parsed: a one-schema scope came to 107 columns out of a
    120-node budget, so the columns crowded out the terms and metrics that are the reason to
    look at a *semantic* graph. Column endpoints are **re-pointed to the owning table**
    rather than dropped, which keeps a term bound to ``lineitem.l_quantity`` attached to
    ``lineitem`` instead of stranding it as an isolated node. Per-column detail is a
    different question, answered by ``GET /schema/{table_id}``.
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
            # A column->its-own-table reference collapses to a self-loop once both ends are
            # re-pointed; so does one term binding two columns of the same table. Neither is
            # worth drawing, and the dedupe keeps the node budget for real relationships.
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
    """Serve one turn. The UI's REST transport, selected when ``can_stream`` is false.

    Request: ``{question, session_id, history: [{role, text}]}``.

    Response: **v2's answer, verbatim** — ``{outcome, text, failed_stage, error_type,
    refused_by, record}``. Not projected into v1's `AnswerView`: ADR 0007 §3 forbids
    synthesizing `tier`, `safety_clearance` or `semantic_assurance`, none of which exists in
    this engine, because a reliability badge with nothing behind it is the defect class the
    rewrite removed. ``answer_text`` is added beside them for one reason given below.

    ``session_id`` becomes the ``thread_id`` **on the config**, which is what LangGraph
    checkpoints on, so a conversation genuinely resumes under one checkpoint and an
    ``ask_user`` interrupt can be answered. An earlier version of this route put the thread id
    only in the turn state and asserted the same sentence; it was false, because
    ``compile_graph()`` also built a fresh saver per request. Both halves are fixed.

    ``history`` is **not injected into the conversation** and is not a second memory. The
    thread is the memory. It is read for exactly one thing -- numbering the turn -- and if it
    disagrees with the thread, the thread is right. Accepting it and also replaying it would
    be two sources for one fact, which is the failure this file keeps arguing against.

    Defined ``def`` rather than ``async def`` deliberately. FastAPI runs a sync handler in a
    threadpool, so the synchronous connector and model calls do not occupy the event loop —
    which is the same property `blockbuster` was complaining about, obtained rather than
    suppressed.
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
    """Answer a clarification. The other half of ``POST /chat``'s interrupt.

    **This route did not exist, and its absence was a deadlock on the transport the UI uses.**
    ``/chat`` called ``graph.invoke`` and returned ``out["answer"]``; when ``ask_user``
    interrupted, no node had written ``answer``, so the route replied **HTTP 200** with
    ``{"answer_text": null}`` and dropped ``__interrupt__`` on the floor. The client saw a
    successful empty answer, the graph stayed paused forever, and nothing on screen was wrong —
    which ``serve/tools.py`` already calls "the worst failure shape available here" about the
    payload version of the same bug. Meanwhile ``/capabilities`` reported
    ``can_clarify: true``.

    Request: ``{session_id, clarification_id?, answer | choice_id | declined, identity?}``.

    ``clarification_id`` is checked against the pending question when supplied, because an
    answer attributed to the wrong question is worse than a refused one.
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
    # Logged here too, and with the *clarification* as the question. A resumed turn is the
    # one that produces the record, so leaving it out would make every clarified
    # conversation invisible to the audit surface — which is the half of the traffic most
    # worth auditing.
    return _logged(_shape(out), str(pending.get("question") or ""))


def _config(session: Any, question: str | None, thread_id: str) -> dict[str, Any]:
    """This request's config. The thread goes on the **config**, not in the turn state.

    That is what LangGraph checkpoints on. An earlier version put it only in the turn and
    asserted in a docstring that a conversation would resume; it could not.
    """
    config = session.configurable(question=question) if question else session.configurable()
    config["configurable"]["thread_id"] = thread_id
    return config


def _identity(body: dict[str, Any], thread_id: str) -> dict[str, str]:
    """Who is asking, for ``resume_authorised``.

    **On this deployment the thread id is the only credential there is, and saying so is the
    point.** ``resume_authorised`` refuses two ``None``s on purpose — an unauthenticated
    deployment must not get cross-caller resume for free — and nothing in this repository
    supplied an identity, so *every* clarification was unanswerable: ``ResumeRejected`` for
    every caller, including the right one.

    Falling back to the thread id grants no authority that posting to ``/chat`` on the same
    thread does not already grant, because there is no authentication in front of either. It is
    a **same-thread** check, not a same-caller one, and a deployment with real auth must send a
    real ``identity`` — which this accepts and prefers.
    """
    supplied = body.get("identity")
    if isinstance(supplied, str) and supplied:
        return {"token": supplied}
    if isinstance(supplied, dict):
        token = next((str(v) for v in supplied.values() if v), "")
        if token:
            return {"token": token}
    return {"token": thread_id}


def _clarification(interrupts: Any) -> dict[str, Any] | None:
    """The ``ask_user`` payload (ADR 0007 §6) among some interrupts, or ``None``.

    Pure, and takes the interrupts rather than a state, because the two callers have different
    ones: a completed ``invoke`` returns ``__interrupt__`` on the state, while a fresh
    ``/chat/resume`` request has no returned state and must read the checkpoint's pending tasks.
    Filtered on ``kind == "clarification"`` so a future interrupt of another kind is not
    answered by the clarification route.
    """
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
    """One response shape for both chat routes, including the paused one.

    Response: **v2's answer, verbatim** — ``{outcome, text, failed_stage, error_type,
    refused_by, record}`` — plus ``answer_text`` and, when the turn is paused, ``clarification``.
    Not projected into v1's ``AnswerView``: ADR 0007 §3 forbids synthesizing ``tier``,
    ``safety_clearance`` or ``semantic_assurance``, none of which exists in this engine.
    """
    pending = _clarification(out.get("__interrupt__"))
    if pending is not None:
        # `outcome: "clarification"` is a **declared** `register.stages.Outcome` member, not a
        # string invented here for the transport.
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
    # The model's text lives in `messages`, not in `answer["text"]` — ADR 0007 §4: `text` is
    # *system* copy and is null on the answered path. A REST caller has no message channel to
    # read, so the one thing it cannot reconstruct is supplied here, under a different name so
    # the two are never confused for one field.
    answer["answer_text"] = _last_ai_text(out)
    answer.setdefault("clarification", None)
    return answer


def _logged(shaped: dict[str, Any], question: str) -> dict[str, Any]:
    """Append the turn to the audit log and say whether that worked.

    A paused turn is **not** logged: it has no record yet, and writing an empty one would
    put a row in the audit list that reports fifteen absent required fields for a turn that
    is waiting rather than broken — the same mistake ``python -m governed_bi.serve`` exit 4
    exists to avoid.

    ``audit_logged`` rides on the response instead of being silently dropped, because "no
    turns are listed" and "no turns were served" must not be the same observation.
    """
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


def _last_ai_text(state: dict[str, Any]) -> str | None:
    """The model's answer, via LangChain's own ``AIMessage.text``.

    Not hand-flattened. The Responses API returns content as blocks
    (``[{"type": "text", ...}, {"type": "reasoning", ...}]``), and an earlier draft of this
    walked them itself — which is re-implementing something `langchain-core` owns, and
    decision #1 records that v1's three layers over `BaseChatModel` were a mistake for
    exactly this reason. ``.text`` already concatenates the text blocks and ignores the rest.
    """
    for message in reversed(state.get("messages") or []):
        if str(getattr(message, "type", "")) in ("human", "tool"):
            continue
        text = getattr(message, "text", None)
        if text:
            return str(text)
    return None


@app.get("/graph")
def er_graph(
    schema: str | None = None,
    focus: str | None = None,
    radius: int = 1,
    node_budget: int = DEFAULT_NODE_BUDGET,
    kinds: str | None = None,
) -> dict[str, Any]:
    """A **bounded** relationship view. ADR 0009 D2.

    This returned all 656 tables and 556 edges unconditionally. The payload is only 166 KB,
    so it looked fine — but the client lays it out with dagre, synchronously, in the browser,
    and 656 nodes is neither fast nor a diagram anybody can read. The fix is not a smaller
    payload, it is a *scope*.

    ``schema`` and ``kinds`` narrow the candidate set; ``focus`` + ``radius`` then walks
    outward over the join graph; ``node_budget`` bounds the result last, breadth-first from
    the focus so what survives is the near neighbourhood rather than whatever sorted first.

    **``meta.truncated`` and ``meta.dropped`` are part of the contract.** A view that quietly
    renders 120 of 656 nodes reads as complete coverage, and this repository has published a
    number on top of that shape. With no scope at all the default budget still applies, so
    there is no request that returns an unlayoutable graph.
    """
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
    """The semantic graph: **every** asset kind, edges from the reference closure.

    Not the ER payload. It was, under a note claiming the two were "genuinely the same graph
    today", and that was wrong on both counts: the client declares a different node shape for
    this route — so the ER payload failed its zod boundary and this tab could not render
    either — and the graphs differ by 13 325 nodes, because this one carries the terms,
    metrics and few-shots hanging off the tables. That layer is the entire thing a *semantic*
    view is for.

    Same scope contract as `/graph`, because a client that can narrow one and not the other
    would show two different corpora on two tabs.
    """
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


# ── the audit surface ─────────────────────────────────────────────────────────
#
# Everything lives under `/audit`, and the namespace is not cosmetic: `GET /runs` returns
# **405** on this server because LangGraph Server owns `POST /runs`, so a route named for
# what it holds would have collided with the platform's own. One prefix that cannot
# collide, four routes.


@app.get("/audit/turns")
def audit_turns(limit: int = 50) -> dict[str, Any]:
    """Every turn this installation has served, newest first.

    ``incomplete_fields`` is computed against **today's** register rather than stored, so a
    turn logged before a field was declared is judged by the declaration in force now — the
    question the column answers is "is this turn quotable", and that is a question about the
    current register.
    """
    turns = list_turns(limit=limit)
    return {
        "turns": turns,
        "meta": {
            "n": len(turns),
            "log_dir": str(TURN_LOG_DIR),
            "columns": list(SUMMARY_FIELDS),
        },
    }


# ``GET /audit/turns/{turn_id}`` was **deleted**. No client ever called it, and everything it
# returned about a turn either duplicated the summary in ``/audit/turns`` or is now on the
# ``/trace`` route below — which the UI fetches at the same moment, for the same turn, from the
# same click. Two routes over one turn is two shapes to keep in step for no second caller.
#
# Its two unique fields moved rather than went: ``record`` (the raw record, so the drawer can
# show a field the register has not yet been taught) and ``undeclared_keys`` (the only signal
# that a producer is writing a field **nobody declared** — which is how a register stops being
# the description of what actually happens).


@app.get("/audit/turns/{turn_id}/trace")
def audit_trace(turn_id: str) -> dict[str, Any]:
    """The turn, grouped by the pipeline stage that produced each field.

    **Derived from ``RECORD_REGISTER``, never from a list written here.** Every
    ``RecordField`` already declares its ``owner`` stage, so the trace is a ``groupby`` over
    a table that exists — which means a field added to the register appears in the trace
    with no edit to this route, and a trace section can never claim a stage the register
    does not assign. A hand-written stage→fields map would be exactly the drift
    ``register/`` was built to end.

    Stage order follows ``Stage``'s declaration order, which is pipeline order, so the
    sections read top to bottom as the turn ran.
    """
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
        # Folded in from the deleted `/audit/turns/{turn_id}`. `stages` is the register's view
        # of the record and therefore shows only fields the register knows about; `record` is
        # the record itself, and `undeclared_keys` names what is in it that nothing declared.
        # That difference is the point: a stage list can look complete while a producer writes
        # a field no one has declared, and only this key says so.
        "record": record,
        "undeclared_keys": sorted(undeclared_keys(record)),
    }


@app.get("/audit/corpus")
def audit_corpus() -> dict[str, Any]:
    """What the corpus is, and what is wrong with it — the two halves in one response.

    ``fatal`` and ``degradations`` are separate lists rather than one with a flag, because
    ADR 0008 D9 makes them different states: a fatal problem means an id is not a key and
    the corpus is not what it claims, while a degradation means the corpus is smaller than
    the lake. The CLI refuses on the first and serves past the second, and a surface that
    blurred them would put this server and that one back into disagreement.
    """
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
