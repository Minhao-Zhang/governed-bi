"""The serve harness as a LangGraph StateGraph (Server flow; Architecture section 6).

This is the design's server harness: a **deterministic LangGraph DAG with
conditional routing, never autonomous ReAct** (design-spine #2). It expresses the
exact same pipeline as :func:`governed_bi.server.flow.answer_question` - route,
refuse-gate, cache fast path, retrieve + context, generate, guardrail, execute,
stamp - as graph nodes, with the bounded self-repair loop realized as a graph
cycle (a guardrail rejection or execution error routes back to ``generate`` until
the attempt cap, then fails closed).

**No logic is duplicated.** Every node calls the same tested building blocks the
plain flow uses (``route_intent``, ``retrieve``, ``assemble_context``, ``check``,
``_finalize_success``, ...), so the graph cannot drift from the core; the
equivalence tests assert the two entry points return the same ``Answer``.

Middleware mapping (the design's ``before_model`` / ``wrap_tool_call``): the
``retrieve`` node injects the resolved context before generation (``before_model``),
and the ``guardrail`` node gates the ``execute`` "tool" boundary, where fail-closed
lives (``wrap_tool_call``).

Requires the ``agents`` extra (langgraph). Imported only here, so
``import governed_bi.server`` never needs langgraph; use
``from governed_bi.server.graph import build_serve_graph``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..gateway import check, column_allowlist
from ..graph import build_graph, plan_joins
from ..retrieval import retrieve as retrieve_assets
from .answer import refusal
from .context import assemble_context
from .flow import (
    MAX_REPAIR_ATTEMPTS,
    _ESCALATION_NO_COVERAGE,
    _execution_feedback,
    _finalize_success,
    _guardrail_feedback,
    _licensed_table_ids,
    _match_negative_example,
    _repairable_guardrail,
    _try_cache_hit,
)
from .routing import bind_terms, route_intent
from .sqlgen import TemplateSqlGenerator

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from ..llm import Embedder
    from ..memory import WorkingMemory
    from .answer import Answer
    from .cache import SqlCache
    from .narrate import AnswerNarrator
    from .sqlgen import SqlGenerator


class ServeState(TypedDict, total=False):
    """State threaded through the serve graph for one question.

    Per-question inputs (``question``, ``identity``, ``session_id``) are passed at
    invoke; the deployment dependencies (corpus, gateway, settings, generator,
    embedder, cache) are bound into the nodes by :func:`build_serve_graph`.
    """

    question: str
    identity: Any
    session_id: str
    base_provenance: dict
    attempts: int
    feedback: list
    seen_sql: list
    last_refusal: dict
    dialect: str
    allowlist: Any
    graph_obj: Any
    retrieval: Any
    context: Any
    licensed: Any
    generated: Any
    progress: bool
    guard_passed: bool
    guard_repairable: bool
    answer: Any  # the terminal Answer


def build_serve_graph(
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    *,
    sql_generator: "SqlGenerator | None" = None,
    embedder: "Embedder | None" = None,
    cache: "SqlCache | None" = None,
    working_memory: "WorkingMemory | None" = None,
    narrator: "AnswerNarrator | None" = None,
):
    """Compile the serve DAG for a (corpus, gateway, settings, ...) deployment.

    Returns a compiled LangGraph graph; invoke it with
    ``{"question": ..., "identity": ..., "session_id": ...}`` and read
    ``result["answer"]`` (or use :func:`answer_question_graph`). ``corpus`` should
    be the ``for_server()`` view.
    """
    generator = sql_generator or TemplateSqlGenerator()

    # ── Nodes (close over the deployment dependencies) ──

    def ingest(state: ServeState) -> dict:
        question = state["question"]
        route = route_intent(question)
        bound = bind_terms(corpus, question)
        base_provenance = {
            "route": route.value,
            "bound_terms": bound,
            "session_id": state["session_id"],
            "user": state["identity"].user,
        }
        return {
            "base_provenance": base_provenance,
            "attempts": 0,
            "feedback": [],
            "seen_sql": [],
            "last_refusal": {"refused_by": "no_coverage", "escalation": _ESCALATION_NO_COVERAGE},
        }

    def refuse_gate(state: ServeState) -> dict:
        negative = _match_negative_example(corpus, state["question"])
        if negative is not None:
            return {
                "answer": refusal(
                    escalation=negative.escalation,
                    provenance={
                        **state["base_provenance"],
                        "refused_by": "refuse_gate",
                        "negative_example": negative.id,
                    },
                )
            }
        return {}

    def prepare(state: ServeState) -> dict:
        return {
            "dialect": gateway.catalog().dialect.value,
            "allowlist": column_allowlist(corpus),
            "graph_obj": build_graph(corpus),
        }

    def cache_lookup(state: ServeState) -> dict:
        if cache is None:
            return {}
        hit = _try_cache_hit(
            cache,
            state["question"],
            gateway,
            state["identity"],
            settings,
            state["allowlist"],
            state["dialect"],
            state["graph_obj"],
            state["base_provenance"],
            narrator=narrator,
        )
        return {"answer": hit} if hit is not None else {}

    def retrieve_node(state: ServeState) -> dict:
        graph_obj = state["graph_obj"]
        retrieval = retrieve_assets(corpus, state["question"], embedder=embedder)
        try:
            licensing_join_ids = plan_joins(graph_obj, set(retrieval.table_ids)).join_ids
        except ValueError:
            licensing_join_ids = []
        licensed_ids = _licensed_table_ids(corpus, graph_obj, retrieval, licensing_join_ids)
        history = (
            working_memory.history(state["session_id"]) if working_memory is not None else ()
        )
        context = assemble_context(
            corpus, retrieval, licensed_table_ids=licensed_ids, history=history
        )
        return {"retrieval": retrieval, "context": context, "licensed": context.allowed_table_names()}

    def generate_node(state: ServeState) -> dict:
        generated = generator.generate(
            state["question"],
            state["retrieval"],
            corpus,
            feedback=tuple(state["feedback"]),
            context=state["context"],
        )
        attempts = state["attempts"] + 1
        if generated is None:
            return {"generated": None, "attempts": attempts, "progress": False}
        if generated.sql in state["seen_sql"]:  # no progress on the feedback
            return {"generated": generated, "attempts": attempts, "progress": False}
        return {
            "generated": generated,
            "attempts": attempts,
            "seen_sql": state["seen_sql"] + [generated.sql],
            "progress": True,
        }

    def guardrail_node(state: ServeState) -> dict:
        generated = state["generated"]
        verdict = check(
            generated.sql,
            allowed_columns=set(state["allowlist"].allowed),
            suspect_columns=state["allowlist"].suspect,
            allowed_tables=state["licensed"],
            hard_block_suspect=settings.hard_block_suspect_columns,
            dialect=state["dialect"],
        )
        if verdict.passed:
            return {"guard_passed": True}
        fb, last_refusal = _guardrail_feedback(generated, verdict)
        return {
            "guard_passed": False,
            "guard_repairable": _repairable_guardrail(verdict),
            "feedback": state["feedback"] + [fb],
            "last_refusal": last_refusal,
        }

    def execute_node(state: ServeState) -> dict:
        generated = state["generated"]
        try:
            result = gateway.execute(generated.sql, state["identity"])
        except Exception as err:  # give the generator a chance to repair, then fail closed
            fb, last_refusal = _execution_feedback(generated, err)
            return {"feedback": state["feedback"] + [fb], "last_refusal": last_refusal}
        answer = _finalize_success(
            question=state["question"],
            graph=state["graph_obj"],
            generated=generated,
            result=result,
            attempts=state["attempts"],
            base_provenance=state["base_provenance"],
            dialect=state["dialect"],
            allowlist=state["allowlist"],
            licensed=state["licensed"],
            cache=cache,
            narrator=narrator,
        )
        return {"answer": answer}

    def refuse_node(state: ServeState) -> dict:
        last = dict(state["last_refusal"])
        escalation = last.pop("escalation")
        return {
            "answer": refusal(
                escalation=escalation,
                provenance={**state["base_provenance"], **last, "attempts": state["attempts"]},
            )
        }

    # ── Routers (conditional edges implementing the branches + repair cycle) ──

    def after_refuse_gate(state: ServeState):
        return END if state.get("answer") is not None else "prepare"

    def after_cache(state: ServeState):
        return END if state.get("answer") is not None else "retrieve"

    def after_generate(state: ServeState):
        # Declined (None) or no progress on the feedback -> fail closed.
        return "guardrail" if state.get("progress") else "refuse"

    def after_guardrail(state: ServeState):
        if state.get("guard_passed"):
            return "execute"
        if not state.get("guard_repairable"):
            return "refuse"  # hard policy block: fail closed, don't coach a retry
        return "generate" if state["attempts"] < MAX_REPAIR_ATTEMPTS else "refuse"

    def after_execute(state: ServeState):
        if state.get("answer") is not None:
            return END
        return "generate" if state["attempts"] < MAX_REPAIR_ATTEMPTS else "refuse"

    builder = StateGraph(ServeState)
    builder.add_node("ingest", ingest)
    builder.add_node("refuse_gate", refuse_gate)
    builder.add_node("prepare", prepare)
    builder.add_node("cache", cache_lookup)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("generate", generate_node)
    builder.add_node("guardrail", guardrail_node)
    builder.add_node("execute", execute_node)
    builder.add_node("refuse", refuse_node)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "refuse_gate")
    builder.add_conditional_edges("refuse_gate", after_refuse_gate, ["prepare", END])
    builder.add_edge("prepare", "cache")
    builder.add_conditional_edges("cache", after_cache, ["retrieve", END])
    builder.add_edge("retrieve", "generate")
    builder.add_conditional_edges("generate", after_generate, ["guardrail", "refuse"])
    builder.add_conditional_edges("guardrail", after_guardrail, ["execute", "generate", "refuse"])
    builder.add_conditional_edges("execute", after_execute, ["generate", "refuse", END])
    builder.add_edge("refuse", END)
    return builder.compile()


def answer_question_graph(
    question: str,
    identity: "Identity",
    *,
    corpus: "Corpus",
    gateway: "Gateway",
    settings: "Settings",
    session_id: str,
    sql_generator: "SqlGenerator | None" = None,
    embedder: "Embedder | None" = None,
    cache: "SqlCache | None" = None,
    working_memory: "WorkingMemory | None" = None,
    narrator: "AnswerNarrator | None" = None,
) -> "Answer":
    """Build + invoke the serve DAG for one question; return the ``Answer``.

    Signature-compatible with :func:`governed_bi.server.flow.answer_question`, so it
    is a drop-in LangGraph-harnessed alternative. For repeated serving, build the
    graph once with :func:`build_serve_graph` and invoke it per question instead.
    """
    graph = build_serve_graph(
        corpus,
        gateway,
        settings,
        sql_generator=sql_generator,
        embedder=embedder,
        cache=cache,
        working_memory=working_memory,
        narrator=narrator,
    )
    final = graph.invoke(
        {"question": question, "identity": identity, "session_id": session_id}
    )
    return final["answer"]
