"""The deterministic serve DAG (Server flow; Architecture section 6).

Wires the stages into a hard-wired, auditable pipeline with conditional routing:

    question -> query understanding + term binding -> intent route -> refuse-gate ->
    RVGD retrieval -> SQL gen -> Steiner join plan -> five-layer guardrails ->
    execute (as-user) -> answer + reliability stamp

The refuse-gate (D5) runs alongside the hard guardrails: a curated
``negative_example`` match or any guardrail veto ends in a fail-closed refusal,
never a confident wrong number. SQL generation is a pluggable seam
(:class:`~governed_bi.server.sqlgen.SqlGenerator`); the default deterministic
generator handles metric / KPI questions so the whole path runs without a model.

Shared governance helpers live in :mod:`governed_bi.server.governance` and are
re-exported here so existing imports (``graph.py``, tests) keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..gateway import check, column_allowlist
from ..graph import build_graph, detect_missing_join_path, plan_joins
from ..retrieval import filter_corpus_for_retrieval, retrieve, route_schemas
from .answer import refusal
from .context import assemble_context
from .governance import (
    LICENSE_JOIN_HOPS,
    MAX_REPAIR_ATTEMPTS,
    _ESCALATION_EXECUTION,
    _ESCALATION_GUARDRAIL,
    _ESCALATION_MISSING_EDGE,
    _ESCALATION_NO_COVERAGE,
    _HARD_REFUSE_LAYERS,
    _NON_REPAIRABLE_LAYERS,
    _answer_text,
    _emit,
    _execution_feedback,
    _finalize_success,
    _finish_unsuccessful,
    _guardrail_feedback,
    _licensed_table_ids,
    _match_negative_example,
    _repairable_guardrail,
    _result_table,
    _suspect_in_scope,
    _try_cache_hit,
    missing_edge_refusal,
)
from .routing import bind_terms, route_intent
from .sqlgen import RepairFeedback, SqlGenerator, TemplateSqlGenerator

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from ..llm import Embedder
    from ..memory import WorkingMemory
    from .cache import SqlCache
    from .narrate import AnswerNarrator

__all__ = [
    "LICENSE_JOIN_HOPS",
    "MAX_REPAIR_ATTEMPTS",
    "_ESCALATION_EXECUTION",
    "_ESCALATION_GUARDRAIL",
    "_ESCALATION_MISSING_EDGE",
    "_ESCALATION_NO_COVERAGE",
    "_HARD_REFUSE_LAYERS",
    "_NON_REPAIRABLE_LAYERS",
    "_answer_text",
    "_emit",
    "_execution_feedback",
    "_finalize_success",
    "_finish_unsuccessful",
    "_guardrail_feedback",
    "_licensed_table_ids",
    "_match_negative_example",
    "_repairable_guardrail",
    "_result_table",
    "_suspect_in_scope",
    "_try_cache_hit",
    "answer_question",
    "missing_edge_refusal",
]


def answer_question(
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
    on_event: "Callable[[dict], None] | None" = None,
):
    """Run one question through the serve DAG, fail-closed on any guardrail or
    refuse-gate hit. ``corpus`` should be the ``for_server()`` view.

    ``sql_generator`` defaults to the deterministic template generator; an
    enterprise deployment injects a model-backed one implementing the same
    ``SqlGenerator`` protocol. ``embedder`` (optional) turns on the retrieval
    vector channel (BM25 + embedding cosine, fused); with none, retrieval is
    pure lexical BM25. ``cache`` (optional) is the semantic SQL cache: a hit
    (after the refuse-gate) re-guardrails + re-executes stored SQL and skips
    retrieval/planning/generation; a clean answer is written back on a miss.
    This is the **conversational** serve entry: each call is one turn of a
    session. ``working_memory`` (D8) supplies the prior turns of this
    ``session_id``; when present they are injected into the prompt context so a
    follow-up can resolve references. This function only *reads* the history - the
    caller records the new turn after receiving the answer, so the current
    question is never double-counted as prior context. Omit ``working_memory`` for
    a **single-round** call (each question independent) - the shape the eval
    harness uses; every other caller threads a session.
    ``narrator`` (optional) phrases the executed result into natural language for
    the answer text; with none, the compact deterministic render is used. Either
    way the executed rows are carried on ``Answer.result`` for display/audit.
    ``on_event`` (optional) receives a small ``{"stage": ...}`` dict at each
    pipeline stage (route / refuse_gate / cache_hit / retrieve / generate /
    guardrail / execute / compose) for live progress; it is best-effort and never
    changes the answer (see :func:`_emit`).
    """
    route = route_intent(question)
    bound_terms = bind_terms(corpus, question)
    base_provenance: dict = {
        "route": route.value,
        "bound_terms": bound_terms,
        "session_id": session_id,
        "user": identity.user,
    }
    _emit(on_event, "route", route=route.value)

    # Refuse-gate (D5): a curated negative example ends the flow immediately.
    negative = _match_negative_example(corpus, question)
    if negative is not None:
        _emit(on_event, "refuse_gate", negative_example=negative.id)
        return refusal(
            escalation=negative.escalation,
            provenance={**base_provenance, "refused_by": "refuse_gate", "negative_example": negative.id},
        )

    dialect = gateway.catalog().dialect.value
    # D15: Postgres/Redshift default to multi-schema (qualified SQL + guardrails).
    # SQLite stays single-schema so the BIRD graded path is unchanged.
    multi_schema = settings.datasource.is_multi_schema()
    default_schema = settings.datasource.schema if multi_schema else None
    allowlist = column_allowlist(corpus, multi_schema=multi_schema)
    graph = build_graph(corpus)

    # SQL semantic-cache fast path (D7): a hit re-guardrails + re-executes stored
    # SQL (and re-derives the stamp from the current graph), skipping retrieval /
    # generation. A stale or now-blocked hit falls through (fail-closed).
    if cache is not None:
        hit = _try_cache_hit(
            cache, question, gateway, identity, settings, allowlist, dialect, graph, base_provenance,
            multi_schema=multi_schema,
            default_schema=default_schema,
            narrator=narrator,
            on_event=on_event,
        )
        if hit is not None:
            return hit

    _emit(on_event, "retrieve")
    # D15: on multi-schema, shortlist schemas then expand along curated joins
    # before RVGD so bridge tables in un-mentioned schemas are not dropped.
    retrieval_corpus = corpus
    routed_schemas: frozenset[str] | None = None
    if multi_schema:
        routed_schemas = route_schemas(corpus, question, embedder=embedder)
        retrieval_corpus = filter_corpus_for_retrieval(corpus, routed_schemas)
        _emit(on_event, "schema_route", schemas=sorted(routed_schemas))
        base_provenance = {
            **base_provenance,
            "routed_schemas": sorted(routed_schemas),
        }
    retrieval = retrieve(retrieval_corpus, question, embedder=embedder)

    generator = sql_generator or TemplateSqlGenerator(multi_schema=multi_schema)

    # D15 missing-edge: cross-schema retrieval with no curated join path refuses
    # before generate (do not invent a relationship). Within-schema disconnects
    # still fall through to the repair / no_coverage path.
    missing = detect_missing_join_path(
        corpus, graph, set(retrieval.table_ids), multi_schema=multi_schema
    )
    if missing is not None:
        _emit(on_event, "missing_edge", schemas=sorted(missing.schemas))
        return missing_edge_refusal(base_provenance, missing)

    # L4 licensing scope: retrieval's tables, the Steiner points needed to connect
    # THEM, and their FK join-neighborhood (decoupling L4 from retrieval recall).
    # Planned over retrieval, never the generator's declared tables, so a
    # rogue/hallucinating generator cannot self-authorize an off-scope table.
    # Question-scoped, so it is computed once and reused across repair attempts.
    try:
        licensing_join_ids = plan_joins(graph, set(retrieval.table_ids)).join_ids
    except ValueError:
        licensing_join_ids = []
    licensed_ids = _licensed_table_ids(corpus, graph, retrieval, licensing_join_ids)

    # Resolve the licensed scope into a prompt context (schema, joins, caveats,
    # skills, exemplars) the generator reads. The guardrail's allowed_tables is
    # derived from it, so what the model can see == what L4 permits. Prior
    # conversation turns (D8 working memory, if supplied) are injected so a
    # follow-up can resolve references; the caller records this turn afterward.
    history = working_memory.history(session_id) if working_memory is not None else ()
    context = assemble_context(
        corpus, retrieval, licensed_table_ids=licensed_ids, history=history, multi_schema=multi_schema
    )
    licensed = context.allowed_table_names()

    # Bounded self-repair loop: generate -> guardrail -> execute. A guardrail
    # rejection or an execution error is handed back to the generator (as
    # RepairFeedback) for another attempt rather than refusing outright; every
    # attempt is re-guardrailed, so un-vetted SQL never executes. Stops early when
    # the generator cannot improve (repeats a SQL), and fails closed after the cap.
    feedback: list[RepairFeedback] = []
    seen_sql: set[str] = set()
    last_refusal: dict = {"refused_by": "no_coverage", "escalation": _ESCALATION_NO_COVERAGE}
    attempts = 0
    coverage_best_effort = False

    while attempts < MAX_REPAIR_ATTEMPTS:
        _emit(on_event, "generate", attempt=attempts + 1)
        generated = generator.generate(
            question, retrieval, corpus, feedback=tuple(feedback), context=context
        )
        attempts += 1
        # §6: a bare coverage decline has no SQL to grade — force one best-effort
        # emit (decline disallowed) so deliver-and-grade can still run.
        if (
            generated is None
            and settings.grade_semantic_failures
            and not coverage_best_effort
            and not last_refusal.get("sql")
        ):
            coverage_best_effort = True
            _emit(on_event, "coverage_best_effort")
            feedback.append(
                RepairFeedback(
                    sql="",
                    stage="coverage",
                    reason=(
                        "Prior decline is not allowed under deliver-and-grade. "
                        "Emit a best-effort read-only SELECT from the licensed "
                        "tables; the answer will be marked unverified."
                    ),
                )
            )
            generated = generator.generate(
                question,
                retrieval,
                corpus,
                feedback=tuple(feedback),
                context=context,
                allow_decline=False,
            )
            if generated is None:
                break
        elif generated is None:
            break  # the generator declined; keep the most informative refusal so far
        if generated.sql in seen_sql:
            break  # no progress on the feedback; stop repairing
        seen_sql.add(generated.sql)

        verdict = check(
            generated.sql,
            allowed_columns=set(allowlist.allowed),
            suspect_columns=allowlist.suspect,
            allowed_tables=licensed,
            hard_block_suspect=settings.hard_block_suspect_columns,
            dialect=dialect,
            multi_schema=multi_schema,
            default_schema=default_schema,
        )
        _emit(
            on_event,
            "guardrail",
            attempt=attempts,
            passed=verdict.passed,
            failed_layer=verdict.failed_layer.value if verdict.failed_layer else None,
        )
        if not verdict.passed:
            fb, last_refusal = _guardrail_feedback(generated, verdict)
            if coverage_best_effort:
                last_refusal = {**last_refusal, "coverage_best_effort": True}
            if not _repairable_guardrail(verdict):
                break  # hard policy block: fail closed, don't coach a retry
            feedback.append(fb)
            continue

        _emit(on_event, "execute", attempt=attempts)
        try:
            result = gateway.execute(generated.sql, identity)
        except Exception as err:  # give the generator a chance to repair, then fail closed
            fb, last_refusal = _execution_feedback(generated, err)
            if coverage_best_effort:
                last_refusal = {**last_refusal, "coverage_best_effort": True}
            feedback.append(fb)
            continue

        return _finalize_success(
            question=question,
            graph=graph,
            generated=generated,
            result=result,
            attempts=attempts,
            base_provenance=base_provenance,
            dialect=dialect,
            allowlist=allowlist,
            licensed=licensed,
            cache=cache,
            narrator=narrator,
            on_event=on_event,
            coverage_best_effort=coverage_best_effort,
        )

    # Exhausted attempts (or generator declined): §6 graded delivery or hard refuse.
    return _finish_unsuccessful(
        settings=settings,
        gateway=gateway,
        identity=identity,
        last_refusal=last_refusal,
        attempts=attempts,
        base_provenance=base_provenance,
        question=question,
        narrator=narrator,
        on_event=on_event,
    )
