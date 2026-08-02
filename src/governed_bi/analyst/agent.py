"""Governed agentic serve core + outer deterministic rails (ADR 0002).

Inner loop: ``create_agent`` + ``GovernanceMiddleware`` + governed tools.
Outer loop: thin LangGraph ``StateGraph`` — refuse-gate, agent_core,
finalize / refuse. Agent-internal ``messages`` / ``licensed`` / ``ledger`` stay
node-local and never merge into the chat transcript (ADR 0001 / gotcha G2).
Deployment deps (corpus, gateway, graph, allowlist) are closures — not state
channels — so a future checkpointer stays thin.
"""

from __future__ import annotations

import hashlib
import re
import time
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .. import prompts
from ..corpus.schemas import TableAsset
from ..gateway import column_allowlist
from ..graph import build_graph, detect_missing_join_path, plan_joins
from ..retrieval import (
    SCHEMA_PICK_MAX_TABLES,
    RetrievalIndexCache,
    embed_schema_documents,
    expand_schemas_via_curated_joins,
    filter_corpus_for_retrieval,
    pick_schema,
    retrieve,
    shortlist_schemas,
)
from ..stages import Stage
from .answer import refusal
from .clarify import new_clarification_id, parse_response
from .context import assemble_context
from .governance import (
    _ESCALATION_GUARDRAIL,
    _ESCALATION_MODEL_ERROR,
    _ESCALATION_NO_COVERAGE,
    _LEDGER_STATUS,
    GovEventStream,
    StageRecorder,
    _finalize_success,
    _finish_unsuccessful,
    _licensed_table_ids,
    _match_negative_example,
    missing_edge_refusal,
    narrate_answer,
)
from .middleware import (
    AGENT_RECURSION_LIMIT,
    GovernanceHardStop,
    GovernanceMiddleware,
    licensed_physical_names,
    result_from_ledger,
)
from .run_log import FinalizeCtx, amend_run_tokens, finalize_and_log, new_run_id
from .sqlgen import GeneratedSql, _tables_used
from .tools import make_tools

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from ..llm import Embedder
    from ..memory import WorkingMemory
    from .answer import Answer
    from .narrate import AnswerNarrator

#: The default agent-core system prompt. Derived from the registry rather than
#: held here, so ``agent_core@v1`` and this constant cannot drift apart — a run
#: stamping ``agent_core=v1`` must have sent the text the registry hashed.
SYSTEM_PROMPT = prompts.get("agent_core").text

_ESCALATION_CLARIFY_DECLINED = (
    "I needed one clarification to answer this safely, but didn't receive an "
    "answer, so I stopped rather than guess. Re-ask with the detail and I'll continue."
)


def _assemble_note_item(corpus: "Corpus", nid: str) -> dict[str, Any]:
    """One note entry for the ``assemble`` stream ``items.notes`` list."""
    item: dict[str, Any] = {"id": nid}
    asset = corpus.by_id(nid)
    force = getattr(asset, "normative_force", None) if asset is not None else None
    if force is not None:
        item["normative_force"] = force
    return item


def _schema_table_totals(
    corpus: "Corpus", schemas: list[str] | set[str]
) -> dict[str, int]:
    """Analyst-visible table counts per schema, without ``for_analyst()`` deep-copies.

    Matches the table population ``_schema_pick_summary`` shows (non-excluded
    ``TableAsset`` rows). Used only for the ``schema_route.truncated`` wire field —
    calling ``_analyst_tables`` per candidate would re-copy the corpus every turn
    and break the index-cache copy budget.
    """
    wanted = set(schemas)
    counts = {s: 0 for s in wanted}
    for a in corpus.assets:
        if not isinstance(a, TableAsset) or a.schema not in wanted:
            continue
        gov = getattr(a, "governance", None)
        if gov is not None and getattr(gov, "excluded", False):
            continue
        counts[a.schema] += 1
    return counts


class ClarificationPending:
    """Returned by :func:`answer_question_agent` instead of an ``Answer`` when the
    inner agent paused on ``ask_user``. The caller (the chat-graph node) turns
    ``request`` into a client-visible ``interrupt`` and calls back with the answer
    (contract: docs/analyst.md, serve-time clarification)."""

    __slots__ = ("request",)

    def __init__(self, request: dict) -> None:
        self.request = request


def _extract_clarifications(messages: list | None) -> list[dict]:
    """Recover the turn's answered clarifications from the inner agent's final
    messages, pairing each ``ask_user`` call with its answer ToolMessage. Robust to
    multiple clarifications in one turn (provenance, contract §7)."""
    asks: dict[str, dict] = {}
    for m in messages or []:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                if tc.get("name") == "ask_user":
                    asks[tc.get("id")] = tc.get("args") or {}
    out: list[dict] = []
    for m in messages or []:
        if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None) in asks:
            question = asks[m.tool_call_id].get("question", "")
            out.append(
                {
                    "clarification_id": new_clarification_id(question),
                    "question": question,
                    "answer": str(m.content),
                    "answered_by": "user",
                }
            )
    return out


class ServeRailsState(TypedDict, total=False):
    """Outer rails state for one question. Thin — only serializable primitives
    (no heavy deps; ADR 0001). ``context_block`` is the rendered semantic layer
    (Amendment 1) and ``seed_licensed`` the base L4 scope, both from ``assemble``."""

    question: str
    session_id: str
    base_provenance: dict
    context_block: str
    seed_licensed: list
    answer: Any
    outcome: str  # "finalize" | "refuse" | "continue" | "miss" | "clarify"
    clarification: dict  # ClarificationRequest to surface, when outcome == "clarify"


def _physical_to_id_map(corpus: "Corpus") -> dict[str, str]:
    from ..corpus.schemas import TableAsset

    out: dict[str, str] = {}
    for asset in corpus.assets:
        if not isinstance(asset, TableAsset):
            continue
        gov = getattr(asset, "governance", None)
        if gov is not None and getattr(gov, "excluded", False):
            continue
        out[f"{asset.schema}.{asset.physical_name}"] = asset.id
    return out


def _tables_used_in(
    sql: str | None,
    corpus: "Corpus",
    dialect: str,
    default_schema: str | None,
) -> list[str] | None:
    """Asset ids for the tables ``sql`` references, or ``None`` when there is no SQL.

    Used on the refusal paths so a turn that generated a query and had it *rejected* still
    records which tables it touched. Offline analysis reads ``tables_used`` to ask whether
    an answer reached past the schema router; without this, every blocked turn dropped out
    of that measurement — and the drop correlates with the event, because the escape most
    likely to be blocked is one that reached an out-of-routed table without licensing it.

    Resolved against the full serve corpus, the same map the success path uses, so an
    out-of-routed table resolves rather than being silently dropped.

    ``None`` rather than ``[]`` when there was no SQL: a turn that generated nothing used no
    tables, which is a different fact from a turn whose tables could not be resolved.
    """
    if not sql:
        return None
    found = sorted(
        _tables_used(sql, _physical_to_id_map(corpus), dialect, default_schema=default_schema)
    )
    return found or None


def _table_provenance_names(corpus: "Corpus", table_ids) -> list[str]:
    """Schema-qualified physical names for table provenance, sorted.

    Reuses :func:`licensed_physical_names` so there is one projection of asset ids
    to physical names in the tree. Qualified (``schema.table``) rather than bare:
    a pooled corpus repeats table names across schemas, and a bare list would let
    offline analysis credit a table from the wrong schema. Ids that resolve to no
    table are dropped, not passed through — a raw ``tbl_x_y`` id leaking into a
    list of physical names would read as an offered table that does not exist.
    """
    return sorted(licensed_physical_names(corpus, list(table_ids)))


def build_agent_core(
    corpus: "Corpus",
    gateway: "Gateway",
    identity: "Identity",
    model: Any,
    *,
    settings: "Settings",
    dialect: str,
    default_schema: str | None,
    embedder: "Embedder | None" = None,
    system_prompt: str = SYSTEM_PROMPT,
    enable_clarify: bool = False,
    checkpointer: Any = None,
    stages: "StageRecorder | None" = None,
    # The graph's shared retrieval index, so the agent's ``search_corpus`` does not rebuild
    # one per call. See :func:`make_tools`.
    index_cache: Any = None,
    # The routed schema set for this turn; bounds what ``inspect_schema`` may license
    # (AUDIT S4). None = unbounded, correct for a single-schema corpus.
    licensable_schemas: "frozenset[str] | set[str] | None" = None,
    # Live timeline side channel for ``search_corpus`` hits. See :func:`make_tools`.
    search_hits: dict | None = None,
):
    """Assemble ``create_agent`` with governed tools + middleware.

    ``enable_clarify`` adds the ``ask_user`` HITL tool and requires ``checkpointer``
    (``interrupt`` needs one to pause/resume). Both default off, so the
    eval/offline path builds the identical agent it always has. ``stages`` is the
    turn's :class:`StageRecorder`, so the middleware's tool counts / guardrail /
    execute records land on the same turn as the rails' own.
    """
    tools = make_tools(
        corpus,
        gateway,
        identity,
        embedder=embedder,
        enable_clarify=enable_clarify,
        index_cache=index_cache,
        licensable_schemas=licensable_schemas,
        search_hits=search_hits,
    )
    mw = GovernanceMiddleware(
        corpus,
        gateway,
        identity,
        dialect=dialect,
        default_schema=default_schema,
        settings=settings,
        stages=stages,
    )
    # Sequential tools: also bind at construction; middleware re-asserts per call (G1).
    bound_model = model
    if hasattr(model, "bind") and not isinstance(getattr(model, "responses", None), list):
        try:
            bound_model = model.bind(parallel_tool_calls=False)
        except Exception:
            bound_model = model
    return_agent = create_agent(
        model=bound_model,
        tools=tools,
        middleware=[mw],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )
    # L4: agent_core drains failed_model_calls after a raised model call.
    return_agent._gov_middleware = mw  # type: ignore[attr-defined]
    return return_agent


def _final_text(final: dict) -> str:
    """Text of the agent's last AI message (its closing statement), or ``""``."""
    for msg in reversed(list(final.get("messages") or [])):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            return str(content or "")
    return ""


def _sql_fingerprint(sql: str) -> str:
    """Whitespace/case/quote-insensitive key for comparing two SQL strings."""
    return re.sub(r'[\s"`\[\]]+', "", str(sql or "")).lower().rstrip(";")


def _sql_blocks_in_text(text: str) -> list[str]:
    """SQL the agent quoted in its closing message, best-effort.

    Fenced blocks first (``` or ```sql), then a bare trailing SELECT. Only used to
    *choose among* queries that already executed and passed — never as SQL to run,
    so a sloppy extraction can pick the wrong recorded query but can never introduce
    an unguardrailed one.
    """
    if not text:
        return []
    fence = chr(96) * 3
    blocks = re.findall(rf"{fence}(?:sql)?\s*(.+?){fence}", text, re.S | re.I)
    if blocks:
        return [b.strip() for b in blocks]
    bare = re.findall(r"(?is)\b(select\b.+?)(?:;|\Z)", text)
    return [b.strip() for b in bare]


def extract_final_sql(
    final: dict,
    *,
    corpus: "Corpus",
    dialect: str,
    default_schema: str | None = None,
) -> tuple[str | None, frozenset[str], dict | None]:
    """The query the agent presented as its answer: sql, tables_used (G3), ledger entry.

    Preference order:

    1. A passing ``run_query`` whose SQL the agent quoted in its final message.
    2. Otherwise the **last** passing ``run_query``.

    (2) alone was the whole rule, and it silently mis-reports any turn that runs a
    sanity check after its real answer — "revenue by region" followed by a
    ``SELECT COUNT(*)`` to confirm the row count delivers the count as the answer,
    with the correct query sitting earlier in the same ledger (AUDIT R1). The agent's
    own closing message is the only place its intent is stated, so consult it first
    and fall back to positional order when it says nothing usable.

    The chosen entry carries ``final_sql_source`` (``"agent_final_message"`` or
    ``"last_passing"``) so a scored row records which rule applied.
    """
    ledger = list(final.get("ledger") or [])
    phys_to_id = _physical_to_id_map(corpus)
    passing = [
        e
        for e in ledger
        if e.get("action") == "run_query" and e.get("verdict") == "pass" and e.get("sql")
    ]
    if not passing:
        return None, frozenset(), None

    chosen: dict | None = None
    source = "last_passing"
    if len(passing) > 1:
        quoted = {_sql_fingerprint(b) for b in _sql_blocks_in_text(_final_text(final))}
        if quoted:
            for entry in reversed(passing):
                if _sql_fingerprint(entry["sql"]) in quoted:
                    chosen, source = entry, "agent_final_message"
                    break
    if chosen is None:
        chosen = passing[-1]

    sql = chosen["sql"]
    tables_used = _tables_used(sql, phys_to_id, dialect, default_schema=default_schema)
    return sql, tables_used, {**chosen, "final_sql_source": source}


def _column_count_for(corpus: "Corpus", table_id: str) -> int:
    """Resolve by asset id then physical name; ambiguous bare → 0.

    Assemble rails use this to size licensed tables. Ambiguous bare names go
    through :meth:`Corpus.table_by_name` (``None``, not first-match).
    """
    asset = corpus.by_id(table_id)
    if not isinstance(asset, TableAsset):
        asset = corpus.table_by_name(table_id)
    if not isinstance(asset, TableAsset):
        return 0
    return sum(
        1
        for c in asset.columns
        if not getattr(getattr(c, "governance", None), "excluded", False)
    )


@dataclass(frozen=True)
class ServeDeployment:
    """Everything one compiled serve graph is deployed against.

    One value in place of the seventeen keyword arguments :func:`build_serve_rails`
    used to thread, so a caller that wants a graph names a deployment rather than
    re-typing the same argument list (there were nine such sites). Nothing here is
    per-question: the fields are the corpus, the connections, the model, and the
    per-turn *identifiers* the rails stamp — the question itself arrives in
    :class:`ServeRailsState`.

    Frozen because a compiled graph closes over it; a caller that needs a variant
    (a different ``session_id`` per eval graph, say) uses ``dataclasses.replace``
    and gets a new graph, rather than mutating a deployment two graphs share.
    """

    corpus: "Corpus"
    gateway: "Gateway"
    settings: "Settings"
    identity: "Identity"
    model: Any
    embedder: "Embedder | None" = None
    working_memory: "WorkingMemory | None" = None
    narrator: "AnswerNarrator | None" = None
    on_event: "Callable[[dict], None] | None" = None
    session_id: str = "agent"
    clarify_checkpointer: Any = None
    clarify_thread: str | None = None
    clarify_resume: Any = None
    run_id: str | None = None
    n_human: int = 1
    # Caller-owned, reusable across turns: the rails are rebuilt per question, which
    # would otherwise re-embed every schema document on every question (AUDIT R6).
    index_cache: "RetrievalIndexCache | None" = None
    schema_vectors: Any = None


@dataclass
class ServeRuntime:
    """What one compiled serve graph knows before the first question arrives.

    A :class:`ServeDeployment` plus everything derived from it once per graph: the
    dialect and serving schema, the join graph, the column allowlist, the resolved
    prompt texts, the schema router's chat client and pre-embedded schema vectors,
    the shared retrieval index, and the turn's two recorders.

    This is what makes the rails nodes *addressable*. They used to be closures
    inside ``build_serve_rails``, so no code outside that function could name one,
    let alone test or replace it. They are now module-level functions whose first
    argument is this object (N18).

    Deployment deps live here rather than in :class:`ServeRailsState` (ADR 0001 /
    finding #7): the state channels stay thin and serializable so a checkpointer
    added later does not have to carry a corpus and a gateway.
    """

    deployment: ServeDeployment
    dialect: str
    default_schema: str | None
    agent_core_prompt: str
    schema_pick_prompt: str
    #: Join graph over the serve corpus (``build_graph``).
    graph_obj: Any
    allowlist: Any
    #: Every schema the corpus holds tables for.
    corpus_schemas: set[str]
    spans_schemas: bool
    route_top_k: int
    router_chat: Any
    router_schema_vectors: Any
    #: Resolved retrieval index — the caller's when it supplied one, else this
    #: graph's own. Never ``None``, unlike ``deployment.index_cache``.
    index_cache: "RetrievalIndexCache"
    stages: StageRecorder
    events: GovEventStream
    #: Memoised ``filter_corpus_for_retrieval`` results, keyed by routed schema set.
    routed_corpora: dict = field(default_factory=dict)
    #: Side channel: ``search_corpus`` → :func:`_resolve_tool` (serve-transparency
    #: C4). Cleared on each ingest so a prior turn's hit cannot leak into the next.
    search_hits: dict = field(default_factory=dict)
    #: Per-invoke turn counter in a one-cell list, so a reused rails graph (the eval
    #: ``agent_solver``) mints a fresh turn_id / run_id per question instead of
    #: UPSERT-colliding on ``eval:1``.
    turn_n: list[int] = field(default_factory=lambda: [0])

    # -- deployment passthroughs, so node bodies read `rt.corpus` not
    # `rt.deployment.corpus` for the dozen deps they touch every turn.
    @property
    def corpus(self) -> "Corpus":
        return self.deployment.corpus

    @property
    def gateway(self) -> "Gateway":
        return self.deployment.gateway

    @property
    def settings(self) -> "Settings":
        return self.deployment.settings

    @property
    def identity(self) -> "Identity":
        return self.deployment.identity

    @property
    def model(self) -> Any:
        return self.deployment.model

    @property
    def embedder(self) -> "Embedder | None":
        return self.deployment.embedder

    @property
    def working_memory(self) -> "WorkingMemory | None":
        return self.deployment.working_memory

    @property
    def narrator(self) -> "AnswerNarrator | None":
        return self.deployment.narrator

    @property
    def session_id(self) -> str:
        return self.deployment.session_id

    @property
    def clarify_checkpointer(self) -> Any:
        return self.deployment.clarify_checkpointer

    @property
    def clarify_thread(self) -> str | None:
        return self.deployment.clarify_thread

    @property
    def clarify_resume(self) -> Any:
        return self.deployment.clarify_resume

    @classmethod
    def build(cls, deployment: ServeDeployment) -> "ServeRuntime":
        """Derive the per-graph state. Runs once per compiled graph, never per turn."""
        corpus = deployment.corpus
        settings = deployment.settings
        # Bare references resolve to the serving schema (the SQLite ATTACH alias, or the
        # pinned Postgres schema); None means the source spans every schema, so a bare
        # reference fails closed.
        default_schema = settings.datasource.serving_schema()
        # Prompt variants resolved ONCE per stack, not per turn: the map is what
        # ``serve_config_hash`` / the stamped record claim this graph sent, so
        # re-reading settings inside a node would let the claim and the bytes diverge.
        prompt_variants = prompts.resolve(settings.prompt_variants)
        # Schema routing (shortlist + curated cross-schema expansion) only earns its keep
        # when the corpus actually spans multiple schemas (the scale run); a single-db
        # corpus skips it. Cheap to compute once here from the licensed table assets.
        corpus_schemas = {a.schema for a in corpus.assets if isinstance(a, TableAsset)}
        spans_schemas = len(corpus_schemas) > 1
        # A pinned serving schema (SQLite ATTACH alias / pinned Postgres schema) must be
        # one the corpus actually holds tables for; otherwise the qualified allowlist
        # keys never match and EVERY query silently false-refuses. Catch that config
        # drift loudly here, where the datasource and corpus first meet. (None = span
        # all schemas, so there is nothing to reconcile.)
        if default_schema is not None and corpus_schemas and default_schema not in corpus_schemas:
            raise ValueError(
                f"serving schema {default_schema!r} has no tables in the corpus "
                f"(schemas present: {sorted(corpus_schemas)}). Align the datasource "
                "`schema`/`corpus_pin` with the loaded corpus, or leave `schema` unset "
                "to span all schemas."
            )
        # Schema-routing knobs (D15). ``schema_route_top_k`` widens the BM25/embedder
        # shortlist; ``schema_route_llm_pick`` collapses it to a single LLM-chosen schema
        # (D15 — the single-schema-answer regime, e.g. the BIRD data
        # lake). Both only bite when the corpus spans schemas. The router chat wraps the
        # raw model in a ChatClient (``pick_schema`` needs ``.complete``); built once.
        router_chat = None
        if spans_schemas and settings.schema_route_llm_pick and deployment.model is not None:
            from ..llm import LangChainChatClient  # noqa: PLC0415 (lazy: agents extra)

            router_chat = LangChainChatClient(deployment.model)
        # Schema-document vectors are constant per corpus: embed them once here rather
        # than re-embedding every schema doc on each question (O(schemas) embed calls
        # per turn at data-lake scale). Only the question is embedded per turn.
        router_schema_vectors = deployment.schema_vectors
        if router_schema_vectors is None and spans_schemas and deployment.embedder is not None:
            router_schema_vectors = embed_schema_documents(corpus, deployment.embedder)
        # One rich-event emitter for the whole turn (reset in `ingest_node`); the agent
        # path emits the {seq,kind,step,status,detail} contract, never the legacy {stage}
        # shape governance.py's on_event helpers still accept but which agent.py never
        # feeds a callback into (docs/analyst.md, the event contract).
        finalize_ctx = FinalizeCtx(
            settings=settings,
            run_id=deployment.run_id or new_run_id(),
            thread_id=deployment.session_id,
            n_human=deployment.n_human,
            model=getattr(settings.models, "llm_model", None),
            serve_path="agent",
            t0=time.perf_counter(),
        )
        # The durable counterpart of the live stream: one recorder per turn, owned by
        # the emitter so both reset on the same boundary (see StageRecorder).
        stages = StageRecorder()
        return cls(
            deployment=deployment,
            dialect=deployment.gateway.catalog().dialect.value,
            default_schema=default_schema,
            agent_core_prompt=prompts.text("agent_core", prompt_variants),
            schema_pick_prompt=prompts.text("schema_pick", prompt_variants),
            graph_obj=build_graph(corpus),
            allowlist=column_allowlist(corpus),
            corpus_schemas=corpus_schemas,
            spans_schemas=spans_schemas,
            route_top_k=settings.schema_route_top_k,
            router_chat=router_chat,
            router_schema_vectors=router_schema_vectors,
            # Retrieval indexes are constant per routed corpus, exactly like the schema
            # vectors above — but ``retrieve`` used to rebuild both on every question,
            # which meant re-embedding every asset in the routed corpus per turn. Only
            # the question embedding is genuinely per-turn. Both caches are graph-scoped,
            # so each eval worker's graph owns its own and they need no lock (see
            # ``eval/parallel.py`` on per-worker isolation); they die with the graph, so
            # nothing crosses runs.
            index_cache=(
                deployment.index_cache
                if deployment.index_cache is not None
                else RetrievalIndexCache()
            ),
            stages=stages,
            events=GovEventStream(
                deployment.on_event, finalize_ctx=finalize_ctx, stages=stages
            ),
            turn_n=[deployment.n_human - 1],
        )


def _timed(rt: ServeRuntime, stage: Stage, node):
    """Register a rails node with its own stage record.

    Wrapping at registration rather than inside each body keeps the nodes
    un-indented; a node that handles its own exception (``agent_core``) times
    the inner call instead, so a caught crash is not recorded as a stage that
    ran fine.
    """

    def run(state: ServeRailsState) -> dict:
        with rt.stages.stage(stage):
            update = node(state)
        # A node can end the turn from inside the block (a missing-edge
        # refusal), and its own record did not exist yet when `final()`
        # stamped the answer. Re-stamp so the enclosing stage is not missing from
        # exactly the turns that stopped in it — that absence would bias any
        # average over the records towards the turns that got further.
        answer = update.get("answer") if isinstance(update, dict) else None
        if answer is not None:
            update = {
                **update,
                "answer": replace(
                    answer,
                    provenance={
                        **(answer.provenance or {}),
                        **rt.stages.provenance(),
                    },
                ),
            }
        return update

    return run


def ingest_node(rt: ServeRuntime, state: ServeRailsState) -> dict:
    """Open the turn: reset the emitter, mint this question's run id, seed provenance."""
    rt.events.reset()  # new turn: fresh seq + serve_path tag + stage records
    rt.search_hits.clear()
    rt.turn_n[0] += 1
    question = state["question"]
    if rt.events._finalize_ctx is not None:
        # Prefer a run_id bound by the outer invoke (logging_setup ContextVar)
        # so Langfuse metadata, stage_events, and log lines share one key.
        from ..logging_setup import peek_run_id  # noqa: PLC0415

        rt.events._finalize_ctx = replace(
            rt.events._finalize_ctx,
            run_id=peek_run_id() or new_run_id(),
            n_human=rt.turn_n[0],
            t0=time.perf_counter(),
            token_usage=[],
            question=question,
        )
    with rt.stages.stage(Stage.route):
        pass  # the turn's first recorded stage; term binding is the agent's job now
    base = {
        "session_id": state.get("session_id") or rt.session_id,
        "user": rt.identity.user,
        "runtime": "agent",
    }
    rt.events.rail("route")
    return {
        "base_provenance": base,
        "session_id": state.get("session_id") or rt.session_id,
    }


def refuse_gate_node(rt: ServeRuntime, state: ServeRailsState) -> dict:
    """Refuse before any model call when the question matches a curated negative."""
    negative = _match_negative_example(rt.corpus, state["question"])
    if negative is not None:
        rt.events.rail("refuse_gate", "refused", negative_example=negative.id)
        ans = refusal(
            escalation=negative.escalation,
            provenance={
                **state["base_provenance"],
                "refused_by": "refuse_gate",
                "negative_example": negative.id,
            },
        )
        ans = rt.events.final(ans)
        return {"answer": ans, "outcome": "refuse"}
    rt.events.rail("refuse_gate", "ok")
    return {"outcome": "continue"}


def after_refuse(state: ServeRailsState) -> Literal["assemble", "__end__"]:
    return END if state.get("outcome") == "refuse" else "assemble"


def _route_schemas(
    rt: ServeRuntime, question: str, base_provenance: dict
) -> tuple["Corpus", dict]:
    """Pick the corpus slice this question retrieves against, and record how.

    Returns the retrieval corpus and the provenance the routing decision earned.
    A single-schema corpus never routes and takes the ``else`` branch — which
    still records, because an absent record and a bypassed router are different
    facts (see the comment there).
    """
    if not rt.spans_schemas:
        # A single-schema corpus never routes. Recorded, not omitted: an absent
        # schema_pick record would read as a build that cannot measure the pick.
        rt.stages.skipped(Stage.schema_pick, spans_schemas=False)
        # Same for the shortlist: a bypassed router recorded no channel, and the
        # eval must read that as "not measured" rather than as a healthy embedding
        # route. ``skipped`` says so positively.
        rt.stages.skipped(Stage.shortlist, spans_schemas=False)
        # ...and the same is true of the *row*. Leaving `base_provenance`
        # untouched here made a bypassed turn indistinguishable from a routed one
        # that lost its provenance: the row recorded `routed_schemas=[]`, so
        # `routed_hit` was False on every question and `routing_recall` read 0.0
        # for a pool that has nothing to route. Worse, the eval's own
        # "was routing bypassed?" guard tests `isinstance(total_schemas, int)`,
        # which no single-schema turn could ever satisfy — so every wrong answer
        # in a one-schema pool was charged to `schema_pick`, a stage that did not
        # run. The bypass is now asserted by the code that knows it, not inferred
        # downstream from a field's absence.
        #
        # `routed_schemas` is the schema the turn is pinned to, so `routed_hit`
        # is true for the right reason. `schema_pick` and `shortlisted_schemas`
        # stay ABSENT on purpose: stamping them would enrol these rows in
        # `schema_pick_accuracy` and `gold_schema_rank` as unanimous successes of
        # a picker and a shortlist that never ran, which is how a metric starts
        # measuring its own denominator.
        return rt.corpus, {
            **base_provenance,
            "routed_schemas": sorted(rt.corpus_schemas),
            "total_schemas": len(rt.corpus_schemas),
            "routing_bypassed": True,
        }
    # Shortlist by embedding similarity (BM25 fallback). Then either (a)
    # collapse to a single LLM-chosen schema (``schema_route_llm_pick`` — the
    # single-schema-answer regime, no cross-schema joins), or (b) expand the
    # shortlist along curated cross-schema joins (the default cross-schema
    # regime). Record both counts + the pick so a scale run can see how the
    # shortlist prunes and where it routed (silent mis-routing would
    # otherwise be invisible in the EX number).
    route_channel: dict = {}
    route_ranked: list = []
    # Timed as its own stage, and not only for the timing. ``eval.arms``'s solver
    # relay is an explicit ALLOW-LIST of provenance keys, so nothing this node adds
    # to ``base_provenance`` reaches a scored row unless that list names it —
    # ``schema_route_channel`` / ``schema_route_degraded`` did not, which is why
    # two fields that existed for a year appeared in no artifact. ``stage_events``
    # IS relayed verbatim (and lands in ``stage_events.jsonl`` besides), so the
    # channel travels in this record's ``detail`` and the driver reads it there.
    # Keep both: if the relay ever carries the keys directly, the driver prefers
    # them.
    with rt.stages.stage(Stage.shortlist) as shortlist_detail:
        shortlisted = shortlist_schemas(
            rt.corpus,
            question,
            top_k=rt.route_top_k,
            embedder=rt.embedder,
            schema_vectors=rt.router_schema_vectors,
            settings=rt.settings,
            index_cache=rt.index_cache,
            channel_out=route_channel,
            ranked_out=route_ranked,
        )
        # A silent embedding->BM25 degradation costs 4.7pp of shortlist recall at the
        # default top_k (0.953 -> 0.906, measured over 1351 questions in
        # ``runs/ablation/e1-shortlist-curated.json``); recorded so the drop is
        # attributable (AUDIT R8).
        shortlist_detail.update(route_channel)
        shortlist_detail["n_candidates"] = len(shortlisted)
    base_provenance = {**base_provenance, **route_channel}
    picked: str | None = None
    pick_fallback: str | None = None
    if rt.router_chat is not None and shortlisted:
        # Its own sub-stage: a routing regression has to be separable from
        # the rest of assemble, and this is the only LLM call in the node.
        with rt.stages.stage(Stage.schema_pick, n_candidates=len(shortlisted)) as detail:
            decision = pick_schema(
                rt.corpus,
                question,
                shortlisted,
                chat=rt.router_chat,
                max_columns=rt.settings.schema_pick_max_columns,
                system_prompt=rt.schema_pick_prompt,
            )
            detail["fallback"] = decision.fallback is not None
        picked, pick_fallback = decision.schema, decision.fallback
        if decision.usage_metadata:
            rt.events.add_token_usage(
                [
                    {
                        "source": "router",
                        "usage_metadata": decision.usage_metadata,
                    }
                ]
            )
        routed = (
            frozenset([picked])
            if picked
            else expand_schemas_via_curated_joins(rt.corpus, set(shortlisted))
        )
    else:
        rt.stages.skipped(Stage.schema_pick, llm_pick=rt.router_chat is not None)
        routed = expand_schemas_via_curated_joins(rt.corpus, set(shortlisted))
    # Memoised by routed schema set. The filter is deterministic in
    # ``routed``, so rebuilding it per question minted a fresh ``Corpus``
    # object with identical contents every time — which then defeated the
    # retrieval index cache below, because there was nothing stable to key on.
    # Bounded by the number of distinct neighbourhoods a run visits.
    routed_key = frozenset(routed)
    retrieval_corpus = rt.routed_corpora.get(routed_key)
    if retrieval_corpus is None:
        retrieval_corpus = rt.routed_corpora[routed_key] = filter_corpus_for_retrieval(
            rt.corpus, routed
        )
    base_provenance = {
        **base_provenance,
        "routed_schemas": sorted(routed),
        # Kept in RELEVANCE order, not sorted: the position of the true
        # schema in this list is the signal that separates "retrieval never
        # surfaced it" from "the picker overrode a correct rank-1", and
        # alphabetising would throw it away. (``routed`` is a frozenset, so
        # it has no meaningful order and stays sorted for stable diffs.)
        "shortlisted_schemas": list(shortlisted),
        "total_schemas": len(rt.corpus_schemas),
        "schema_pick": picked,
        # Set when ``picked`` is really the rank-1 fallback after a failed
        # or unparseable pick, so a degraded row is not scored as a
        # decision the model made.
        "schema_pick_fallback": pick_fallback,
    }
    # Live timeline: which schemas were shortlisted and which one won.
    # Scores only when the embedding channel ran; truncated only when the
    # picker saw fewer tables than the schema has (SCHEMA_PICK_MAX_TABLES).
    channel = route_channel.get("schema_route_channel")
    score_by = {s: sc for s, sc in route_ranked}
    candidates = []
    for rank, name in enumerate(shortlisted, start=1):
        row: dict[str, Any] = {"schema": name, "rank": rank}
        if channel == "embedding" and name in score_by:
            row["score"] = score_by[name]
        candidates.append(row)
    truncated = []
    table_totals = _schema_table_totals(rt.corpus, shortlisted)
    for name in shortlisted:
        tables_total = table_totals.get(name, 0)
        tables_shown = min(SCHEMA_PICK_MAX_TABLES, tables_total)
        if tables_total > tables_shown:
            truncated.append(
                {
                    "schema": name,
                    "tables_shown": tables_shown,
                    "tables_total": tables_total,
                }
            )
    rt.events.rail(
        "schema_route",
        "ok",
        n_total=len(rt.corpus_schemas),
        channel=channel,
        degraded=route_channel.get("schema_route_degraded"),
        candidates=candidates,
        picked=picked,
        fallback=pick_fallback,
        truncated=truncated or None,
    )
    return retrieval_corpus, base_provenance


def _assemble_inner(rt: ServeRuntime, state: ServeRailsState) -> dict:
    """Amendment 1: run the deterministic front half and seed the semantic layer.

    Reuses the exact deterministic assembly (retrieval + licensing +
    ``assemble_context``) that used to feed the old template generator, so
    the agent starts at parity (context + base licensed scope), then refines.
    """
    corpus = rt.corpus
    settings = rt.settings
    question = state["question"]
    sid = state.get("session_id") or rt.session_id
    history = list(rt.working_memory.history(sid)) if rt.working_memory is not None else []
    retrieval_corpus, base_provenance = _route_schemas(rt, question, state["base_provenance"])
    with rt.stages.stage(Stage.retrieve) as detail:
        retrieval = retrieve(
            retrieval_corpus,
            question,
            embedder=rt.embedder,
            settings=settings,
            index_cache=rt.index_cache,
        )
        detail["n_tables"] = len(retrieval.table_ids)
    # Table-level provenance. Without it a wrong-table answer is indistinguishable
    # from a wrong-*retrieval* one in the scored rows, and those need opposite
    # fixes (retrieval tuning vs. generation prompting). Recorded before the
    # missing-edge check so a refusal carries what retrieval offered.
    base_provenance = {
        **base_provenance,
        "retrieved_tables": _table_provenance_names(corpus, retrieval.table_ids),
        # Carried so the stamp can say something about the *evidence*, not only
        # about what went wrong downstream (AUDIT C2). 0.0 = the question names
        # nothing this corpus contains.
        "retrieval_lexical_coverage": retrieval.lexical_coverage,
    }
    missing = detect_missing_join_path(corpus, rt.graph_obj, set(retrieval.table_ids))
    if missing is not None:
        rt.events.rail(
            "assemble", "refused", missing_edge=True, schemas=sorted(missing.schemas)
        )
        ans = missing_edge_refusal(base_provenance, missing)
        ans = rt.events.final(ans)
        return {"answer": ans, "outcome": "refuse"}
    try:
        licensing_join_ids = plan_joins(rt.graph_obj, set(retrieval.table_ids)).join_ids
    except ValueError:
        licensing_join_ids = []
    licensed_ids = _licensed_table_ids(corpus, rt.graph_obj, retrieval, licensing_join_ids)
    base_provenance = {
        **base_provenance,
        "licensed_tables": _table_provenance_names(corpus, licensed_ids),
    }
    context = assemble_context(
        corpus,
        retrieval,
        licensed_table_ids=licensed_ids,
        history=history,
        db_name=settings.datasource.db,
        always_note_global_max=settings.always_note_global_max,
        always_note_char_max=settings.always_note_char_max,
        # Both default to off (0 / False), so this wiring is a no-op until an
        # experiment turns them on — deliberately, because a silent behaviour change
        # here would make every existing run incomparable to every future one. Off,
        # the rendered block is byte-identical (pinned by a hash test over the real
        # 20260731 corpora).
        max_table_columns=settings.analyst_max_table_columns,
        compact_caveats=settings.analyst_compact_suspect_caveats,
    )
    # What the model was actually handed. Outcome metrics alone cannot separate
    # "the curated corpus did not help" from "the curated corpus never reached
    # the prompt"; these do.
    rendered = context.render()
    base_provenance = {
        **base_provenance,
        "injected_note_ids": list(context.injected_note_ids),
        "n_notes_injected": len(context.injected_note_ids),
        "n_few_shots_injected": len(context.few_shots),
        "n_joins_injected": len(context.joins),
        "n_metrics_injected": len(context.metrics),
        "n_terms_injected": len(context.terms),
        "n_caveats_injected": len(context.caveats),
        "context_chars": len(rendered),
        # How much the column budget withheld this turn. 0 both when the budget is
        # off and when it did not bind, which is the right conflation for analysis
        # — either way the model saw every column — while a non-zero value is the
        # only way to tell a small context apart from a TRUNCATED one. Without it
        # the intervention would be visible in the config and invisible in the row.
        "n_columns_omitted": context.n_columns_omitted,
        # The identity of what was handed over, not just its size. Two arms
        # differing only in corpus content can render byte-identical context —
        # that is what a treatment failing to reach the model looks like, and a
        # character count is too coarse to catch it. `eval.treatment` compares
        # these across arms and voids the comparison when they agree too often.
        "context_hash": hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16],
    }
    rt.events.rail(
        "assemble",
        "ok",
        schema=rt.default_schema,
        tables=len(context.tables),
        few_shots=len(context.few_shots),
        notes=len(context.injected_note_ids),
        caveats=len(context.caveats),
        context_chars=len(rendered),
        items={
            "tables": [
                {
                    "id": t.id,
                    "physical_name": t.physical_name,
                    "schema": t.schema,
                    "retrieved": t.retrieved,
                }
                for t in context.tables
            ],
            "joins": [
                {
                    "on": j.on,
                    "cardinality": j.cardinality,
                    "confidence": j.confidence,
                    "low_confidence": j.low_confidence,
                }
                for j in context.joins
            ],
            "few_shots": [{"question": fs.question} for fs in context.few_shots],
            "notes": [
                _assemble_note_item(corpus, nid) for nid in context.injected_note_ids
            ],
            "terms": [{"name": t.name} for t in context.terms],
            "metrics": [{"name": m.name} for m in context.metrics],
        },
    )
    # ``base_provenance`` is always a new dict by this point (table provenance
    # is recorded unconditionally above), so it always propagates.
    return {
        "context_block": rendered,
        "seed_licensed": sorted(licensed_ids),
        "base_provenance": base_provenance,
        "outcome": "continue",
    }


def assemble_node(rt: ServeRuntime, state: ServeRailsState) -> dict:
    """Deterministic front half — fails closed like every other terminal path.

    This was the one node in the outer rails with no exception handling. Its body
    guards a single ``plan_joins`` ``ValueError``; everything else — schema
    shortlisting, the LLM pick, retrieval, licensing, ``assemble_context`` — could
    raise straight out of ``graph.invoke``. An embedder timeout or a retrieval bug
    therefore produced no ``Answer``, no refusal, and no run-log row at all, which is
    a worse audit gap than losing the ledger: there is nothing to find afterwards.

    ``agent_core_node`` has had this protection on three separate paths for a while
    (``GovernanceHardStop`` / ``GraphRecursionError`` / bare ``Exception``); this is
    the same shape, so a failure here becomes an L4 model-error refusal that still
    runs through ``events.final`` and therefore still logs.

    In the eval harness the gap was masked one layer up — the driver wraps
    ``solve_with_meta`` — so this mattered most in live chat, where
    ``api/graph_app.py`` has only a ``finally``.
    """
    try:
        return _assemble_inner(rt, state)
    except Exception as err:
        # ``configure_logging`` (M4 N12a) now installs a root handler, so a
        # ``logger.exception`` would reach disk — but this path still prints
        # (bulk print→logger is N11 / later, not a silent mid-N12a swap). The
        # traceback remains the fastest operator signal when assemble dies.
        print(f"*** assemble failed, refusing (model_error): {type(err).__name__}: {err}")
        traceback.print_exc()
        ans = refusal(
            escalation=_ESCALATION_MODEL_ERROR,
            provenance={
                **state["base_provenance"],
                "refused_by": "model_error",
                "error_type": type(err).__name__,
                # Which node died. Without it a `model_error` refusal is
                # indistinguishable from one raised inside the agent core, and the
                # two call for opposite investigations.
                "failed_stage": Stage.assemble.value,
            },
        )
        ans = rt.events.final(ans)
        return {"answer": ans, "outcome": "refuse"}


def after_assemble(state: ServeRailsState) -> Literal["agent_core", "__end__"]:
    return END if state.get("outcome") == "refuse" else "agent_core"


def _tool_start_detail(step: str, args: dict) -> dict:
    """The live-timeline ``start`` detail for one governed or exploration tool call."""
    if step == "search_corpus":
        return {"query": args.get("query")}
    if step in ("inspect_schema", "sample_rows"):
        return {"table_id": args.get("table_id")}
    if step == "run_query":
        return {"sql": args.get("sql")}
    if step == "ask_user":
        # Timeline row for the clarification (contract §5); the active prompt is
        # the interrupt value, this is the passive "asking…" step.
        return {"question": args.get("question"), "why": args.get("why")}
    return {}


def _notes_detail(step: str, args: dict, content: str) -> dict:
    """Durable + live detail for one ``read_notes`` / ``grep_notes`` call.

    Both tools return a rendered string and stash nothing, so what they *did* has
    to be read back off the ``ToolMessage``. The sentinels matched below are the
    tools' own contract (``analyst.tools``); anything else is a real hit. Read back
    rather than stashed through a side channel (as ``search_corpus`` does) because
    these tools touch no state — there is nothing to stash, and a side channel for
    two string checks would be the more fragile of the two couplings.
    """
    if step == "read_notes":
        return {
            "note_id": args.get("note_id"),
            "found": not content.endswith(": not available"),
            "withheld": content.endswith("(names excluded identifiers)"),
            "chars": len(content),
        }
    # "(no matching notes)" is one line and zero hits; an "error:" line is a bad
    # pattern, not a miss, and the two must not read alike.
    bad_pattern = content.startswith("error:")
    empty = bad_pattern or content in ("(no matching notes)", "")
    lines = [ln for ln in content.splitlines() if ln and not ln.startswith("…")]
    return {
        "pattern": args.get("pattern"),
        "hits": 0 if empty else len(lines),
        "pattern_error": bad_pattern,
        "capped": "…(output capped)" in content or "…(hit cap)" in content,
        "chars": len(content),
    }


def _resolve_tool(
    rt: ServeRuntime,
    step,
    args,
    entry,
    tcid,
    licensed_delta,
    attempt,
    *,
    ms: float | None = None,
    content: str = "",
):
    """Emit one tool-resolve event + its durable stage record; return the updated
    ``run_query`` attempt count.

    For governed tools the ledger ``entry`` is the source of truth (verdict /
    layer / reason / sql / rows), so the live event and the final
    ``governance_ledger`` never drift (Inv #10). Exploration tools have no
    ledger entry — their detail is reconstructed from args + the licensed delta.

    Two sinks, deliberately: :class:`GovEventStream` is the *ephemeral* one and
    goes nowhere at all when no ``on_event`` callback is attached, which is every
    eval run — so the same detail is also written to the turn's
    :class:`StageRecorder`, which lands on provenance and in
    ``stage_events.jsonl``. Before that, a run's entire tool trajectory survived
    only as a per-question histogram (``n_tool_calls``): counts, no ordering, no
    arguments, no results.

    What does **not** get a record: a ``run_query``, and a ``sample_rows`` that
    reached the guardrail. ``GovernanceMiddleware.wrap_tool_call`` already writes
    ``guardrail`` + ``execute`` for those (both stamped ``detail["action"]``), and
    a third row would double-count an action the ledger and every rate derived
    from it already agree on. The asymmetry is the ``sample_rows`` licensing
    denial: it returns *before* ``check()`` runs, so it leaves neither of that
    pair, and without the record below it leaves no durable trace at all.

    ``ms`` is measured across ``agent.stream`` (start event → ToolMessage), so it
    includes tools-node dispatch, not just the tool body.
    """
    entry = entry or {}
    if step == "run_query":
        attempt += 1
        verdict = entry.get("verdict")
        result = entry.get("result") or {}
        rt.events.tool(
            "run_query",
            _LEDGER_STATUS.get(verdict, "error"),
            step_id=tcid,
            attempt=attempt,
            sql=entry.get("sql") or args.get("sql"),
            verdict=verdict,
            layer=entry.get("layer"),
            reason=entry.get("reason"),
            allowed=entry.get("allowed"),
            rows=result.get("row_count"),
        )
    elif step == "sample_rows":
        verdict = entry.get("verdict")
        result = entry.get("result") or {}
        table_id = args.get("table_id") or entry.get("table_id")
        rt.events.tool(
            "sample_rows",
            _LEDGER_STATUS.get(verdict, "error"),
            step_id=tcid,
            table_id=table_id,
            rows=result.get("row_count"),
            reason=entry.get("reason"),
        )
        if verdict == "deny":
            # The pre-guardrail licensing denial (see the docstring). ``status`` stays
            # "ok" — the tool ran exactly as designed; the refusal is the detail.
            rt.stages.record(
                Stage.sample_rows,
                ms=ms,
                action="sample_rows",
                table_id=table_id,
                denied=True,
                reason=entry.get("reason"),
            )
    elif step == "inspect_schema":
        table_id = args.get("table_id")
        licensed = bool(licensed_delta)
        columns = _column_count_for(rt.corpus, table_id) if licensed else 0
        rt.events.tool(
            "inspect_schema",
            "ok" if licensed else "miss",
            step_id=tcid,
            table_id=table_id,
            columns=columns,
            licensed=licensed,
        )
        rt.stages.record(
            Stage.inspect_schema,
            ms=ms,
            table_id=table_id,
            columns=columns,
            licensed=licensed,
        )
    elif step == "search_corpus":
        hit = rt.search_hits.pop("last", {})
        rt.events.tool(
            "search_corpus",
            "ok",
            step_id=tcid,
            query=args.get("query"),
            tables=hit.get("tables"),
            few_shots=hit.get("few_shots"),
            metrics=hit.get("metrics"),
            notes=hit.get("notes"),
            terms=hit.get("terms"),
            items=hit.get("items"),
        )
        # ``items`` (the per-hit ids and names) is deliberately NOT recorded: it is a
        # display payload for the live timeline, and the durable record wants the
        # query and the shape of what came back, not a second copy of the corpus.
        rt.stages.record(
            Stage.search_corpus,
            ms=ms,
            query=args.get("query"),
            tables=hit.get("tables"),
            few_shots=hit.get("few_shots"),
            metrics=hit.get("metrics"),
            notes=hit.get("notes"),
            terms=hit.get("terms"),
        )
    elif step in ("read_notes", "grep_notes"):
        detail = _notes_detail(step, args, content)
        rt.events.tool(step, "ok", step_id=tcid, **detail)
        rt.stages.record(Stage(step), ms=ms, **detail)
    else:
        rt.events.tool(step, "ok", step_id=tcid)
    return attempt


def _stream_agent(rt: ServeRuntime, agent, init: dict, config: dict) -> dict:
    """Consume ``agent.stream`` to emit live tool events; return the final state.

    Tool calls are forced sequential (G1), so each ``tools`` super-step carries
    exactly one ToolMessage (+ at most one ledger entry), which makes pairing a
    model-node ``start`` with its ``tools``-node ``resolve`` trivial. The final
    accumulated state comes from the last ``values`` chunk (replaces
    ``agent.invoke``'s return value)."""
    # ``init`` is the fresh input dict, or a ``Command(resume=...)`` on the
    # HITL resume path — which isn't a mapping, so start empty and let the
    # first ``values`` chunk populate it.
    final_state: dict = dict(init) if isinstance(init, dict) else {}
    pending: dict[str, dict] = {}  # tool_call_id → {"step","args"}
    attempt = 0
    try:
        for mode, chunk in agent.stream(
            init, config=config, stream_mode=["updates", "values"]
        ):
            if mode == "values":
                if isinstance(chunk, dict):
                    final_state = chunk
                continue
            if not isinstance(chunk, dict):
                continue
            for update in chunk.values():
                if not isinstance(update, dict):
                    continue
                ledger_iter = iter(
                    e for e in (update.get("ledger") or []) if isinstance(e, dict)
                )
                licensed_delta = update.get("licensed") or []
                for msg in update.get("messages") or []:
                    if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                        for tc in msg.tool_calls:
                            tcid = tc.get("id")
                            step = tc.get("name") or "tool"
                            args = tc.get("args") or {}
                            # ``t0`` is the only clock this tool call gets: the
                            # tools node is inside ``create_agent``, so nothing on
                            # our side wraps the call itself. Measured here, the
                            # span is start-event → ToolMessage.
                            pending[tcid] = {
                                "step": step,
                                "args": args,
                                "t0": time.perf_counter(),
                            }
                            rt.events.tool(
                                step, "start", step_id=tcid, **_tool_start_detail(step, args)
                            )
                    elif isinstance(msg, ToolMessage):
                        tcid = getattr(msg, "tool_call_id", None)
                        info = pending.pop(tcid, None) or {}
                        step = info.get("step") or "tool"
                        args = info.get("args") or {}
                        t0 = info.get("t0")
                        # None, not 0.0, when the start was never seen (a resumed
                        # HITL turn replays the ToolMessage alone): unmeasured must
                        # not read as instantaneous.
                        ms = None if t0 is None else round((time.perf_counter() - t0) * 1000.0, 3)
                        raw = getattr(msg, "content", "")
                        entry = next(ledger_iter, None) if step in ("run_query", "sample_rows") else None
                        attempt = _resolve_tool(
                            rt,
                            step,
                            args,
                            entry,
                            tcid,
                            licensed_delta,
                            attempt,
                            ms=ms,
                            content=raw if isinstance(raw, str) else "",
                        )
    except GovernanceHardStop as e:
        # Pair the L2 block with its pending run_query start so the row resolves
        # instead of hanging (the exception raised inside wrap_tool_call before
        # the tools-node update was streamed).
        tcid = next(iter(pending), None)
        rt.events.tool(
            "run_query",
            "blocked",
            step_id=tcid,
            attempt=sum(1 for x in e.ledger if x.get("action") == "run_query"),
            sql=e.entry.get("sql"),
            verdict="block",
            layer=e.entry.get("layer"),
            reason=e.entry.get("reason"),
            allowed=e.entry.get("allowed"),
        )
        raise
    except GraphRecursionError as e:
        # Step budget exhausted: carry the accumulated ledger (from the last
        # streamed `values` chunk) to the caller so the audit trail survives
        # the exhaustion path instead of being reported as empty (Inv #10).
        e.partial_state = final_state  # type: ignore[attr-defined]
        raise
    return final_state


def _drain_failed_model_calls(rt: ServeRuntime, agent) -> None:
    """Fold the middleware's failed-model-call token stubs into the turn's usage.

    A raised model call still burned tokens. Every exit from ``agent_core`` — the
    clean one and all three failure ones — has to drain them, or a turn's cost is
    understated by exactly the calls that went wrong.
    """
    mw = getattr(agent, "_gov_middleware", None)
    if mw is not None and mw.failed_model_calls:
        rt.events.add_token_usage(mw.failed_model_calls)
        mw.failed_model_calls.clear()


def _turn_system_prompt(rt: ServeRuntime, context_block: str) -> str:
    """The agent-core prompt for this turn: registry text + governed context + now."""
    system_prompt = rt.agent_core_prompt
    if context_block:
        system_prompt = f"{rt.agent_core_prompt}\n\n## Governed context\n{context_block}"
    # Ground relative-date reasoning ("today", "this month", "last quarter") in
    # the machine's LOCAL wall-clock time, stamped per turn (never import-time).
    now_local = datetime.now().astimezone()
    return (
        f"{system_prompt}\n\n## Current time\n"
        f"The current date and time is {now_local.strftime('%Y-%m-%d %H:%M:%S %Z (UTC%z)')} "
        f"(the user's local time). Resolve any relative dates in the question against it."
    )


def _clarify_preflight(
    rt: ServeRuntime, state: ServeRailsState, agent, inner_cfg: dict
) -> "tuple[dict | None, Any]":
    """Handle an inner agent already paused on ``ask_user`` from a prior pass.

    Returns ``(rails update, agent input override)``. A non-None update ends the
    node immediately (re-surface the request, or refuse a declined clarification);
    a non-None override is the ``Command(resume=...)`` that unpauses the agent.
    Both None means there is nothing pending and the turn starts fresh.
    """
    snap = agent.get_state(inner_cfg)
    if not (snap.next and getattr(snap, "interrupts", None)):
        return None, None
    request = snap.interrupts[0].value
    if rt.clarify_resume is None:
        # Outer graph re-ran before interrupt() returned the answer;
        # re-surface the same request (contract §2 re-execution).
        return {"outcome": "clarify", "clarification": request}, None
    parsed = parse_response(rt.clarify_resume)
    if parsed["declined"]:
        ledger = list((snap.values or {}).get("ledger") or [])
        ans = refusal(
            escalation=_ESCALATION_CLARIFY_DECLINED,
            provenance={
                **state["base_provenance"],
                "refused_by": "clarification_declined",
                "clarification_id": request.get("clarification_id"),
                "governance_ledger": ledger,
            },
        )
        ans = rt.events.final(ans)
        return {"answer": ans, "outcome": "refuse"}, None
    # Resume the paused inner agent with the user's answer.
    return None, Command(resume=rt.clarify_resume)


def _refuse_hard_stop(
    rt: ServeRuntime, state: ServeRailsState, agent, e: GovernanceHardStop
) -> dict:
    """L2 block raised out of the tool wrapper — refuse with the ledger in hand."""
    _drain_failed_model_calls(rt, agent)
    ledger = list(e.ledger)
    entry = e.entry
    hard_stop_tables = _tables_used_in(
        entry.get("sql"), rt.corpus, rt.dialect, rt.default_schema
    )
    ans = refusal(
        escalation=_ESCALATION_GUARDRAIL,
        provenance={
            **state["base_provenance"],
            "refused_by": "guardrail",
            "failed_layer": entry.get("layer"),
            "reason": entry.get("reason"),
            "sql": entry.get("sql"),
            "governance_ledger": ledger,
            # A hard stop is a refusal *with* SQL in hand. See
            # :func:`_tables_used_in`.
            **({"tables_used": hard_stop_tables} if hard_stop_tables else {}),
        },
    )
    ans = rt.events.final(ans)
    return {"answer": ans, "outcome": "refuse"}


def _refuse_recursion_exhausted(
    rt: ServeRuntime, state: ServeRailsState, question: str, e: GraphRecursionError
) -> dict:
    """Step budget exhausted without a final answer → fail closed (§6),
    never crash the caller (the eval arm / a live turn). Recovers the
    accumulated ledger from the exhausted stream (attached by
    :func:`_stream_agent`) so the refusal still carries its real audit trail
    and attempt count, not an empty placeholder (Inv #10)."""
    partial_state = getattr(e, "partial_state", None) or {}
    ledger = list(partial_state.get("ledger") or [])
    attempts = sum(1 for x in ledger if x.get("action") == "run_query")
    ans = _finish_unsuccessful(
        settings=rt.settings,
        gateway=rt.gateway,
        identity=rt.identity,
        last_refusal={
            "refused_by": "exhausted",
            "escalation": _ESCALATION_NO_COVERAGE,
            "reason": f"agent exceeded {AGENT_RECURSION_LIMIT}-step budget",
            "governance_ledger": ledger,
        },
        attempts=attempts,
        base_provenance={
            **state["base_provenance"],
            "recursion_exhausted": True,
            "governance_ledger": ledger,
        },
        question=question,
        narrator=None,  # narration deferred to narrate_node
        stages=rt.stages,
        allowlist=rt.allowlist,
        allowed_tables=frozenset(
            licensed_physical_names(rt.corpus, partial_state.get("licensed") or [])
        ),
        dialect=rt.dialect,
        default_schema=rt.default_schema,
    )
    ans = rt.events.final(ans)
    return {"answer": ans, "outcome": "refuse"}


def _refuse_model_error(
    rt: ServeRuntime, state: ServeRailsState, agent, e: Exception
) -> dict:
    """L4: model/call failure — drain failed-call stubs and still emit one
    portable record (metadata-only; no exception message text).
    Distinct from coverage refusals: do not tell the user to add coverage
    when the failure was infrastructure (AUDIT R2)."""
    _drain_failed_model_calls(rt, agent)
    ans = refusal(
        escalation=_ESCALATION_MODEL_ERROR,
        provenance={
            **state["base_provenance"],
            "refused_by": "model_error",
            "error_type": type(e).__name__,
        },
    )
    ans = rt.events.final(ans)
    return {"answer": ans, "outcome": "refuse"}


def _finish_without_passing_sql(
    rt: ServeRuntime,
    question: str,
    final: dict,
    ledger: list,
    base_provenance: dict,
) -> dict:
    """The agent stopped with no query that both ran and passed: refuse.

    Charged to the guardrail when the ledger holds a rejected ``run_query``, and
    to coverage when it holds none at all — the two call for opposite fixes.
    """
    last = next(
        (
            e
            for e in reversed(ledger)
            if e.get("action") == "run_query" and e.get("verdict") != "pass"
        ),
        None,
    )
    last_refusal = {
        "refused_by": "guardrail" if last else "no_coverage",
        "escalation": _ESCALATION_GUARDRAIL if last else _ESCALATION_NO_COVERAGE,
        "failed_layer": (last or {}).get("layer"),
        "reason": (last or {}).get("reason"),
        "sql": (last or {}).get("sql"),
        "governance_ledger": ledger,
    }
    attempts = sum(1 for e in ledger if e.get("action") == "run_query")
    # The tables the blocked SQL referenced. Without this, a turn that generated a
    # query and had it *rejected* carries no ``tables_used`` at all — and offline
    # analysis that reads that field to ask "did this answer reach past the router"
    # gets ``None`` and drops the row.
    #
    # That exclusion is not random, which is what makes it worth stamping: the
    # escape most likely to trip L4 term-semantics is precisely one that reached an
    # out-of-routed table *without* ``inspect_schema`` licensing it first. So the
    # rows silently dropped correlate with the event being measured, and the
    # escape rate is biased low by an unknown amount.
    #
    # Resolved against the full serve ``corpus``, the same map the success path
    # uses, so an out-of-routed table resolves rather than being dropped.
    blocked_tables = _tables_used_in(
        (last or {}).get("sql"), rt.corpus, rt.dialect, rt.default_schema
    )
    ans = _finish_unsuccessful(
        settings=rt.settings,
        gateway=rt.gateway,
        identity=rt.identity,
        last_refusal=last_refusal,
        attempts=attempts or 0,
        base_provenance={
            **base_provenance,
            "governance_ledger": ledger,
            # Absent, not empty, when there was no SQL to parse: a turn that never
            # generated one used no tables, which is a different fact from a turn
            # whose tables could not be resolved.
            **({"tables_used": blocked_tables} if blocked_tables else {}),
        },
        question=question,
        narrator=None,  # narration deferred to narrate_node
        stages=rt.stages,
        allowlist=rt.allowlist,
        allowed_tables=frozenset(
            licensed_physical_names(rt.corpus, final.get("licensed") or [])
        ),
        dialect=rt.dialect,
        default_schema=rt.default_schema,
    )
    if ans.provenance.get("governance_ledger") is None:
        ans = replace(
            ans,
            provenance={**ans.provenance, "governance_ledger": ledger},
        )
    ans = rt.events.final(ans)
    return {"answer": ans, "outcome": "refuse"}


def _refuse_missing_ledger_result(
    rt: ServeRuntime,
    question: str,
    final: dict,
    ledger: list,
    sql: str,
    base_provenance: dict,
) -> dict:
    """Should not happen for a pass entry; fail closed rather than re-execute."""
    ans = _finish_unsuccessful(
        settings=rt.settings,
        gateway=rt.gateway,
        identity=rt.identity,
        last_refusal={
            "refused_by": "execution",
            "escalation": _ESCALATION_GUARDRAIL,
            "error": "missing ledger result for passing SQL",
            "sql": sql,
            "governance_ledger": ledger,
        },
        attempts=sum(1 for e in ledger if e.get("action") == "run_query"),
        base_provenance=base_provenance,
        question=question,
        narrator=None,  # narration deferred to narrate_node
        stages=rt.stages,
        allowlist=rt.allowlist,
        allowed_tables=frozenset(
            licensed_physical_names(rt.corpus, final.get("licensed") or [])
        ),
        dialect=rt.dialect,
        default_schema=rt.default_schema,
    )
    ans = rt.events.final(ans)
    return {"answer": ans, "outcome": "refuse"}


def agent_core_node(rt: ServeRuntime, state: ServeRailsState) -> dict:
    """Run the governed agent core for one turn and finalize its outcome.

    Every exit is one of: a delivered answer, a refusal, or a clarification to
    surface. The three ``except`` clauses are the fail-closed paths (§6) — a crash
    inside the inner agent must never reach the caller as an exception.
    """
    question = state["question"]
    seed_licensed = list(state.get("seed_licensed") or [])
    clarify_on = rt.clarify_checkpointer is not None
    agent = build_agent_core(
        rt.corpus,
        rt.gateway,
        rt.identity,
        rt.model,
        settings=rt.settings,
        dialect=rt.dialect,
        default_schema=rt.default_schema,
        embedder=rt.embedder,
        system_prompt=_turn_system_prompt(rt, state.get("context_block") or ""),
        enable_clarify=clarify_on,
        checkpointer=rt.clarify_checkpointer,
        stages=rt.stages,
        index_cache=rt.index_cache,
        # Recorded by assemble_node; absent (or empty) on a single-schema corpus
        # where routing never runs, which leaves the scope unbounded as before.
        licensable_schemas=frozenset(
            state.get("base_provenance", {}).get("routed_schemas") or ()
        )
        or None,
        search_hits=rt.search_hits,
    )

    # One tracing handler per turn: it is attached at the outer graph.invoke
    # (answer_question_agent) and propagates into this inner agent.stream via the
    # run context. Attaching a *second* handler here logged every model call
    # twice (same LangChain run_id → two Langfuse generations under different
    # parents → ~2x trace cost/tokens), so inherit rather than re-attach.
    inner_cfg: dict = {
        "recursion_limit": AGENT_RECURSION_LIMIT,
    }
    agent_input: Any = {
        "messages": [HumanMessage(content=question)],
        "licensed": seed_licensed,
        "ledger": [],
    }
    if clarify_on:
        inner_cfg["configurable"] = {"thread_id": rt.clarify_thread}
        early, resume_input = _clarify_preflight(rt, state, agent, inner_cfg)
        if early is not None:
            return early
        if resume_input is not None:
            agent_input = resume_input

    try:
        # Timed inside the try, not around the node: every ``except`` below
        # converts a crash into a fail-closed refusal, so a node-level record
        # would stamp ``ok`` on the exact failures this measurement exists to
        # find. The recorder marks the stage ``error`` and re-raises into them.
        with rt.stages.stage(Stage.agent_core) as detail:
            final = _stream_agent(rt, agent, agent_input, inner_cfg)
            detail["n_messages"] = len(final.get("messages") or [])
        rt.events.add_token_usage(final.get("token_usage"))
        _drain_failed_model_calls(rt, agent)
    except GovernanceHardStop as e:
        return _refuse_hard_stop(rt, state, agent, e)
    except GraphRecursionError as e:
        return _refuse_recursion_exhausted(rt, state, question, e)
    except Exception as e:
        return _refuse_model_error(rt, state, agent, e)

    # Local provenance copy — never mutate the input state in place (a LangGraph
    # node returns updates, it does not edit `state`; the in-place write would
    # bite once a checkpointer or a parallel branch is added). Finalizers below
    # read this local.
    base_provenance = state["base_provenance"]
    if clarify_on:
        # The inner agent may have paused on a fresh ask_user this pass; bubble
        # it up so the chat-graph node surfaces it as a client interrupt.
        snap2 = agent.get_state(inner_cfg)
        if snap2.next and getattr(snap2, "interrupts", None):
            return {"outcome": "clarify", "clarification": snap2.interrupts[0].value}
        # Otherwise fold the turn's answered clarifications into provenance (§7),
        # so both success and refusal finalizers below carry them.
        answered = _extract_clarifications(final.get("messages"))
        if answered:
            base_provenance = {**base_provenance, "clarifications": answered}

    ledger = list(final.get("ledger") or [])
    sql, tables_used, pass_entry = extract_final_sql(
        final, corpus=rt.corpus, dialect=rt.dialect, default_schema=rt.default_schema
    )
    if not sql or pass_entry is None:
        return _finish_without_passing_sql(rt, question, final, ledger, base_provenance)

    result = result_from_ledger(pass_entry)
    if result is None:
        return _refuse_missing_ledger_result(
            rt, question, final, ledger, sql, base_provenance
        )

    generated = GeneratedSql(
        sql=sql,
        tables_used=tables_used,
        metric_id=None,
    )
    attempts = sum(1 for e in ledger if e.get("action") == "run_query")
    ans = _finalize_success(
        question=question,
        graph=rt.graph_obj,
        generated=generated,
        result=result,
        attempts=attempts,
        base_provenance=base_provenance,
        dialect=rt.dialect,
        allowlist=rt.allowlist,
        narrator=None,  # narration deferred to narrate_node
        ledger=ledger,
    )
    ans = rt.events.final(ans)
    return {"answer": ans, "outcome": "finalize"}


def narrate_node(rt: ServeRuntime, state: ServeRailsState) -> dict:
    """Phrase the delivered answer into grounded English — a first-class graph
    step so the narrator's model call is one trace span under the turn (not a
    side call inside finalization). No-op for refusals (no ``answer`` with a
    result grid) and when no narrator is configured; a
    narrator failure keeps the deterministic finalizer text (see
    ``narrate_answer``)."""
    answer = state.get("answer")
    if answer is None:
        return {}
    with rt.stages.stage(Stage.narrate) as detail:
        narrated, usage = narrate_answer(answer, state["question"], rt.narrator)
        narrated_ran = narrated is not answer
        detail["narrated"] = narrated_ran
    # ``narrate`` is the turn's last stage but runs AFTER events.final() stamped
    # provenance, so re-stamp the records here — and before the re-append below,
    # so the durable row gets them too. Without this, the one stage that runs
    # after finalization is the one stage no record ever mentions.
    narrated = replace(
        narrated,
        provenance={**(narrated.provenance or {}), **rt.stages.provenance()},
    )
    # Only fold narrator tokens when the narrator actually ran. Usage comes from
    # narrate_answer's return value (M4 N14) — not a shared client field.
    if not narrated_ran:
        return {"answer": narrated}
    if usage:
        narrated = amend_run_tokens(
            narrated,
            settings=rt.settings,
            extra_usage=[{"source": "narrator", "usage_metadata": usage}],
            model=getattr(rt.settings.models, "llm_model", None),
        )
    return {"answer": narrated}


def build_serve_rails(deployment: "ServeDeployment"):
    """Compile the outer deterministic StateGraph wrapping the agent core.

    HITL clarification (contract: docs/analyst.md, serve-time clarification) is on
    only when ``clarify_checkpointer`` is set — then ``agent_core`` runs the inner
    agent on that checkpointer + ``clarify_thread`` so ``ask_user``'s ``interrupt``
    can pause/resume. ``clarify_resume`` (a ``ClarificationResponse``) resumes a
    paused inner agent. All three default off/None, leaving the eval path
    byte-for-byte unchanged."""
    rt = ServeRuntime.build(deployment)
    builder = StateGraph(ServeRailsState)
    builder.add_node("ingest", partial(ingest_node, rt))
    builder.add_node("refuse_gate", partial(refuse_gate_node, rt))
    builder.add_node(
        "assemble", _timed(rt, Stage.assemble, partial(assemble_node, rt))
    )
    builder.add_node("agent_core", partial(agent_core_node, rt))
    builder.add_node("narrate", partial(narrate_node, rt))
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "refuse_gate")
    builder.add_conditional_edges("refuse_gate", after_refuse, ["assemble", END])
    builder.add_conditional_edges("assemble", after_assemble, ["agent_core", END])
    builder.add_edge("agent_core", "narrate")
    builder.add_edge("narrate", END)
    return builder.compile()


def answer_question_agent(
    question: str,
    identity: "Identity",
    *,
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    session_id: str,
    model: Any,
    embedder: "Embedder | None" = None,
    working_memory: "WorkingMemory | None" = None,
    narrator: "AnswerNarrator | None" = None,
    on_event: "Callable[[dict], None] | None" = None,
    clarify_checkpointer: Any = None,
    clarify_thread: str | None = None,
    clarify_resume: Any = None,
    run_id: str | None = None,
    n_human: int = 1,
    index_cache: "RetrievalIndexCache | None" = None,
    schema_vectors: Any = None,
) -> "Answer | ClarificationPending":
    """Run one question through the agentic serve rails.

    Returns an ``Answer`` normally, or a :class:`ClarificationPending` when the
    inner agent paused on ``ask_user`` (HITL; contract in docs/analyst.md). Clarification is active
    only when ``clarify_checkpointer`` is passed; the eval path calls this without
    it and always gets an ``Answer``.
    """
    _run_id = run_id or new_run_id()
    from ..logging_setup import bind_log_context, reset_log_context
    from ..obs import RunContext, tracing_invoke_config
    from ..prompts import prompt_set_hash as _prompt_set_hash
    from ..provenance import turn_id as make_turn_id

    tid = make_turn_id(session_id, n_human)
    log_tokens = bind_log_context(run_id=_run_id, turn_id=tid)
    try:
        graph = build_serve_rails(
            deployment=ServeDeployment(
                corpus=corpus,
                gateway=gateway,
                settings=settings,
                identity=identity,
                model=model,
                embedder=embedder,
                working_memory=working_memory,
                narrator=narrator,
                on_event=on_event,
                session_id=session_id,
                clarify_checkpointer=clarify_checkpointer,
                clarify_thread=clarify_thread,
                clarify_resume=clarify_resume,
                run_id=_run_id,
                n_human=n_human,
                index_cache=index_cache,
                schema_vectors=schema_vectors,
            )
        )
        ctx = RunContext(
            run_id=_run_id,
            turn_id=tid,
            corpus_pin=getattr(settings.datasource, "corpus_pin", None),
            prompt_set_hash=_prompt_set_hash(settings.prompt_variants),
            identity=getattr(identity, "user", None),
        )
        final = graph.invoke(
            {
                "question": question,
                "session_id": session_id,
            },
            config=tracing_invoke_config(ctx=ctx),
        )
    finally:
        reset_log_context(log_tokens)
    if final.get("outcome") == "clarify":
        return ClarificationPending(final.get("clarification") or {})
    answer = final.get("answer")
    if answer is None:
        ans = refusal(
            escalation=_ESCALATION_NO_COVERAGE,
            provenance={"refused_by": "no_coverage", "session_id": session_id},
        )
        return finalize_and_log(
            ans,
            ctx=FinalizeCtx(
                settings=settings,
                run_id=_run_id,
                thread_id=session_id,
                n_human=n_human,
                model=getattr(settings.models, "llm_model", None),
                outcome="refuse",
                question=question,
            ),
        )
    return answer
