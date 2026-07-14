"""Governed read-only tools for the agentic serve core (ADR 0002).

Every data touch goes through these tools. ``inspect_schema`` grows the per-turn
``licensed`` set (Inv #4); ``run_query`` / ``sample_rows`` are gated *and executed*
by ``GovernanceMiddleware`` (Inv #2/#10) — their bodies are never reached under
the agent path.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

from ..corpus.schemas import TableAsset
from ..retrieval import retrieve

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity
    from ..llm import Embedder


def _is_excluded(asset: Any) -> bool:
    gov = getattr(asset, "governance", None)
    return bool(gov is not None and getattr(gov, "excluded", False))


def _table_by_id(corpus: "Corpus", table_id: str) -> TableAsset | None:
    asset = corpus.by_id(table_id)
    if isinstance(asset, TableAsset) and not _is_excluded(asset):
        return asset
    # Physical-name fallback (model sometimes echoes names from search output).
    for a in corpus.assets:
        if isinstance(a, TableAsset) and not _is_excluded(a) and a.physical_name == table_id:
            return a
    return None


def render_retrieval(result) -> str:
    """Compact retrieval summary for the model (ids + scores, no excluded assets)."""
    lines: list[str] = [f"question: {result.question}"]
    if result.table_ids:
        lines.append("tables:")
        for tid in result.table_ids:
            score = result.scores.get(tid)
            suffix = f" (score={score:.3f})" if score is not None else ""
            lines.append(f"  - {tid}{suffix}")
    if result.term_ids:
        lines.append("terms: " + ", ".join(result.term_ids))
    if result.metric_ids:
        lines.append("metrics: " + ", ".join(result.metric_ids))
    if not result.table_ids and not result.term_ids and not result.metric_ids:
        lines.append("(no matching assets)")
    return "\n".join(lines)


def render_columns(asset: TableAsset, *, multi_schema: bool = False) -> str:
    """Columns + types for ``inspect_schema`` (physical identifiers the SQL must use)."""
    qual = f"{asset.schema}.{asset.physical_name}" if multi_schema else asset.physical_name
    lines = [
        f"table_id: {asset.id}",
        f"physical: {qual}",
        f"description: {asset.description or ''}",
        "columns:",
    ]
    for col in asset.columns:
        if col.governance.excluded:
            continue
        suspect = ""
        if getattr(col.reliability, "status", None) is not None:
            status = getattr(col.reliability.status, "value", col.reliability.status)
            if status == "suspect":
                suspect = " [SUSPECT — do not use]"
        lines.append(
            f"  - {col.physical_name}: {col.physical_type}"
            f" ({col.logical_type.value if hasattr(col.logical_type, 'value') else col.logical_type})"
            f"{suspect}"
        )
    return "\n".join(lines)


def render_few_shots(corpus: "Corpus", few_shot_ids: list, *, limit: int = 3) -> list[str]:
    """Q→gold-SQL exemplars (the highest-value curated content) for a query."""
    from ..corpus.schemas import FewShotAsset

    lines: list[str] = []
    for fid in few_shot_ids[:limit]:
        fs = corpus.by_id(fid)
        if isinstance(fs, FewShotAsset):
            lines.append(f"  Q: {fs.question}")
            lines.append(f"  A: {fs.sql}")
    return lines


def render_metrics(corpus: "Corpus", metric_ids: list) -> list[str]:
    """Metric name = expression over base table (the curated meaning)."""
    from ..corpus.schemas import MetricAsset, TableAsset

    lines: list[str] = []
    for mid in metric_ids:
        m = corpus.by_id(mid)
        if isinstance(m, MetricAsset):
            base = corpus.by_id(m.base_table)
            base_name = base.physical_name if isinstance(base, TableAsset) else m.base_table
            dims = f" (dims: {', '.join(m.dimensions)})" if m.dimensions else ""
            lines.append(f"  {m.name} = {m.expression} over {base_name}{dims}")
    return lines


def render_terms(corpus: "Corpus", term_ids: list) -> list[str]:
    """Business term → synonyms (maps question language to the schema)."""
    from ..corpus.schemas import TermAsset

    lines: list[str] = []
    for tid in term_ids:
        t = corpus.by_id(tid)
        if isinstance(t, TermAsset):
            syn = f" (synonyms: {', '.join(t.synonyms)})" if t.synonyms else ""
            lines.append(f"  {t.name}{syn}")
    return lines


def render_result(result) -> str:
    """Compact executed-result text for tool feedback."""
    if result.row_count == 0:
        return "0 rows"
    head = ", ".join(result.columns)
    preview_rows = result.rows[:5]
    body = "\n".join(" | ".join(str(c) for c in row) for row in preview_rows)
    more = f"\n... ({result.row_count} rows total)" if result.row_count > 5 else ""
    trunc = " [truncated]" if result.truncated else ""
    return f"columns: [{head}]\nrows:\n{body}{more}{trunc}"


def make_tools(
    corpus: "Corpus",
    gateway: "Gateway",
    identity: "Identity",
    *,
    embedder: "Embedder | None" = None,
    multi_schema: bool = False,
):
    """Factory: four governed tools closed over deployment deps.

    ``gateway`` / ``identity`` are accepted for signature symmetry with the
    middleware (which owns execution for ``run_query`` / ``sample_rows``).
    """
    _ = gateway, identity  # owned by GovernanceMiddleware for data-touching tools

    @tool
    def search_corpus(query: str) -> str:
        """Find more governed context for a query beyond what you were given.

        Returns matching tables plus **curated content** — few-shot Q→SQL
        exemplars, metric expressions, and business terms. Use when the seeded
        context is missing a table/example you need; then ``inspect_schema`` any
        new table before querying it.
        """
        r = retrieve(corpus, query, embedder=embedder)
        kept = [
            tid
            for tid in r.table_ids
            if (asset := corpus.by_id(tid)) is not None and not _is_excluded(asset)
        ]
        filtered = replace(
            r,
            table_ids=kept,
            scores={k: v for k, v in r.scores.items() if k in kept or not str(k).startswith("tbl_")},
        )
        out = [render_retrieval(filtered)]
        fs = render_few_shots(corpus, r.few_shot_ids)
        if fs:
            out += ["", "few-shot examples (Q → gold SQL):", *fs]
        mt = render_metrics(corpus, r.metric_ids)
        if mt:
            out += ["", "metrics:", *mt]
        tm = render_terms(corpus, r.term_ids)
        if tm:
            out += ["", "terms:", *tm]
        return "\n".join(out)

    @tool
    def inspect_schema(
        table_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Show a table's columns+types and LICENSE it for this turn.

        You cannot query a table until you have inspected it. Call tools one at a time.
        """
        asset = _table_by_id(corpus, table_id)
        if asset is None:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"{table_id}: not available",
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )
        return Command(
            update={
                "licensed": [asset.id],
                "messages": [
                    ToolMessage(
                        content=render_columns(asset, multi_schema=multi_schema),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    @tool
    def sample_rows(table_id: str, n: int = 5) -> str:
        """Preview up to n rows of an already-licensed table (read-only, RLS via identity).

        Only allowlisted columns are returned — never excluded or suspect columns.
        Guardrailed and executed by governance middleware.
        """
        raise RuntimeError(
            "sample_rows must be intercepted by GovernanceMiddleware (Inv #2)"
        )

    @tool
    def run_query(sql: str) -> str:
        """Execute a read-only SELECT. Guardrailed + audited by middleware.

        Only use identifiers from tables you have inspected. If BLOCKED, fix and retry.
        """
        raise RuntimeError(
            "run_query must be intercepted by GovernanceMiddleware (Inv #2)"
        )

    return [search_corpus, inspect_schema, sample_rows, run_query]
