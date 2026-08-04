"""The custom REST routes the frontend consumes, mounted by ``langgraph.json``'s ``http.app``.

ADR 0007 §7. `docs/openapi.json` is v1's spec and remains the spec-of-record for the **route
shapes**; it is not the spec for the answer, which changed with the rewrite.

**Every value here is an observation.** `/capabilities` is the UI's first request, so a
hard-coded `true` in it is the stub-path defect one layer out: the interface would promise a
model that will never answer, and the user would read the silence as a bug in their question.
`can_edit` is false because the curator is out of scope; `can_scope` and `can_search` are false
because those four routes are not built, and the UI degrades to the flat `/schema` dump and a
client-side index. Reporting false is the cheapest honest path to a working page.

**No route needs a model.** All five ungated routes are projections of the session's assets, so
the corpus is browsable before anyone pays for a token.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI

from governed_bi.api.graph_app import session_from_environment
from governed_bi.register.assets import ASSET_REGISTER

__all__ = ["app"]

app = FastAPI(title="governed-bi", version="2")


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
        "can_stream": False,
        # Observed, never assumed: a session with no model serves the stub path, and saying
        # otherwise would make the interface blame the question for the silence.
        "has_live_model": session.agent_model is not None,
        "model": session.knobs_resolved.get("llm_model"),
        # The four scope/search routes are not built. The UI falls back to the flat /schema
        # dump plus a client-side index, which works.
        "can_scope": False,
        "can_search": False,
        # The `ask_user` tool is bound whenever a model is, so a clarification is reachable
        # exactly when something can ask for one — **and now answerable**, which is the half
        # this field was previously lying about. `POST /chat` dropped `__interrupt__` and
        # replied 200 with a null answer while the graph stayed paused, and `resume_clarification`
        # refused every caller because no turn carried an `identity`. Reporting `true` over that
        # was worse than reporting `false`: it made the interface offer a question it could not
        # accept an answer to.
        "can_clarify": session.agent_model is not None,
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
        "ci_green": not session.problems,
        "findings": [str(p) for p in session.problems],
    }


@app.get("/schema")
def schema(schema: str | None = None) -> list[dict[str, Any]]:
    """Every table as the UI's `TableView`, with its columns."""
    session = _session()
    tables = [
        a for a in session.assets_by_id.values()
        if a.asset_type.value == "table" and (schema is None or getattr(a, "schema", None) == schema)
    ]
    out: list[dict[str, Any]] = []
    for table in sorted(tables, key=lambda a: a.id):
        columns = [
            {
                "id": c.id,
                "name": getattr(c, "physical_name", c.id.rsplit(".", 1)[-1]),
                "summary": c.summary,
                "type": getattr(c, "data_type", None),
            }
            for c in session.assets_by_id.values()
            if c.asset_type.value == "column" and c.id.startswith(f"{table.id}.")
        ]
        out.append({
            "id": table.id,
            "name": getattr(table, "physical_name", table.id),
            "schema": getattr(table, "schema", None),
            "summary": table.summary,
            "columns": sorted(columns, key=lambda c: c["id"]),
        })
    return out


@app.get("/corpus/assets")
def corpus_assets(type: str | None = None) -> list[dict[str, Any]]:
    """Assets of one type, as rows. ``type`` is validated against the **register**, not a
    hand-written list, so a new asset type is reachable here the moment it is declared."""
    session = _session()
    known = {t.value for t in ASSET_REGISTER}
    if type is not None and type not in known:
        return []
    return [
        {"id": a.id, "asset_type": a.asset_type.value, "summary": a.summary,
         "schema": getattr(a, "schema", None)}
        for a in sorted(session.assets_by_id.values(), key=lambda a: a.id)
        if type is None or a.asset_type.value == type
    ]


def _graph_payload() -> dict[str, Any]:
    """Tables as nodes, join edges as edges. **From the structure**, not from a second walk
    over the assets: `CorpusStructure` is the one resolution of physical names to asset ids
    (ADR 0005 §2.8.2), and a graph drawn from a different one could show an edge the router
    does not have."""
    session = _session()
    edges = [
        {"source": left, "target": right, "kind": "join",
         "join_ids": list(session.structure.joins_by_edge.get((left, right), ()))}
        for left, right in sorted(session.structure.join_edges)
    ]
    nodes = [
        {"id": a.id, "label": getattr(a, "physical_name", a.id), "kind": a.asset_type.value,
         "schema": session.structure.schema_tags.get(a.id)}
        for a in sorted(session.assets_by_id.values(), key=lambda a: a.id)
        if a.asset_type.value == "table"
    ]
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
    return _shape(_graph().invoke(turn, config))


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
            f"clarification_id {wanted!r} does not match the pending question "
            f"{pending.get('clarification_id')!r}"
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
        return _error(
            "resume identity mismatch: the caller answering is not the caller that was asked"
        )
    return _shape(out)


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
    return _clarification(
        [i for task in tasks for i in (getattr(task, "interrupts", ()) or ())]
    )


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
        return {"outcome": "clarification", "text": pending.get("question"),
                "failed_stage": None, "error_type": None, "refused_by": None,
                "record": {}, "answer_text": None, "clarification": pending}
    answer = dict(out.get("answer") or {})
    # The model's text lives in `messages`, not in `answer["text"]` — ADR 0007 §4: `text` is
    # *system* copy and is null on the answered path. A REST caller has no message channel to
    # read, so the one thing it cannot reconstruct is supplied here, under a different name so
    # the two are never confused for one field.
    answer["answer_text"] = _last_ai_text(out)
    answer.setdefault("clarification", None)
    return answer


def _error(detail: str) -> dict[str, Any]:
    """A refusal a client can read, in the same shape as every other reply."""
    return {"outcome": "crashed", "text": detail, "failed_stage": "resume",
            "error_type": "ValueError", "refused_by": None, "record": {},
            "answer_text": None, "clarification": None}


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
def er_graph() -> dict[str, Any]:
    return _graph_payload()


@app.get("/knowledge-graph")
def knowledge_graph() -> dict[str, Any]:
    """Same payload as `/graph` for now, and saying so is better than two drifting walks.

    v1 distinguished an ER graph from a knowledge graph by the note and term assets layered
    over it, and this corpus is uncurated, so the two are genuinely the same graph today. When
    notes exist, this is where they are added — and the difference will be a real one rather
    than a name.
    """
    return _graph_payload()
