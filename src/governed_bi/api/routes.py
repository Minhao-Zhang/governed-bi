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
#: The same saver goes to the nested `create_agent` (`agent_checkpointer`), because that is
#: where `ask_user` interrupts from: two savers means the interrupt is written to one and
#: looked for in the other, which fails as a turn that hangs rather than as an error.
_GRAPH: Any = None


def _graph() -> Any:
    global _GRAPH
    if _GRAPH is None:
        from langgraph.checkpoint.memory import InMemorySaver

        from governed_bi.serve.graph import build_graph

        saver = InMemorySaver()
        _GRAPH = build_graph(agent_checkpointer=saver).compile(checkpointer=saver)
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
        # exactly when something can ask for one.
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
        return {"outcome": "crashed", "text": "no question", "failed_stage": "accept",
                "error_type": "ValueError", "refused_by": None, "record": {}, "answer_text": None}

    thread_id = str(body.get("session_id") or "") or uuid.uuid4().hex[:16]
    turn_index = 1 + sum(1 for h in body.get("history") or [] if (h or {}).get("role") == "user")
    turn = session.turn(question, turn_index=turn_index, thread_id=thread_id)
    config = session.configurable(question=question)
    # The thread goes on the **config**, because that is what LangGraph checkpoints on. An
    # earlier version put it only in the turn state and claimed in this docstring that a
    # conversation would resume; it could not. See `_graph()` for the other half.
    config["configurable"]["thread_id"] = thread_id
    out = _graph().invoke(turn, config)
    answer = dict(out.get("answer") or {})
    # The model's text lives in `messages`, not in `answer["text"]` — ADR 0007 §4: `text` is
    # *system* copy and is null on the answered path. A REST caller has no message channel to
    # read, so the one thing it cannot reconstruct is supplied here, under a different name so
    # the two are never confused for one field.
    answer["answer_text"] = _last_ai_text(out)
    return answer


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
