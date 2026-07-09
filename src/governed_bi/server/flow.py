"""The deterministic serve DAG (Server flow; Architecture section 6).

Wires the stages into a hard-wired, auditable pipeline with conditional routing:

    ask -> query understanding + term binding -> intent route -> refuse-gate ->
    RVGD retrieval -> SQL gen -> Steiner join plan -> five-layer guardrails ->
    execute (as-user) -> answer + reliability stamp

The refuse-gate (D5) runs alongside the hard guardrails: a curated
``negative_example`` match or any guardrail veto ends in a fail-closed refusal,
never a confident wrong number. SQL generation is a pluggable seam
(:class:`~governed_bi.server.sqlgen.SqlGenerator`); the default deterministic
generator handles metric / KPI questions so the whole path runs without a model.

This is the deterministic core. The full design fronts it with a LangGraph DAG
and middleware (``before_model`` / ``wrap_tool_call``), the semantic SQL cache
(``server.cache``), and working memory (D8); those wrap this function, they do
not change its contract.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from ..gateway import check, column_allowlist
from ..graph import build_graph, join_neighborhood, plan_joins
from ..retrieval import retrieve
from .answer import LOW_CONFIDENCE_JOIN, Answer, UncertaintySignals, assemble, refusal
from .context import assemble_context
from .routing import bind_terms, route_intent
from .sqlgen import RepairFeedback, SqlGenerator, TemplateSqlGenerator

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from ..llm import Embedder

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
) -> "Answer":
    """Run one question through the serve DAG, fail-closed on any guardrail or
    refuse-gate hit. ``corpus`` should be the ``for_server()`` view.

    ``sql_generator`` defaults to the deterministic template generator; an
    enterprise deployment injects a model-backed one implementing the same
    ``SqlGenerator`` protocol. ``embedder`` (optional) turns on the retrieval
    vector channel (BM25 + embedding cosine, fused); with none, retrieval is
    pure lexical BM25.
    """
    route = route_intent(question)
    bound_terms = bind_terms(corpus, question)
    base_provenance: dict = {
        "route": route.value,
        "bound_terms": bound_terms,
        "session_id": session_id,
        "user": identity.user,
    }

    # Refuse-gate (D5): a curated negative example ends the flow immediately.
    negative = _match_negative_example(corpus, question)
    if negative is not None:
        return refusal(
            escalation=negative.escalation,
            provenance={**base_provenance, "refused_by": "refuse_gate", "negative_example": negative.id},
        )

    retrieval = retrieve(corpus, question, embedder=embedder)

    generator = sql_generator or TemplateSqlGenerator()
    graph = build_graph(corpus)
    dialect = gateway.catalog().dialect.value
    allowlist = column_allowlist(corpus)

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
    # derived from it, so what the model can see == what L4 permits.
    context = assemble_context(corpus, retrieval, licensed_table_ids=licensed_ids)
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

    while attempts < MAX_REPAIR_ATTEMPTS:
        generated = generator.generate(
            question, retrieval, corpus, feedback=tuple(feedback), context=context
        )
        attempts += 1
        if generated is None:
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
        )
        if not verdict.passed:
            layer = verdict.failed_layer.value if verdict.failed_layer else None
            feedback.append(
                RepairFeedback(sql=generated.sql, stage="guardrail", reason=f"{layer}: {verdict.reason}")
            )
            last_refusal = {
                "refused_by": "guardrail",
                "escalation": _ESCALATION_GUARDRAIL,
                "failed_layer": layer,
                "reason": verdict.reason,
                "sql": generated.sql,
            }
            continue

        try:
            result = gateway.execute(generated.sql, identity)
        except Exception as err:  # give the generator a chance to repair, then fail closed
            feedback.append(RepairFeedback(sql=generated.sql, stage="execution", reason=str(err)))
            last_refusal = {
                "refused_by": "execution",
                "escalation": _ESCALATION_EXECUTION,
                "error": str(err),
                "sql": generated.sql,
            }
            continue

        # Success. The stamp reflects the joins the executed SQL actually needs and
        # whether it took a repair to get here (a repaired answer is lineage, not
        # governed).
        try:
            stamp_plan = plan_joins(graph, set(generated.tables_used))
            join_ids, min_confidence = stamp_plan.join_ids, stamp_plan.min_confidence
        except ValueError:
            join_ids, min_confidence = [], 1.0

        signals = UncertaintySignals(
            low_confidence_join=min_confidence < LOW_CONFIDENCE_JOIN,
            suspect_in_scope=_suspect_in_scope(generated.sql, allowlist.suspect, dialect),
            repaired=attempts > 1,
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
        }
        return assemble(
            text=_render(result, generated), sql=generated.sql, signals=signals, provenance=provenance
        )

    # Exhausted the attempts (or the generator declined / could not improve): fail
    # closed with the most recent failure reason.
    escalation = last_refusal.pop("escalation")
    return refusal(escalation=escalation, provenance={**base_provenance, **last_refusal, "attempts": attempts})
