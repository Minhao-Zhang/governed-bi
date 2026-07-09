"""Server step 7: SQL generation.

Two implementations of the ``SqlGenerator`` seam:

- ``LlmSqlGenerator`` (the design-vision generator): layers a system prompt
  (role -> schema constraint -> safety -> output) over a ``ChatClient`` and emits
  **physical (obfuscated) identifiers** from the resolved ``PromptContext``. It is
  feedback-aware, so the flow's bounded self-repair loop can correct a rejected
  attempt. The client is injected, so tests use a scripted ``StaticChatClient``
  and production uses ``OpenAiChatClient``.
- ``TemplateSqlGenerator`` (deterministic, no model): answers metric / KPI
  questions by emitting the metric's expression over its base table. Intentionally
  narrow (single-table aggregate, no grouping); anything it cannot template it
  declines by returning ``None``, and the flow fails closed.

A generated statement declares the tables it touches so the flow can plan joins
(for the reliability stamp) and run the guardrails against the real query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import sqlglot
from sqlglot import exp

from ..corpus.schemas import MetricAsset, TableAsset
from .context import assemble_context

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..llm import ChatClient
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


# --------------------------------------------------------------------------- #
# LLM-backed generator (the design-vision generator)
# --------------------------------------------------------------------------- #

# The model emits this exact token when it judges the question unanswerable from
# the licensed tables. The flow then fails closed rather than guessing.
_CANNOT_ANSWER = "CANNOT_ANSWER"

_SYSTEM_PROMPT = """\
You are a careful SQL generator for a governed analytics system. You translate a \
business question into ONE read-only SQL query over a fixed, pre-authorised set of \
tables.

Hard rules (a violation is rejected by a downstream guardrail, so follow them):
- Use ONLY the physical table and column identifiers listed in the context below. \
Do not invent, guess, or qualify names with a database/schema prefix.
- Emit exactly ONE statement, and it MUST be a read-only SELECT (or a WITH ... \
SELECT). Never DDL/DML (no INSERT/UPDATE/DELETE/DROP/ALTER/etc.).
- Never SELECT *; project the specific columns you need.
- NEVER reference a column marked "[SUSPECT - DO NOT USE]". Those are unreliable \
decoys; using one is a correctness failure.
- Prefer the metric definitions and gold examples when they fit the question, and \
follow any guidance in the Skills section.
- Join only along the join paths listed; prefer high-confidence joins.

Output format:
- Return ONLY the SQL, with no explanation and no markdown fences.
- Target SQL dialect: {dialect}.
- If the question cannot be answered from these tables, return exactly: \
{cannot_answer}
"""


def _render_feedback(feedback: tuple[RepairFeedback, ...]) -> str:
    """Render prior failed attempts so the model can repair (self-repair loop)."""
    if not feedback:
        return ""
    lines = ["", "Your previous attempt(s) failed. Fix the issue and try again:"]
    for i, fb in enumerate(feedback, 1):
        lines.append(f"  Attempt {i} ({fb.stage} failure): {fb.sql}")
        lines.append(f"    reason: {fb.reason}")
    return "\n".join(lines)


def _extract_sql(response: str) -> str:
    """Pull the SQL out of a model response, tolerating markdown fences/prose.

    If the response has fenced code blocks, take the **last** one (the model's
    final answer usually follows any echoed example) and drop its info-string
    (```sql / ```python / bare ```), so a language tag is never captured into the
    SQL. Otherwise strip stray backticks. Trims a trailing semicolon; returns the
    empty string for an empty response.
    """
    text = response.strip()
    if not text:
        return ""
    # A fenced block: opening fence + optional info-string, a newline, then the
    # body, then the closing fence. Take the last block's body.
    blocks = re.findall(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)```", text, re.DOTALL)
    if blocks:
        text = blocks[-1].strip()
    else:
        text = text.strip("`").strip()
    return text.rstrip(";").strip()


def _tables_used(sql: str, physical_to_id: dict[str, str], dialect: str | None) -> frozenset[str]:
    """Map the physical table names in ``sql`` back to their asset ids.

    Best-effort: a parse failure or an unmapped name yields fewer ids, which only
    affects the reliability stamp's join plan - the guardrails re-parse the SQL
    independently, so this is never a safety input.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return frozenset()
    ids: set[str] = set()
    for table in tree.find_all(exp.Table):
        asset_id = physical_to_id.get(table.name)
        if asset_id is not None:
            ids.add(asset_id)
    return frozenset(ids)


class LlmSqlGenerator:
    """Model-backed generator: reads the :class:`PromptContext`, emits physical SQL.

    Layers a system prompt (role -> schema constraint -> safety -> output) over a
    :class:`~governed_bi.llm.ChatClient` and asks for a single read-only statement
    in physical (obfuscated) identifiers. It is **feedback-aware**: a guardrail or
    execution failure from a prior attempt is rendered back into the prompt, which
    is what makes the flow's bounded self-repair loop effective. Returns ``None``
    when the model declines (the ``CANNOT_ANSWER`` sentinel or an empty reply), so
    the flow fails closed instead of guessing.

    The client is injected, so tests drive it with a scripted
    :class:`~governed_bi.llm.StaticChatClient` and production injects
    :class:`~governed_bi.llm.OpenAiChatClient`. Implements the :class:`SqlGenerator`
    protocol.
    """

    def __init__(self, chat: "ChatClient", *, dialect: str | None = None) -> None:
        self.chat = chat
        self.dialect = dialect

    def generate(
        self,
        question: str,
        retrieval: "RetrievalResult",
        corpus: "Corpus",
        *,
        feedback: tuple[RepairFeedback, ...] = (),
        context: "PromptContext | None" = None,
    ) -> GeneratedSql | None:
        # Standalone fallback: if the flow did not pass a context, build one from
        # the retrieved tables only (no FK-neighborhood widening).
        if context is None:
            context = assemble_context(
                corpus, retrieval, licensed_table_ids=frozenset(retrieval.table_ids)
            )

        system = _SYSTEM_PROMPT.format(
            dialect=self.dialect or "standard SQL", cannot_answer=_CANNOT_ANSWER
        )
        user = (
            f"Context:\n{context.render()}\n\n"
            f"Question: {question}"
            f"{_render_feedback(feedback)}"
        )

        response = self.chat.complete(system, user)
        sql = _extract_sql(response)
        # Decline only on the exact sentinel (matched against the extracted SQL, not
        # the raw response), so valid SQL that merely contains the literal
        # 'CANNOT_ANSWER' (e.g. a status filter) is not mistaken for a decline.
        if not sql or sql.strip().upper() == _CANNOT_ANSWER:
            return None

        return GeneratedSql(
            sql=sql,
            tables_used=_tables_used(sql, context.physical_to_id(), self.dialect),
            metric_id=None,
        )
