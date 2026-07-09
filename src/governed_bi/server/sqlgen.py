"""Server step 7: SQL generation.

The full design layers a system prompt (role -> schema constraint -> safety ->
output) over an LLM to emit **physical (obfuscated) identifiers**. That LLM
generator is the seam defined by the ``SqlGenerator`` protocol.

This module also ships a deterministic ``TemplateSqlGenerator`` that needs no
model: it answers metric / KPI questions by emitting the metric's expression
over its base table. It is intentionally narrow (single-table aggregate, no
grouping); anything it cannot template it declines by returning ``None``, and the
flow fails closed. A generated statement declares the tables it touches so the
flow can plan joins and run the guardrails against the real query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..corpus.schemas import MetricAsset, TableAsset

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..retrieval import RetrievalResult
    from .context import PromptContext


@dataclass(frozen=True)
class GeneratedSql:
    """A generated statement plus the table-asset ids it reads from."""

    sql: str
    tables_used: frozenset[str] = field(default_factory=frozenset)
    metric_id: str | None = None


@dataclass(frozen=True)
class RepairFeedback:
    """Why a prior attempt failed, handed back to the generator for a repair.

    ``stage`` is ``"guardrail"`` (blocked before execution) or ``"execution"``
    (raised when run); ``reason`` is the guardrail layer + message, or the
    database error. A feedback-aware generator uses these to fix its next SQL.
    """

    sql: str
    stage: str
    reason: str


@runtime_checkable
class SqlGenerator(Protocol):
    """Turns a question + retrieval context into a SQL statement, or ``None``.

    Returning ``None`` means "I cannot safely generate this"; the flow then fails
    closed (refuse / clarify) rather than guessing. ``feedback`` carries prior
    failed attempts in this turn so the generator can self-repair; a generator
    that ignores it simply makes a single-shot attempt. ``context`` is the
    resolved :class:`~governed_bi.server.context.PromptContext` (schema, joins,
    caveats, skills) the flow assembled; a model-backed generator reads it, the
    deterministic template does not need it.
    """

    def generate(
        self,
        question: str,
        retrieval: "RetrievalResult",
        corpus: "Corpus",
        *,
        feedback: tuple[RepairFeedback, ...] = (),
        context: "PromptContext | None" = None,
    ) -> GeneratedSql | None:
        ...


def _quote(identifier: str) -> str:
    """Double-quote a physical identifier (``transaction`` is a SQL keyword)."""
    return '"' + identifier.replace('"', '""') + '"'


def _alias(name: str) -> str:
    """A safe column alias from a metric name (``total revenue`` -> ``total_revenue``)."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "value"


class TemplateSqlGenerator:
    """Deterministic generator for single-table metric aggregates.

    Picks the top-ranked metric in the retrieval result, and emits
    ``SELECT <expression> AS <alias> FROM <base table>``. Declines (returns
    ``None``) when no metric was retrieved or its base table is missing.

    It is correct-by-construction for its narrow domain, so it ignores
    ``feedback`` (and ``context``) and makes the same single-shot attempt (the
    repair loop then stops on no-progress). Self-repair is exercised by a
    feedback-aware (model-backed) generator.
    """

    def generate(
        self,
        question: str,
        retrieval: "RetrievalResult",
        corpus: "Corpus",
        *,
        feedback: tuple[RepairFeedback, ...] = (),
        context: "PromptContext | None" = None,
    ) -> GeneratedSql | None:
        metric_ids = getattr(retrieval, "metric_ids", [])
        for metric_id in metric_ids:
            metric = corpus.by_id(metric_id)
            if not isinstance(metric, MetricAsset):
                continue
            base = corpus.by_id(metric.base_table)
            if not isinstance(base, TableAsset):
                continue
            sql = (
                f"SELECT {metric.expression} AS {_alias(metric.name)} "
                f"FROM {_quote(base.physical_name)}"
            )
            return GeneratedSql(
                sql=sql,
                tables_used=frozenset({metric.base_table}),
                metric_id=metric_id,
            )
        return None
