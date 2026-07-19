"""Governed agentic serve core + outer deterministic rails (ADR 0002).

Inner loop: ``create_agent`` + ``GovernanceMiddleware`` + governed tools.
Outer loop: thin LangGraph ``StateGraph`` — refuse-gate, cache, agent_core,
finalize / refuse. Agent-internal ``messages`` / ``licensed`` / ``ledger`` stay
node-local and never merge into the chat transcript (ADR 0001 / gotcha G2).
Deployment deps (corpus, gateway, graph, allowlist) are closures — not state
channels — so a future checkpointer stays thin.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..corpus.schemas import TableAsset
from ..gateway import column_allowlist
from ..graph import build_graph, detect_missing_join_path, plan_joins
from ..obs import tracing_callbacks
from ..retrieval import (
    embed_schema_documents,
    expand_schemas_via_curated_joins,
    filter_corpus_for_retrieval,
    retrieve,
    select_schema,
    shortlist_schemas,
)
from .answer import refusal
from .context import assemble_context
from .governance import (
    _ESCALATION_GUARDRAIL,
    _ESCALATION_NO_COVERAGE,
    _LEDGER_STATUS,
    GovEventStream,
    _finalize_success,
    _finish_unsuccessful,
    _licensed_table_ids,
    _match_negative_example,
    _try_cache_hit,
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
from .clarify import new_clarification_id, parse_response
from .routing import bind_terms, route_intent
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
    from .cache import SqlCache
    from .narrate import AnswerNarrator

SYSTEM_PROMPT = """You answer questions over a governed data warehouse by writing \
**one read-only SELECT**.

The `## Governed context` below has been assembled for this question — its tables \
are already licensed and its joins, metrics, few-shot examples, and reliability \
caveats are curated, authoritative guidance. **Prefer it over guessing.** Follow \
the few-shot examples' style, use the listed joins, and never use a column marked \
DO NOT USE.

Write SQL using only identifiers shown in the context, then call `run_query`. If \
the context is missing a table or example you need, call `search_corpus` for more, \
and `inspect_schema` any table **not** already listed before querying it (that \
licenses it). Use `sample_rows` if you need to see real values. If `run_query` \
returns BLOCKED or an error, read it, fix the SQL, and retry (max 3). Never guess \
an identifier. Call tools **one at a time**.
"""

_ESCALATION_CLARIFY_DECLINED = (
    "I needed one clarification to answer this safely, but didn't receive an "
    "answer, so I stopped rather than guess. Re-ask with the detail and I'll continue."
)


class ClarificationPending:
    """Returned by :func:`answer_question_agent` instead of an ``Answer`` when the
    inner agent paused on ``ask_user``. The caller (the chat-graph node) turns
    ``request`` into a client-visible ``interrupt`` and calls back with the answer
    (contract: docs/plans/hitl-clarification-contract.md §2)."""

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
):
    """Assemble ``create_agent`` with governed tools + middleware.

    ``enable_clarify`` adds the ``ask_user`` HITL tool and requires ``checkpointer``
    (``interrupt`` needs one to pause/resume). Both default off, so the
    eval/offline path builds the identical agent it always has.
    """
    tools = make_tools(
        corpus,
        gateway,
        identity,
        embedder=embedder,
        enable_clarify=enable_clarify,
    )
    mw = GovernanceMiddleware(
        corpus,
        gateway,
        identity,
        dialect=dialect,
        default_schema=default_schema,
        settings=settings,
    )
    # Sequential tools: also bind at construction; middleware re-asserts per call (G1).
    bound_model = model
    if hasattr(model, "bind") and not isinstance(getattr(model, "responses", None), list):
        try:
            bound_model = model.bind(parallel_tool_calls=False)
        except Exception:
            bound_model = model
    return create_agent(
        model=bound_model,
        tools=tools,
        middleware=[mw],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )


def extract_final_sql(
    final: dict,
    *,
    corpus: "Corpus",
    dialect: str,
    default_schema: str | None = None,
) -> tuple[str | None, frozenset[str], dict | None]:
    """Last passing ``run_query``: sql, tables_used from SQL parse (G3), ledger entry."""
    ledger = list(final.get("ledger") or [])
    phys_to_id = _physical_to_id_map(corpus)
    for entry in reversed(ledger):
        if entry.get("action") == "run_query" and entry.get("verdict") == "pass":
            sql = entry.get("sql")
            if not sql:
                continue
            tables_used = _tables_used(
                sql, phys_to_id, dialect, default_schema=default_schema
            )
            return sql, tables_used, entry
    return None, frozenset(), None


def build_serve_rails(
    *,
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    identity: "Identity",
    model: Any,
    embedder: "Embedder | None" = None,
    cache: "SqlCache | None" = None,
    working_memory: "WorkingMemory | None" = None,
    narrator: "AnswerNarrator | None" = None,
    on_event: "Callable[[dict], None] | None" = None,
    session_id: str = "agent",
    clarify_checkpointer: Any = None,
    clarify_thread: str | None = None,
    clarify_resume: Any = None,
):
    """Compile the outer deterministic StateGraph wrapping the agent core.

    HITL clarification (contract: docs/plans/hitl-clarification-contract.md) is on
    only when ``clarify_checkpointer`` is set — then ``agent_core`` runs the inner
    agent on that checkpointer + ``clarify_thread`` so ``ask_user``'s ``interrupt``
    can pause/resume. ``clarify_resume`` (a ``ClarificationResponse``) resumes a
    paused inner agent. All three default off/None, leaving the eval path
    byte-for-byte unchanged."""
    # Bare references resolve to the serving schema (the SQLite ATTACH alias, or the
    # pinned Postgres schema); None means the source spans every schema, so a bare
    # reference fails closed.
    default_schema = settings.datasource.serving_schema()
    dialect = gateway.catalog().dialect.value
    # Closures — not state channels (ADR 0001 / finding #7).
    graph_obj = build_graph(corpus)
    allowlist = column_allowlist(corpus)
    # Schema routing (shortlist + curated cross-schema expansion) only earns its keep
    # when the corpus actually spans multiple schemas (the scale run); a single-db
    # corpus skips it. Cheap to compute once here from the licensed table assets.
    _corpus_schemas = {a.schema for a in corpus.assets if isinstance(a, TableAsset)}
    spans_schemas = len(_corpus_schemas) > 1
    # A pinned serving schema (SQLite ATTACH alias / pinned Postgres schema) must be
    # one the corpus actually holds tables for; otherwise the qualified allowlist
    # keys never match and EVERY query silently false-refuses. Catch that config
    # drift loudly here, where the datasource and corpus first meet. (None = span
    # all schemas, so there is nothing to reconcile.)
    if default_schema is not None and _corpus_schemas and default_schema not in _corpus_schemas:
        raise ValueError(
            f"serving schema {default_schema!r} has no tables in the corpus "
            f"(schemas present: {sorted(_corpus_schemas)}). Align the datasource "
            "`schema`/`corpus_pin` with the loaded corpus, or leave `schema` unset "
            "to span all schemas."
        )
    # Schema-routing knobs (D15). ``schema_route_top_k`` widens the BM25/embedder
    # shortlist; ``schema_route_llm_pick`` collapses it to a single LLM-chosen schema
    # (pipeline-design §5.1 — the single-schema-answer regime, e.g. the BIRD data
    # lake). Both only bite when the corpus spans schemas. The router chat wraps the
    # raw model in a ChatClient (``select_schema`` needs ``.complete``); built once.
    route_top_k = settings.schema_route_top_k
    route_llm_pick = settings.schema_route_llm_pick
    router_chat = None
    if spans_schemas and route_llm_pick and model is not None:
        from ..llm import LangChainChatClient  # noqa: PLC0415 (lazy: agents extra)

        router_chat = LangChainChatClient(model)
    # Schema-document vectors are constant per corpus: embed them once here rather
    # than re-embedding every schema doc on each question (O(schemas) embed calls
    # per turn at data-lake scale). Only the question is embedded per turn.
    router_schema_vectors = (
        embed_schema_documents(corpus, embedder)
        if spans_schemas and embedder is not None
        else None
    )
    # One rich-event emitter for the whole turn (reset in `ingest`); the agent path
    # emits the {seq,kind,step,status,detail} contract, never the legacy {stage}
    # shape governance.py's on_event helpers still accept but which agent.py never
    # feeds a callback into (docs/plans/agent-step-visualization.md).
    events = GovEventStream(on_event)

    def _column_count(table_id: str) -> int:
        asset = corpus.by_id(table_id)
        if not isinstance(asset, TableAsset):
            asset = next(
                (a for a in corpus.assets if isinstance(a, TableAsset) and a.physical_name == table_id),
                None,
            )
        if not isinstance(asset, TableAsset):
            return 0
        return sum(
            1
            for c in asset.columns
            if not getattr(getattr(c, "governance", None), "excluded", False)
        )

    def ingest(state: ServeRailsState) -> dict:
        events.reset()  # new turn: fresh seq + serve_path tag
        question = state["question"]
        route = route_intent(question)
        bound_terms = bind_terms(corpus, question)
        base = {
            "route": route.value,
            "bound_terms": bound_terms,
            "session_id": state.get("session_id") or session_id,
            "user": identity.user,
            "runtime": "agent",
        }
        events.rail("route", intent=route.value)
        return {
            "base_provenance": base,
            "session_id": state.get("session_id") or session_id,
        }

    def refuse_gate(state: ServeRailsState) -> dict:
        negative = _match_negative_example(corpus, state["question"])
        if negative is not None:
            events.rail("refuse_gate", "refused", negative_example=negative.id)
            ans = refusal(
                escalation=negative.escalation,
                provenance={
                    **state["base_provenance"],
                    "refused_by": "refuse_gate",
                    "negative_example": negative.id,
                },
            )
            events.final(ans)
            return {"answer": ans, "outcome": "refuse"}
        events.rail("refuse_gate", "ok")
        return {"outcome": "continue"}

    def after_refuse(state: ServeRailsState) -> Literal["cache", "__end__"]:
        return END if state.get("outcome") == "refuse" else "cache"

    def assemble(state: ServeRailsState) -> dict:
        """Amendment 1: run the deterministic front half and seed the semantic layer.

        Reuses the exact deterministic assembly (retrieval + licensing +
        ``assemble_context``) that used to feed the old template generator, so
        the agent starts at parity (context + base licensed scope), then refines.
        """
        question = state["question"]
        sid = state.get("session_id") or session_id
        history = list(working_memory.history(sid)) if working_memory is not None else []
        base_provenance = state["base_provenance"]
        retrieval_corpus = corpus
        if spans_schemas:
            # Shortlist by embedding similarity (BM25 fallback). Then either (a)
            # collapse to a single LLM-chosen schema (``schema_route_llm_pick`` — the
            # single-schema-answer regime, no cross-schema joins), or (b) expand the
            # shortlist along curated cross-schema joins (the default cross-schema
            # regime). Record both counts + the pick so a scale run can see how the
            # shortlist prunes and where it routed (silent mis-routing would
            # otherwise be invisible in the EX number).
            shortlisted = shortlist_schemas(
                corpus,
                question,
                top_k=route_top_k,
                embedder=embedder,
                schema_vectors=router_schema_vectors,
            )
            picked: str | None = None
            if router_chat is not None and shortlisted:
                picked = select_schema(corpus, question, shortlisted, chat=router_chat)
                routed = (
                    frozenset([picked])
                    if picked
                    else expand_schemas_via_curated_joins(corpus, set(shortlisted))
                )
            else:
                routed = expand_schemas_via_curated_joins(corpus, set(shortlisted))
            retrieval_corpus = filter_corpus_for_retrieval(corpus, routed)
            base_provenance = {
                **base_provenance,
                "routed_schemas": sorted(routed),
                "shortlisted_schemas": sorted(shortlisted),
                "total_schemas": len(_corpus_schemas),
                "schema_pick": picked,
            }
        retrieval = retrieve(retrieval_corpus, question, embedder=embedder)
        missing = detect_missing_join_path(
            corpus, graph_obj, set(retrieval.table_ids)
        )
        if missing is not None:
            events.rail(
                "assemble", "refused", missing_edge=True, schemas=sorted(missing.schemas)
            )
            ans = missing_edge_refusal(base_provenance, missing)
            events.final(ans)
            return {"answer": ans, "outcome": "refuse"}
        try:
            licensing_join_ids = plan_joins(graph_obj, set(retrieval.table_ids)).join_ids
        except ValueError:
            licensing_join_ids = []
        licensed_ids = _licensed_table_ids(corpus, graph_obj, retrieval, licensing_join_ids)
        context = assemble_context(
            corpus,
            retrieval,
            licensed_table_ids=licensed_ids,
            history=history,
        )
        events.rail(
            "assemble",
            "ok",
            schema=default_schema,
            tables=len(context.tables),
            few_shots=len(context.few_shots),
        )
        out: dict = {
            "context_block": context.render(),
            "seed_licensed": sorted(licensed_ids),
            "outcome": "continue",
        }
        if base_provenance is not state["base_provenance"]:
            out["base_provenance"] = base_provenance
        return out

    def after_assemble(state: ServeRailsState) -> Literal["agent_core", "__end__"]:
        return END if state.get("outcome") == "refuse" else "agent_core"

    def cache_lookup(state: ServeRailsState) -> dict:
        if cache is None:
            return {"outcome": "miss"}
        hit = _try_cache_hit(
            cache,
            state["question"],
            gateway,
            identity,
            settings,
            allowlist,
            dialect,
            graph_obj,
            state["base_provenance"],
            default_schema=default_schema,
            narrator=None,  # narration is a dedicated graph node (see narrate_node)
            on_event=None,  # agent path emits the rich contract below, not {stage}
        )
        if hit is not None:
            events.rail("cache", "hit", metric_id=hit.provenance.get("metric_id"))
            events.final(hit)
            return {"answer": hit, "outcome": "finalize"}
        return {"outcome": "miss"}

    def after_cache(state: ServeRailsState) -> Literal["assemble", "narrate"]:
        # A cache hit is a delivered answer → phrase it in the narrate node too, so
        # cached and freshly-generated answers take the identical finalization path.
        return "narrate" if state.get("outcome") == "finalize" else "assemble"

    def _tool_start_detail(step: str, args: dict) -> dict:
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

    def _resolve_tool(step, args, entry, tcid, licensed_delta, attempt):
        """Emit one tool-resolve event; return the updated run_query attempt count.

        For governed tools the ledger ``entry`` is the source of truth (verdict /
        layer / reason / sql / rows), so the live event and the final
        ``governance_ledger`` never drift (Inv #10). Exploration tools have no
        ledger entry — their detail is reconstructed from args + the licensed delta.
        """
        entry = entry or {}
        if step == "run_query":
            attempt += 1
            verdict = entry.get("verdict")
            result = entry.get("result") or {}
            events.tool(
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
            events.tool(
                "sample_rows",
                _LEDGER_STATUS.get(verdict, "error"),
                step_id=tcid,
                table_id=args.get("table_id") or entry.get("table_id"),
                rows=result.get("row_count"),
                reason=entry.get("reason"),
            )
        elif step == "inspect_schema":
            table_id = args.get("table_id")
            licensed = bool(licensed_delta)
            events.tool(
                "inspect_schema",
                "ok" if licensed else "miss",
                step_id=tcid,
                table_id=table_id,
                columns=_column_count(table_id) if licensed else 0,
                licensed=licensed,
            )
        elif step == "search_corpus":
            events.tool("search_corpus", "ok", step_id=tcid, query=args.get("query"))
        else:
            events.tool(step, "ok", step_id=tcid)
        return attempt

    def _stream_agent(agent, init: dict, config: dict) -> dict:
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
                                pending[tcid] = {"step": step, "args": args}
                                events.tool(
                                    step, "start", step_id=tcid, **_tool_start_detail(step, args)
                                )
                        elif isinstance(msg, ToolMessage):
                            tcid = getattr(msg, "tool_call_id", None)
                            info = pending.pop(tcid, None) or {}
                            step = info.get("step") or "tool"
                            args = info.get("args") or {}
                            entry = next(ledger_iter, None) if step in ("run_query", "sample_rows") else None
                            attempt = _resolve_tool(step, args, entry, tcid, licensed_delta, attempt)
        except GovernanceHardStop as e:
            # Pair the L2 block with its pending run_query start so the row resolves
            # instead of hanging (the exception raised inside wrap_tool_call before
            # the tools-node update was streamed).
            tcid = next(iter(pending), None)
            events.tool(
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

    def agent_core_node(state: ServeRailsState) -> dict:
        question = state["question"]
        context_block = state.get("context_block") or ""
        seed_licensed = list(state.get("seed_licensed") or [])
        system_prompt = SYSTEM_PROMPT
        if context_block:
            system_prompt = f"{SYSTEM_PROMPT}\n\n## Governed context\n{context_block}"
        # Ground relative-date reasoning ("today", "this month", "last quarter") in
        # the machine's LOCAL wall-clock time, stamped per turn (never import-time).
        now_local = datetime.now().astimezone()
        system_prompt = (
            f"{system_prompt}\n\n## Current time\n"
            f"The current date and time is {now_local.strftime('%Y-%m-%d %H:%M:%S %Z (UTC%z)')} "
            f"(the user's local time). Resolve any relative dates in the question against it."
        )

        clarify_on = clarify_checkpointer is not None
        agent = build_agent_core(
            corpus,
            gateway,
            identity,
            model,
            settings=settings,
            dialect=dialect,
            default_schema=default_schema,
            embedder=embedder,
            system_prompt=system_prompt,
            enable_clarify=clarify_on,
            checkpointer=clarify_checkpointer,
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
            inner_cfg["configurable"] = {"thread_id": clarify_thread}
            snap = agent.get_state(inner_cfg)
            if snap.next and getattr(snap, "interrupts", None):
                # The inner agent is paused on an ask_user from a prior pass.
                request = snap.interrupts[0].value
                if clarify_resume is None:
                    # Outer graph re-ran before interrupt() returned the answer;
                    # re-surface the same request (contract §2 re-execution).
                    return {"outcome": "clarify", "clarification": request}
                parsed = parse_response(clarify_resume)
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
                    events.final(ans)
                    return {"answer": ans, "outcome": "refuse"}
                # Resume the paused inner agent with the user's answer.
                agent_input = Command(resume=clarify_resume)

        try:
            final = _stream_agent(agent, agent_input, inner_cfg)
        except GovernanceHardStop as e:
            ledger = list(e.ledger)
            entry = e.entry
            ans = refusal(
                escalation=_ESCALATION_GUARDRAIL,
                provenance={
                    **state["base_provenance"],
                    "refused_by": "guardrail",
                    "failed_layer": entry.get("layer"),
                    "reason": entry.get("reason"),
                    "sql": entry.get("sql"),
                    "governance_ledger": ledger,
                },
            )
            events.final(ans)
            return {"answer": ans, "outcome": "refuse"}
        except GraphRecursionError as e:
            # Step budget exhausted without a final answer → fail closed (§6),
            # never crash the caller (the eval arm / a live turn). Recover the
            # accumulated ledger from the exhausted stream (attached by
            # `_stream_agent`) so the refusal still carries its real audit trail
            # and attempt count, not an empty placeholder (Inv #10).
            partial = getattr(e, "partial_state", None) or {}
            ledger = list(partial.get("ledger") or [])
            attempts = sum(1 for x in ledger if x.get("action") == "run_query")
            ans = _finish_unsuccessful(
                settings=settings,
                gateway=gateway,
                identity=identity,
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
                on_event=None,
                allowlist=allowlist,
                dialect=dialect,
                default_schema=default_schema,
            )
            events.final(ans)
            return {"answer": ans, "outcome": "refuse"}

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
            final, corpus=corpus, dialect=dialect, default_schema=default_schema
        )
        if not sql or pass_entry is None:
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
            ans = _finish_unsuccessful(
                settings=settings,
                gateway=gateway,
                identity=identity,
                last_refusal=last_refusal,
                attempts=attempts or 0,
                base_provenance={**base_provenance, "governance_ledger": ledger},
                question=question,
                narrator=None,  # narration deferred to narrate_node
                on_event=None,
                allowlist=allowlist,
                dialect=dialect,
                default_schema=default_schema,
            )
            if ans.provenance.get("governance_ledger") is None:
                ans = replace(
                    ans,
                    provenance={**ans.provenance, "governance_ledger": ledger},
                )
            events.final(ans)
            return {"answer": ans, "outcome": "refuse"}

        result = result_from_ledger(pass_entry)
        if result is None:
            # Should not happen for a pass entry; fail closed rather than re-execute.
            ans = _finish_unsuccessful(
                settings=settings,
                gateway=gateway,
                identity=identity,
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
                on_event=None,
                allowlist=allowlist,
                dialect=dialect,
                default_schema=default_schema,
            )
            events.final(ans)
            return {"answer": ans, "outcome": "refuse"}

        generated = GeneratedSql(
            sql=sql,
            tables_used=tables_used,
            metric_id=None,
        )
        attempts = sum(1 for e in ledger if e.get("action") == "run_query")
        # Cache licensed set = physical names of tables the SQL actually touched.
        licensed_phys = frozenset(licensed_physical_names(corpus, tables_used))
        ans = _finalize_success(
            question=question,
            graph=graph_obj,
            generated=generated,
            result=result,
            attempts=attempts,
            base_provenance=base_provenance,
            dialect=dialect,
            allowlist=allowlist,
            licensed=licensed_phys,
            cache=cache,
            narrator=None,  # narration deferred to narrate_node
            on_event=None,
            ledger=ledger,
        )
        events.final(ans)
        return {"answer": ans, "outcome": "finalize"}

    def narrate_node(state: ServeRailsState) -> dict:
        """Phrase the delivered answer into grounded English — a first-class graph
        step so the narrator's model call is one trace span under the turn (not a
        side call inside finalization). No-op for refusals / cache-miss passthrough
        (no ``answer`` with a result grid) and when no narrator is configured; a
        narrator failure keeps the deterministic finalizer text (see
        ``narrate_answer``)."""
        answer = state.get("answer")
        if answer is None:
            return {}
        narrated = narrate_answer(answer, state["question"], narrator)
        if narrated is answer:
            return {}
        return {"answer": narrated}

    builder = StateGraph(ServeRailsState)
    builder.add_node("ingest", ingest)
    builder.add_node("refuse_gate", refuse_gate)
    builder.add_node("cache", cache_lookup)
    builder.add_node("assemble", assemble)
    builder.add_node("agent_core", agent_core_node)
    builder.add_node("narrate", narrate_node)
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "refuse_gate")
    builder.add_conditional_edges("refuse_gate", after_refuse, ["cache", END])
    builder.add_conditional_edges("cache", after_cache, ["assemble", "narrate"])
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
    cache: "SqlCache | None" = None,
    working_memory: "WorkingMemory | None" = None,
    narrator: "AnswerNarrator | None" = None,
    on_event: "Callable[[dict], None] | None" = None,
    clarify_checkpointer: Any = None,
    clarify_thread: str | None = None,
    clarify_resume: Any = None,
) -> "Answer | ClarificationPending":
    """Run one question through the agentic serve rails.

    Returns an ``Answer`` normally, or a :class:`ClarificationPending` when the
    inner agent paused on ``ask_user`` (HITL, contract §2). Clarification is active
    only when ``clarify_checkpointer`` is passed; the eval path calls this without
    it and always gets an ``Answer``.
    """
    graph = build_serve_rails(
        corpus=corpus,
        gateway=gateway,
        settings=settings,
        identity=identity,
        model=model,
        embedder=embedder,
        cache=cache,
        working_memory=working_memory,
        narrator=narrator,
        on_event=on_event,
        session_id=session_id,
        clarify_checkpointer=clarify_checkpointer,
        clarify_thread=clarify_thread,
        clarify_resume=clarify_resume,
    )
    final = graph.invoke(
        {
            "question": question,
            "session_id": session_id,
        },
        config={"callbacks": tracing_callbacks()},  # Langfuse; [] when unconfigured
    )
    if final.get("outcome") == "clarify":
        return ClarificationPending(final.get("clarification") or {})
    answer = final.get("answer")
    if answer is None:
        return refusal(
            escalation=_ESCALATION_NO_COVERAGE,
            provenance={"refused_by": "no_coverage", "session_id": session_id},
        )
    return answer
