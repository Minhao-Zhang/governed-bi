"""Custom REST routes mounted by ``langgraph.json``'s ``http.app`` (ADR 0007 §7).

Route shapes follow ``docs/openapi.json``. Capabilities report what is actually built.
**Every route here is a read and none of them needs a model.** Serving a turn is the streamed
LangGraph Server path (``langgraph.json``'s ``graphs.serve`` → ``api/graph_app.make_graph``) and
nothing else.

``POST /chat`` and ``POST /chat/resume`` are **deleted** (2026-08-18, ADR 0014). They were a
second topology for the same job — no ``accept`` node, the whole of ``ServeState`` in and out, a
process-wide ``InMemorySaver`` of their own and a 32-thread LRU over it — and the second store is
what made them a bug rather than a fallback: the two transports never shared a thread, so
degrading to REST lost the conversation it was meant to rescue. Their append into the audit log
went with them, so ``record_node`` is the only writer of a turn: it returns the envelope onto
``ServeState.turns`` and the checkpointer persists it.

**The surface has a constructor** (2026-08-11). :func:`make_app` takes its two dependencies —
the session and the turn log — and returns an app over exactly those.
:func:`app_from_environment` is the adapter the process entry uses, and is the only thing here
that resolves anything from the environment; the module-level :data:`app` is that adapter's
output because ``langgraph.json`` names an attribute rather than a factory.

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
thread's SQL, the full turn records and an absolute path to the conversation store to anything
that can reach the port — and the platform's own ``/threads`` and ``/runs``, on the same port and
under the same absent credential, will spend model budget for it. Nothing this app serves will:
the chat pair that used to is deleted. The ``_cors_headers`` helper that made a 401 legible
to a browser went with the middleware — with no refusal to head by hand, every response now
passes back through the platform's ``CORSMiddleware`` normally. See ``api/auth.py``, which keeps
the state-write denials that are *not* authentication.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import FastAPI

from governed_bi.api.browse import DEFAULT_NODE_BUDGET, subgraph
from governed_bi.api.browse_routes import make_router
from governed_bi.api.curation_routes import make_curation_router
from governed_bi.api.drafts_routes import make_drafts_router
from governed_bi.api.feedback_routes import make_feedback_router
from governed_bi.api.trust_loop_routes import make_raised_router, make_trust_loop_metrics_router
from governed_bi.api.visibility import visible
from governed_bi.model.provider import reasoning_effort_of
from governed_bi.paths import REPO_ROOT
from governed_bi.register.assets import ASSET_REGISTER
from governed_bi.serve.runtime import bool_knob

__all__ = ["make_app", "app_from_environment", "app"]


# ── the seam ─────────────────────────────────────────────────────────────────


def make_app(session: Any, turn_log: Any) -> FastAPI:
    """An app over exactly these two dependencies. **The constructor.**

    ``session`` is a :class:`~governed_bi.serve.session.Session` (or anything with its read
    surface: ``assets_by_id``, ``structure``, ``connector``, ``agent_model``, ``knobs_resolved``,
    ``corpus_content_hash``, ``problems``). ``turn_log`` is a **reader** of served turns —
    anything exposing ``list_turns``, ``get_turn``, ``SUMMARY_FIELDS`` and ``TURN_LOG_DIR``;
    :class:`governed_bi.api.thread_turns.ThreadTurnLog` is the production one.

    **There is no ``graph``.** It was the third dependency and only the deleted chat pair ever
    called it, so keeping the parameter would advertise a transport this app does not have. A
    turn is served by the graph ``langgraph.json`` mounts, which the platform drives; this app
    never holds it.

    Both are required and neither is defaulted. A default would put the environment back in the
    constructor, which is the thing this exists to remove.
    """
    return _build_app(lambda: session, turn_log)


def app_from_environment() -> FastAPI:
    """The process entry's adapter: the same app, over dependencies resolved on first request.

    Lazy on purpose. ``langgraph.json`` points ``http.app`` at the module attribute below, so
    this runs at import — and ``session_from_environment`` builds a Postgres connector and seeds
    a corpus. Resolving it here would make importing this module require a database.
    """
    from governed_bi.api.graph_app import session_from_environment
    from governed_bi.api.thread_turns import ThreadTurnLog

    # `ThreadTurnLog`: the audit surface reads a turn's record out of thread state now that
    # `ServeState.turns` accumulates it, so there is no second store of the same thing. Its
    # header says why that is readable in-process.
    return _build_app(session_from_environment, ThreadTurnLog())


def _build_app(get_session: Callable[[], Any], turn_log: Any) -> FastAPI:
    """Assemble the app from a session thunk and a turn log.

    A thunk rather than a value, so :func:`make_app` can hand over a concrete object and
    :func:`app_from_environment` can defer. That is the one difference between the two adapters,
    and it lives here rather than in the routes.
    """
    app = FastAPI(title="governed-bi", version="2")

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

        ``thread_id`` narrows to one conversation, which is what a transcript needs. It used to be
        needed because the *store* was global — one time-ordered log of every thread — and it is
        still needed now the source is thread state, because a transcript asks for one thread and
        the reader would otherwise page through every other one to find it.
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
    # Split out of curation_routes.py to stay under the file-length cap (drafts_routes.py's own
    # docstring); same factory shape and deferred session, mounted last for the same reason.
    app.include_router(make_drafts_router(_DeferredSession(get_session)))
    # Task H's own ledger + inbox (feedback.jsonl), never merged into the clarification one --
    # see feedback_routes.py's module docstring. Same factory shape, deferred session, mounted
    # last for the same reason as the two routers above.
    app.include_router(make_feedback_router(_DeferredSession(get_session)))
    # Task B-1's read model: "given a thread, what did it raise, and what became of it" -- over
    # both ledgers above plus the turn log, which is why this factory takes `turn_log` too (see
    # its own docstring for why that is a new shape rather than the single-session pattern the
    # other three routers here use). Mounted last for the same reason as the three above.
    app.include_router(make_raised_router(_DeferredSession(get_session), turn_log))
    # Task C: "count whether the loop turns" -- the same file, same dependency set (both
    # ledgers, the corpus, the turn log) as the router just above, so it is mounted the same way
    # for the same reason. Mounted last for the same reason as every router above.
    app.include_router(make_trust_loop_metrics_router(_DeferredSession(get_session), turn_log))
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


def models_for(session: Any) -> dict[str, Any]:
    """The three model surfaces this run resolved, for the settings page.

    **Read from ``knobs_resolved``, not off the client objects.** That mapping is what every
    measurement row publishes, so a settings page built from it shows the identity a run is
    actually recorded under — and a disagreement between screen and artifact becomes
    impossible rather than merely unlikely. ``serve/runtime.py::model_id`` is where that
    identity is derived, and its own note records what a wrong derivation cost.

    ``embedding.id`` is **provider-qualified** (``bedrock:amazon.titan-embed-text-v2:0``) and is
    reported verbatim rather than split on ``:``. The qualifier is part of the cache-key
    identity (``retrieve.semantic.cache_key`` is ``model|dimensions|text``), and the id itself
    can contain a colon — Titan's ``…-v2:0`` does — so parsing it would corrupt the one field
    that keeps two gateways' vectors apart. ``provider`` is carried beside it instead.

    ``utility.effort`` is observed off the live client because no knob records it: the register
    declares ``llm_reasoning_effort`` for the agent surface only. Adding a second knob is not
    this function's call to make (``register/knobs.py`` is the one home for a knob), so the
    field is honest about being an observation and is ``None`` when there is no live model.
    """
    knobs = session.knobs_resolved
    # `getattr`, matching how this module reads `connector.dialect`: these projections are
    # documented as "a function of the session, testable without an app", and a hard attribute
    # read here would make every existing test fake grow a field to keep passing — which is a
    # test-maintenance tax for no assertion. A session without the handle reports no effort.
    utility = getattr(session, "utility_model", None)
    return {
        "agent": {
            "id": knobs.get("chat_model"),
            "provider": knobs.get("llm_provider"),
            "effort": knobs.get("llm_reasoning_effort"),
        },
        "utility": {
            "id": knobs.get("llm_utility_model"),
            "provider": knobs.get("llm_utility_provider"),
            "effort": reasoning_effort_of(utility) if utility is not None else None,
        },
        "embedding": {
            "id": knobs.get("embedding_model"),
            "provider": knobs.get("embedding_provider"),
            "dimensions": knobs.get("embedding_dimensions"),
        },
    }


def connection_for(session: Any) -> dict[str, Any]:
    """Which warehouse this engine is pointed at. **Credential-free by construction.**

    The redaction is the connector's (``datasource/postgres.py::endpoint``), not this
    function's — see that property for why it lives there. Here the only job is to be robust
    about *shape*: a partial session or a test double may have no ``endpoint``, or one that is
    not a mapping, and a settings page is not worth a 500. ``dialect`` is always present because
    every connector declares it.
    """
    out: dict[str, Any] = {"dialect": getattr(session.connector, "dialect", "postgres")}
    endpoint = getattr(session.connector, "endpoint", None)
    if isinstance(endpoint, Mapping):
        out.update({str(k): v for k, v in endpoint.items()})
    return out


def durable_checkpointer_configured() -> bool:
    """Whether this deployment mounts a durable checkpointer, read off ``langgraph.json``.

    **Derived, because the alternative is unobservable from here.** The platform injects the saver
    it loads from ``checkpointer.path`` into every graph it runs (``langgraph_api/graph.py``
    copies the compiled graph with the saver attached), and this custom app never holds that
    graph — there is no object here to ask. What *is* in the process is the file that decides it,
    and the module it names: if either stops existing the flag goes false without anyone editing
    this line, which is what ADR 0009 D4 asks of a capability flag.

    Both halves are checked. The field alone would report a checkpointer that fails to load; the
    file alone would report one nothing mounts. Neither says the saver is *open* — a live handle
    is what cannot be seen from here, and this is honest about being a configuration reading.
    """
    try:
        config = json.loads((REPO_ROOT / "langgraph.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    declared = str((config.get("checkpointer") or {}).get("path") or "")
    module, _, factory = declared.rpartition(":")
    return bool(module and factory) and (REPO_ROOT / module).is_file()


def served_graph_declared() -> bool:
    """Whether ``langgraph.json`` declares the streamed graph, and its module is on disk.

    The streaming transport is the platform's, not this app's, so there is no client object here
    to ask -- the same bind as :func:`durable_checkpointer_configured`, and the same answer: read
    the file that decides it and the module it names. Delete either and the flag goes false with
    nobody editing this line, which is what makes it an observation.

    It cannot see that the graph *imports*. A syntactically broken `serve` module would leave this
    true while the server failed to start -- at which point nothing answers `/capabilities` either,
    so the lie is unobservable.
    """
    try:
        config = json.loads((REPO_ROOT / "langgraph.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    declared = str((config.get("graphs") or {}).get("serve") or "")
    module, _, factory = declared.rpartition(":")
    return bool(module and factory) and (REPO_ROOT / module).is_file()


def capabilities_for(session: Any) -> dict[str, Any]:
    """``/capabilities``' body. Every field is an observation (ADR 0007 §7)."""
    #: Bound once so ``can_clarify`` cannot drift from ``can_stream``.
    #
    # Derived, not the literal `True` it was. It was left hardcoded because a `false` value would
    # have made the UI mount the REST fallback against a route that no longer exists -- and that
    # reason is spent: the fallback is deleted and `can_stream: false` now renders `<NoTransport/>`,
    # which explains itself. So the last capability flag that described an intention rather than an
    # observation is one too (ADR 0009 D4).
    can_stream = served_graph_declared()
    durable = durable_checkpointer_configured()
    from governed_bi.serve.runtime_overrides import overrides as _live_overrides

    live_knobs = {**session.knobs_resolved, **_live_overrides()}
    return {
        "environment": "local",
        "dialect": getattr(session.connector, "dialect", "postgres"),
        "can_edit": False,
        "edit_mode": "none",
        "can_stream": can_stream,
        "has_live_model": session.agent_model is not None,
        # Kept: the existing header chip reads it, and it is the agent surface's id. The
        # per-surface detail is under `models`, which is what the settings page renders.
        "model": session.knobs_resolved.get("chat_model"),
        "models": models_for(session),
        # Which warehouse this engine is pointed at, credential-free — the connector redacts
        # (`datasource/postgres.py::endpoint`). `getattr` for the same reason as `dialect`
        # above: these projections must stay callable with a partial session.
        "connection": connection_for(session),
        "can_scope": True,
        "can_search": False,
        # `can_stream and …` is kept, unchanged, from ADR 0009 D12 — the flag mounts the
        # interrupt prompt, so it must not be true on a client that cannot show one. The
        # expression is now *trivially* satisfied on its first term rather than load-bearing:
        # the streamed transport is the only one there is, and it has the clarification pair.
        # Still the right shape, because the term that can go false is the model.
        "can_clarify": can_stream and session.agent_model is not None,
        # Both derived (ADR 0009 D4: a flag is flipped by building the thing, not by editing the
        # line). They were hardcoded `False` and described `POST /chat`'s process-local
        # `InMemorySaver`, which is deleted; the served path checkpoints to SQLite through
        # `langgraph.json`'s `checkpointer.path` → `serve/checkpointer.py` (ADR 0014).
        "checkpoint_durable": durable,
        # One observation, not two: an `ask_user` interrupt *is* checkpoint state, and the
        # resume reads it back out of the same store through the platform's `Command(resume=…)`.
        # So this cannot be true while `checkpoint_durable` is false, and it must not be reported
        # as a separate belief. What ADR 0014 verified is thread state surviving a hard kill and
        # a restart; a clarification answered *after* one has not been watched end to end.
        "hitl_survives_process_restart": durable,
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
    """``/audit/turns``' body.

    ``meta.log_dir`` is **where these turns come from**, and since ADR 0014 that is the
    conversation checkpoint database rather than a directory of JSONL files — the audit surface
    reads thread state, and there is no second store to name. The *key* keeps the old spelling
    deliberately: the client's ``auditTurnsSchema`` requires ``log_dir`` and the audit footer
    renders it, so renaming the field would break ``npm run check:api`` to relabel one caption.
    The value is read off the seam (``turn_log.TURN_LOG_DIR``) and not from
    ``serve/checkpointer.py``, because a route that names the production store directly would
    report it for an app built over a fake.
    """
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


#: What ``langgraph.json``'s ``http.app`` points at. An attribute, not a factory — the platform
#: imports the name — so the environment adapter is what builds it.
app = app_from_environment()
