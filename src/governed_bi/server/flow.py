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

This is the deterministic core. The LangGraph harness that fronts it is built in
``server.graph`` (a StateGraph DAG whose nodes reuse the helpers here, with
``before_model`` / ``wrap_tool_call`` middleware realized as the context and
guardrail nodes); ``answer_question_graph`` there is Answer-equivalent to this
function. The semantic SQL cache (``server.cache``) is wired in here; working
memory (D8) still wraps this contract without changing it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from ..gateway import GuardrailLayer, check, column_allowlist
from ..graph import build_graph, detect_missing_join_path, join_neighborhood, plan_joins
from ..retrieval import filter_corpus_for_retrieval, retrieve, route_schemas
from .answer import (
    LOW_CONFIDENCE_JOIN,
    RESULT_PREVIEW_ROWS,
    Answer,
    ResultTable,
    SemanticAssurance,
    UncertaintySignals,
    assemble,
    graded_delivery,
    refusal,
)
from .context import assemble_context
from .routing import bind_terms, route_intent
from .sqlgen import RepairFeedback, SqlGenerator, TemplateSqlGenerator

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity, QueryResult
    from ..graph import MissingJoinPath
    from ..llm import Embedder
    from ..memory import WorkingMemory
    from .cache import SqlCache
    from .narrate import AnswerNarrator

from ..corpus.schemas import JoinAsset, NegativeExampleAsset, TableAsset

# How many times SQL generation may be retried within one turn before failing
# closed. Each retry feeds the prior failure (guardrail reason / execution error)
# back to the generator. Tune against the eval.
MAX_REPAIR_ATTEMPTS = 3

# How many FK-join hops out from the retrieved tables L4 licensing extends (see
# _licensed_tables). 1 admits a retrieved table's direct FK neighbors; raising it
# widens the licensed table scope further. Tunable heuristic; tune against the
# eval's false_refusal_rate vs. any scope-widening cost.
LICENSE_JOIN_HOPS = 1

# Canned escalation blobs for the fail-closed paths (D5).
_ESCALATION_NO_COVERAGE = (
    "This question is outside the governed semantic layer. "
    "Contact the data owner to add coverage."
)
_ESCALATION_GUARDRAIL = (
    "The generated query was blocked by a safety guardrail. "
    "Rephrase the question or contact the data owner."
)
_ESCALATION_EXECUTION = (
    "The query could not be executed against the database. "
    "Contact the data owner if this persists."
)
_ESCALATION_MISSING_EDGE = (
    "No curated relationship connects the schemas needed for this question. "
    "Contact the data owner to declare a cross-schema join."
)

# Refuse-gate tuning: how much a question must overlap a curated example to count
# as a match, and the stop-words dropped when keying on a negative pattern.
_REFUSE_JACCARD = 0.6
_STOPWORDS = frozenset(
    "a an and are about as at be by do does for from how in is many much of on or "
    "the their there to what when where which who why with work works".split()
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _match_negative_example(corpus: "Corpus", question: str) -> "NegativeExampleAsset | None":
    """Return the first curated negative example the question matches, else None.

    Matches on either a distinctive keyword from the pattern (content words, stop
    words removed) or a high token-overlap with one of the example questions.
    Deterministic and conservative to avoid refusing a legitimate question.
    """
    q_tokens = set(_tokens(question))
    if not q_tokens:
        return None
    for asset in corpus.assets:
        if not isinstance(asset, NegativeExampleAsset):
            continue
        keywords = {t for t in _tokens(asset.pattern) if t not in _STOPWORDS}
        if q_tokens & keywords:
            return asset
        for example in asset.example_questions:
            ex_tokens = set(_tokens(example))
            if not ex_tokens:
                continue
            jaccard = len(q_tokens & ex_tokens) / len(q_tokens | ex_tokens)
            if jaccard >= _REFUSE_JACCARD:
                return asset
    return None


def _licensed_table_ids(corpus, graph, retrieval, join_ids, *, hops=LICENSE_JOIN_HOPS) -> frozenset[str]:
    """Table-asset ids the query is licensed to touch (the L4 term-semantics set).

    The union of three sources:
      1. the retrieval scope (candidate tables, which already include a bound
         metric's base table via grounding);
      2. the join endpoints of ``join_ids`` (the Steiner points the plan bridges
         through to connect the retrieved tables);
      3. the FK ``join_neighborhood`` of the retrieved tables, ``hops`` deep.

    Deliberately excludes the generator's self-declared tables so a rogue
    generator cannot authorize a table retrieval never surfaced. The physical-name
    ``allowed_tables`` the guardrail uses is derived from the assembled context
    (``PromptContext.allowed_table_names``) so the model sees exactly what L4 will
    permit.

    Decoupling L4 from retrieval recall (source 3): the lexical retriever can miss
    a table the correct answer needs; licensing the retrieved tables' FK neighbors
    means such a table is not refused just because retrieval under-recalled. This
    is safe because L3 (``column_allowlist``) still guards every column
    independently: only non-excluded, non-suspect columns are ever allowed, so
    reaching a FK-neighbor table exposes only its already-allowed columns and never
    leaks excluded/suspect data. It only widens which related tables' allowed
    columns are reachable, not what any single table exposes. A table beyond
    ``hops`` of (or disconnected from) every retrieved table stays out of scope and
    is still blocked at L4.
    """
    table_ids: set[str] = set(retrieval.table_ids)
    for join_id in join_ids:
        join = corpus.by_id(join_id)
        if isinstance(join, JoinAsset):
            table_ids.add(join.left_table)
            table_ids.add(join.right_table)
    table_ids |= join_neighborhood(graph, set(retrieval.table_ids), hops=hops)
    return frozenset(tid for tid in table_ids if isinstance(corpus.by_id(tid), TableAsset))


def missing_edge_refusal(
    base_provenance: dict,
    missing: "MissingJoinPath",
) -> Answer:
    """Fail-closed Answer for a D15 cross-schema missing curated join."""
    return refusal(
        escalation=_ESCALATION_MISSING_EDGE,
        provenance={
            **base_provenance,
            "refused_by": "missing_edge",
            "table_ids": sorted(missing.table_ids),
            "schemas": sorted(missing.schemas),
            "reason": missing.reason,
            "clarification_hint": {
                "kind": "missing_cross_schema_join",
                "schemas": sorted(missing.schemas),
                "table_ids": sorted(missing.table_ids),
            },
        },
    )


def _suspect_in_scope(sql: str, suspect: frozenset[str], dialect: str | None) -> bool:
    """Whether the SQL references a curator-flagged suspect column (by bare name).

    Only meaningful in prod, where suspect columns are permitted; in dev L3 has
    already hard-blocked them. Approximate (bare name, not table-qualified), which
    is sufficient for lowering the reliability stamp.
    """
    if not suspect:
        return False
    suspect_bare = {ref.split(".", 1)[1] for ref in suspect}
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return False
    return any(col.name in suspect_bare for col in tree.find_all(exp.Column))


def _render(result, generated) -> str:
    """A compact textual answer from the executed result."""
    if result.row_count == 1 and len(result.columns) == 1:
        return f"{result.columns[0]} = {result.rows[0][0]}"
    if result.row_count == 0:
        return "no rows"
    head = ", ".join(result.columns)
    suffix = " (truncated)" if result.truncated else ""
    return f"{result.row_count} row(s) over [{head}]{suffix}"


def _result_table(result: "QueryResult") -> ResultTable:
    """A bounded, display-ready snapshot of the executed result rows.

    Clipped to ``RESULT_PREVIEW_ROWS`` so a wide result never bloats the Answer;
    ``truncated`` reflects the gateway cap or this preview cap.
    """
    rows = list(result.rows[:RESULT_PREVIEW_ROWS])
    return ResultTable(
        columns=list(result.columns),
        rows=rows,
        row_count=result.row_count,
        truncated=result.truncated or len(result.rows) > RESULT_PREVIEW_ROWS,
    )


def _answer_text(
    question: str, sql: str, result: "QueryResult", table: ResultTable, narrator: "AnswerNarrator | None"
) -> str:
    """The answer text: a grounded NL phrasing when a narrator is injected, else
    the compact deterministic render. A narrator failure falls back to the render
    so a model hiccup never turns a governed answer into an error."""
    if narrator is None:
        return _render(result, None)
    try:
        return narrator.narrate(question, sql, table)
    except Exception:
        return _render(result, None)


def _emit(on_event: "Callable[[dict], None] | None", stage: str, **detail) -> None:
    """Fire a best-effort stage-progress event (no-op without a callback).

    The serve flow stays authoritative: a callback that raises must never turn a
    governed answer into an error, so failures here are swallowed. The payload is
    a small stable dict ``{"stage": ..., **detail}`` that the LangGraph server
    maps to a labeled UI stage (see docs/langgraph-rework-plan.md). Stages, in
    pipeline order: ``route``, ``refuse_gate``, ``cache_hit``, ``retrieve``,
    ``generate``, ``guardrail``, ``execute``, ``compose``.
    """
    if on_event is None:
        return
    try:
        on_event({"stage": stage, **detail})
    except Exception:
        pass


def _try_cache_hit(
    cache, question, gateway, identity, settings, allowlist, dialect, graph, base_provenance,
    *,
    multi_schema: bool = False,
    default_schema: str | None = None,
    narrator: "AnswerNarrator | None" = None,
    on_event: "Callable[[dict], None] | None" = None,
) -> "Answer | None":
    """Serve a semantic-cache hit, or return None to fall through to the pipeline.

    A hit is re-guardrailed (against the licensed tables stored with it) and
    re-executed for freshness (D7). If the re-check fails - a corpus change now
    blocks the cached SQL - or execution errors, this returns None so the full
    pipeline runs instead. Fail-closed: a stale/blocked cached query is never
    served. The reliability stamp is **re-derived** from the current graph (over
    the stored ``tables_used``), identical to a fresh miss, so it never goes stale.
    """
    entry = cache.lookup(question)
    if entry is None:
        return None
    verdict = check(
        entry.sql,
        allowed_columns=set(allowlist.allowed),
        suspect_columns=allowlist.suspect,
        allowed_tables=entry.licensed_tables,
        hard_block_suspect=settings.hard_block_suspect_columns,
        dialect=dialect,
        multi_schema=multi_schema,
        default_schema=default_schema,
    )
    if not verdict.passed:
        return None
    try:
        result = gateway.execute(entry.sql, identity)
    except Exception:
        return None
    try:
        stamp_plan = plan_joins(graph, set(entry.tables_used))
        join_ids, min_confidence = stamp_plan.join_ids, stamp_plan.min_confidence
    except ValueError:
        join_ids, min_confidence = [], 1.0
    signals = UncertaintySignals(
        low_confidence_join=min_confidence < LOW_CONFIDENCE_JOIN,
        suspect_in_scope=_suspect_in_scope(entry.sql, allowlist.suspect, dialect),
    )
    provenance = {
        **base_provenance,
        "metric_id": entry.metric_id,
        "tables_used": sorted(entry.tables_used),
        "join_ids": join_ids,
        "min_join_confidence": min_confidence,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "cache_hit": True,
    }
    _emit(on_event, "cache_hit", metric_id=entry.metric_id)
    table = _result_table(result)
    text = _answer_text(question, entry.sql, result, table, narrator)
    return assemble(text=text, sql=entry.sql, signals=signals, provenance=provenance, result=table)


def _guardrail_feedback(generated, verdict) -> tuple[RepairFeedback, dict]:
    """The (repair-feedback, refusal-record) pair for a guardrail rejection.

    Shared by the plain loop (``answer_question``) and the LangGraph DAG so both
    describe a block identically.
    """
    layer = verdict.failed_layer.value if verdict.failed_layer else None
    fb = RepairFeedback(sql=generated.sql, stage="guardrail", reason=f"{layer}: {verdict.reason}")
    refusal_record = {
        "refused_by": "guardrail",
        "escalation": _ESCALATION_GUARDRAIL,
        "failed_layer": layer,
        "reason": verdict.reason,
        "sql": generated.sql,
    }
    return fb, refusal_record


# Guardrail failures the repair loop must NOT coach a retry around: feeding a hard
# policy/DDL block back to the generator is just pressure to evade the policy, not
# a fixable syntax slip. Scope failures (L3 column allowlist / L4 term-semantics)
# stay repairable *by decision* (2026-07-09, design-decisions D11): the
# FK-neighborhood + repair loop is the design's deliberate false-refusal-reduction
# mechanism (see guardrails docstring). Only L2 fails closed without a retry.
_NON_REPAIRABLE_LAYERS = frozenset({GuardrailLayer.policy_blacklist})


def _repairable_guardrail(verdict) -> bool:
    """Whether a guardrail rejection may be fed back for another attempt.

    A hard policy block (L2) fails closed immediately; everything else is repaired
    within the attempt cap.
    """
    return verdict.failed_layer not in _NON_REPAIRABLE_LAYERS


def _execution_feedback(generated, err: Exception) -> tuple[RepairFeedback, dict]:
    """The (repair-feedback, refusal-record) pair for an execution error."""
    fb = RepairFeedback(sql=generated.sql, stage="execution", reason=str(err))
    refusal_record = {
        "refused_by": "execution",
        "escalation": _ESCALATION_EXECUTION,
        "error": str(err),
        "sql": generated.sql,
    }
    return fb, refusal_record


_HARD_REFUSE_LAYERS = frozenset({GuardrailLayer.policy_blacklist.value})


def _finish_unsuccessful(
    *,
    settings: "Settings",
    gateway: "Gateway",
    identity: "Identity",
    last_refusal: dict,
    attempts: int,
    base_provenance: dict,
    question: str,
    on_event: "Callable[[dict], None] | None" = None,
) -> "Answer":
    """Hard-refuse safety failures; §6 deliver-and-grade semantic ones when enabled."""
    record = dict(last_refusal)
    escalation = record.pop("escalation", _ESCALATION_NO_COVERAGE)
    refused_by = record.get("refused_by", "no_coverage")
    failed_layer = record.get("failed_layer")
    sql = record.get("sql")
    provenance = {**base_provenance, **record, "attempts": attempts}

    # Safety stays binary hard-reject (pipeline-design §5.2).
    hard = refused_by == "refuse_gate" or failed_layer in _HARD_REFUSE_LAYERS
    if hard or not sql or not settings.grade_semantic_failures:
        _emit(on_event, "refuse", refused_by=refused_by, failed_layer=failed_layer)
        return refusal(escalation=escalation, provenance=provenance)

    # §6: deliver the last generated SQL with unverified assurance. Try to
    # execute for a complete answer; if execute fails, still return the SQL so
    # eval can grade it (and the UI can show an unverified payload).
    _emit(
        on_event,
        "graded_delivery",
        refused_by=refused_by,
        failed_layer=failed_layer,
    )
    try:
        result = gateway.execute(sql, identity)
        table = _result_table(result)
        text = (
            f"(unverified) Executed after semantic failure "
            f"({refused_by}/{failed_layer or 'n/a'})."
        )
        return graded_delivery(
            sql=sql,
            provenance=provenance,
            result=table,
            text=text,
        )
    except Exception as err:
        provenance = {**provenance, "graded_delivery_execute_error": str(err)}
        return graded_delivery(
            sql=sql,
            provenance=provenance,
            result=None,
            text=(
                f"(unverified) SQL retained after semantic failure "
                f"({refused_by}/{failed_layer or 'n/a'}); execution failed."
            ),
        )


def _finalize_success(
    *, question, graph, generated, result, attempts, base_provenance, dialect, allowlist, licensed,
    cache, narrator: "AnswerNarrator | None" = None, on_event: "Callable[[dict], None] | None" = None,
    coverage_best_effort: bool = False,
) -> "Answer":
    """Stamp + assemble a successful answer, and write back a clean one to the cache.

    The stamp reflects the joins the executed SQL actually needs and whether it
    took a repair to get here (a repaired answer is lineage, not governed). Shared
    by ``answer_question`` and the LangGraph DAG so both stamp identically.
    """
    try:
        stamp_plan = plan_joins(graph, set(generated.tables_used))
        join_ids, min_confidence = stamp_plan.join_ids, stamp_plan.min_confidence
    except ValueError:
        join_ids, min_confidence = [], 1.0

    signals = UncertaintySignals(
        low_confidence_join=min_confidence < LOW_CONFIDENCE_JOIN,
        suspect_in_scope=_suspect_in_scope(generated.sql, allowlist.suspect, dialect),
        fenced_raw_fallback=coverage_best_effort,
        repaired=attempts > 1 or coverage_best_effort,
    )
    provenance = {
        **base_provenance,
        "metric_id": generated.metric_id,
        "tables_used": sorted(generated.tables_used),
        "join_ids": join_ids,
        "min_join_confidence": min_confidence,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "attempts": attempts,
        "coverage_best_effort": coverage_best_effort,
    }
    table = _result_table(result)
    _emit(on_event, "compose")
    text = _answer_text(question, generated.sql, result, table, narrator)
    answer = assemble(
        text=text, sql=generated.sql, signals=signals, provenance=provenance, result=table
    )
    # Cache admission gates on the *semantic* axis, never on safety alone: only a
    # ``certified`` answer (clean run, no uncertainty flag) is written back, so a
    # later hit is always high-assurance. Cache SQL text only, never results (D7).
    if cache is not None and answer.semantic_assurance is SemanticAssurance.certified:
        cache.put(
            question,
            generated.sql,
            licensed_tables=licensed,
            tables_used=frozenset(generated.tables_used),
            metric_id=generated.metric_id,
        )
    return answer


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
) -> "Answer":
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
        on_event=on_event,
    )
