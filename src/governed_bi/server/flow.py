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
from ..graph import build_graph, plan_joins
from ..retrieval import retrieve
from .answer import LOW_CONFIDENCE_JOIN, Answer, UncertaintySignals, assemble, refusal
from .routing import bind_terms, route_intent
from .sqlgen import SqlGenerator, TemplateSqlGenerator

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity

from ..corpus.schemas import JoinAsset, NegativeExampleAsset, TableAsset

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


def _physical(corpus: "Corpus", table_id: str) -> str | None:
    asset = corpus.by_id(table_id)
    return asset.physical_name if isinstance(asset, TableAsset) else None


def _licensed_tables(corpus, retrieval, join_ids) -> frozenset[str]:
    """Physical names the query is licensed to touch (the L4 term-semantics set).

    The retrieval scope (candidate tables, which already include a bound metric's
    base table via grounding) plus any Steiner-point tables the join plan bridges
    through. Deliberately excludes the generator's self-declared tables so a rogue
    generator cannot authorize a table retrieval never surfaced.
    """
    table_ids: set[str] = set(retrieval.table_ids)
    for join_id in join_ids:
        join = corpus.by_id(join_id)
        if isinstance(join, JoinAsset):
            table_ids.add(join.left_table)
            table_ids.add(join.right_table)
    return frozenset(p for tid in table_ids if (p := _physical(corpus, tid)) is not None)


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
) -> "Answer":
    """Run one question through the serve DAG, fail-closed on any guardrail or
    refuse-gate hit. ``corpus`` should be the ``for_server()`` view.

    ``sql_generator`` defaults to the deterministic template generator; an
    enterprise deployment injects a model-backed one implementing the same
    ``SqlGenerator`` protocol.
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

    retrieval = retrieve(corpus, question)

    generator = sql_generator or TemplateSqlGenerator()
    generated = generator.generate(question, retrieval, corpus)
    if generated is None:
        return refusal(
            escalation=_ESCALATION_NO_COVERAGE,
            provenance={**base_provenance, "refused_by": "no_coverage"},
        )

    graph = build_graph(corpus)

    # L4 licensing scope: retrieval's tables plus the Steiner points needed to
    # connect THEM. Planned over retrieval, never the generator's declared tables,
    # so a rogue/hallucinating generator cannot self-authorize an off-scope table.
    try:
        licensing_plan = plan_joins(graph, set(retrieval.table_ids))
        licensing_join_ids = licensing_plan.join_ids
    except ValueError:
        licensing_join_ids = []
    licensed = _licensed_tables(corpus, retrieval, licensing_join_ids)

    # Reliability stamp: confidence of the joins the generated SQL actually needs
    # (best-effort; a lone or non-FK-connected table simply yields no plan).
    try:
        stamp_plan = plan_joins(graph, set(generated.tables_used))
        join_ids, min_confidence = stamp_plan.join_ids, stamp_plan.min_confidence
    except ValueError:
        join_ids, min_confidence = [], 1.0

    dialect = gateway.catalog().dialect.value
    allowlist = column_allowlist(corpus)

    verdict = check(
        generated.sql,
        allowed_columns=set(allowlist.allowed),
        suspect_columns=allowlist.suspect,
        allowed_tables=licensed,
        hard_block_suspect=settings.hard_block_suspect_columns,
        dialect=dialect,
    )
    if not verdict.passed:
        return refusal(
            escalation=_ESCALATION_GUARDRAIL,
            provenance={
                **base_provenance,
                "refused_by": "guardrail",
                "failed_layer": verdict.failed_layer.value if verdict.failed_layer else None,
                "reason": verdict.reason,
                "sql": generated.sql,
            },
        )

    try:
        result = gateway.execute(generated.sql, identity)
    except Exception as err:  # fail-closed on any execution error
        return refusal(
            escalation=_ESCALATION_EXECUTION,
            provenance={**base_provenance, "refused_by": "execution", "error": str(err), "sql": generated.sql},
        )

    signals = UncertaintySignals(
        low_confidence_join=min_confidence < LOW_CONFIDENCE_JOIN,
        suspect_in_scope=_suspect_in_scope(generated.sql, allowlist.suspect, dialect),
    )
    provenance = {
        **base_provenance,
        "metric_id": generated.metric_id,
        "tables_used": sorted(generated.tables_used),
        "join_ids": join_ids,
        "min_join_confidence": min_confidence,
        "row_count": result.row_count,
        "truncated": result.truncated,
    }
    return assemble(text=_render(result, generated), sql=generated.sql, signals=signals, provenance=provenance)
