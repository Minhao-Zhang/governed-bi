"""Analyst step 5b: retrieval -> prompt context assembly.

Retrieval returns asset *ids*; a SQL generator needs the resolved *meaning* -
the schema text, join paths, business terms, metrics, reliability caveats, gold
exemplars, and governed notes - laid out as one context bundle. This module builds that
bundle deterministically from the ``for_analyst()`` corpus and a
:class:`~governed_bi.retrieval.RetrievalResult`, so it is unit-testable with no
model and no network. It is the contract every :class:`SqlGenerator` reads from,
and it is where the semantic layer's value is injected into an answer.

**The tables it presents are exactly the L4-licensed set** (the retrieved tables
plus their FK join-neighborhood and the Steiner points the plan bridges through).
The agent core derives the guardrail's ``allowed_tables`` from
:meth:`PromptContext.allowed_table_names`, so *what the model can see is exactly
what the guardrail will permit* - no wider, no narrower. L3 still guards every
column independently, so widening to neighbor tables never exposes an excluded or
suspect column.

The three points where curator inference drives serve behavior all land here
(``docs/analyst.md``): reliability caveats become explicit "DO NOT USE" lines,
join ``confidence`` is annotated (and low-confidence joins flagged), and
always-active notes are included by summary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..corpus.schemas import (
    JoinAsset,
    MetricAsset,
    ReliabilityStatus,
    TableAsset,
    TermAsset,
)
from .answer import LOW_CONFIDENCE_JOIN

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..retrieval import RetrievalResult


# --------------------------------------------------------------------------- #
# View models (resolved, physical-identifier facing; what the generator reads)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ColumnView:
    physical_name: str
    physical_type: str
    logical_type: str
    role: str | None = None
    description: str | None = None
    suspect: bool = False
    caveat: str | None = None  # the reliability note, if suspect


@dataclass(frozen=True)
class TableView:
    id: str
    physical_name: str
    description: str | None
    grain: str | None
    columns: list[ColumnView]
    retrieved: bool  # True: surfaced by retrieval; False: reachable only via a join
    schema: str | None = None  # the table's scoping schema (its ``db``); qualifies L4


@dataclass(frozen=True)
class JoinView:
    on: str  # physical equality, verbatim from the join asset
    cardinality: str | None = None
    confidence: float | None = None
    low_confidence: bool = False


@dataclass(frozen=True)
class TermView:
    name: str
    synonyms: list[str]
    binds_to: str | None  # human description of the bound target


@dataclass(frozen=True)
class MetricView:
    name: str
    expression: str
    base_table: str  # physical name
    dimensions: list[str]


@dataclass(frozen=True)
class FewShotView:
    question: str
    sql: str


@dataclass(frozen=True)
class PromptContext:
    """The resolved context a generator turns into SQL.

    ``tables`` is the licensed set (retrieved + join-reachable). ``render`` emits
    the text block a generator layers its system prompt over; the structured
    fields stay available for a generator (or test) that wants them directly.
    """

    question: str
    tables: list[TableView] = field(default_factory=list)
    joins: list[JoinView] = field(default_factory=list)
    terms: list[TermView] = field(default_factory=list)
    metrics: list[MetricView] = field(default_factory=list)
    few_shots: list[FewShotView] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    # Injected note lines (must_honour); kept as ``rules`` for PromptContext compat.
    rules: list[str] = field(default_factory=list)
    # Advisory note lines (normative_force=advisory).
    advisory_notes: list[str] = field(default_factory=list)
    # Prior (role, content) turns from working memory (D8), oldest first. Empty
    # only for a single-round eval call; every conversational caller passes the
    # session history so a follow-up ("what about last year?") resolves against it.
    conversation: list[tuple[str, str]] = field(default_factory=list)

    def allowed_table_names(self) -> frozenset[str]:
        """The licensed tables — the L4 ``allowed_tables`` set: schema-qualified
        ``{schema}.{physical_name}``, matching the guardrail's qualified L4 set."""
        return frozenset(f"{t.schema}.{t.physical_name}" for t in self.tables)

    def physical_to_id(self) -> dict[str, str]:
        """Map each licensed schema-qualified table name back to its asset id (for
        resolving a generator's declared tables to ids), matching
        :meth:`allowed_table_names`."""
        return {f"{t.schema}.{t.physical_name}": t.id for t in self.tables}

    def render(self) -> str:
        """Render the context as a text block for an LLM prompt."""
        return _render(self)


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #


def _column_view(corpus_column) -> ColumnView:
    rel = corpus_column.reliability
    suspect = rel.status is ReliabilityStatus.suspect
    return ColumnView(
        physical_name=corpus_column.physical_name,
        physical_type=corpus_column.physical_type,
        logical_type=corpus_column.logical_type.value,
        role=corpus_column.role.value if corpus_column.role is not None else None,
        description=corpus_column.description,
        suspect=suspect,
        caveat=rel.note if suspect else None,
    )


def _table_view(table: TableAsset, *, retrieved: bool) -> TableView:
    return TableView(
        id=table.id,
        physical_name=table.physical_name,
        description=table.description,
        grain=table.grain,
        columns=[_column_view(c) for c in table.columns],
        retrieved=retrieved,
        schema=table.schema,
    )


def _describe_binding(corpus: "Corpus", term: TermAsset) -> str | None:
    if term.binding is None:
        return None
    target = corpus.by_id(term.binding.asset_id)
    kind = term.binding.asset_type
    if isinstance(target, MetricAsset):
        return f"metric '{target.name}'"
    if isinstance(target, TableAsset):
        return f"table '{target.physical_name}'"
    return f"{kind} '{term.binding.asset_id}'"


def assemble_context(
    corpus: "Corpus",
    retrieval: "RetrievalResult",
    *,
    licensed_table_ids: frozenset[str] | set[str],
    low_confidence_join: float = LOW_CONFIDENCE_JOIN,
    history: Sequence[tuple[str, str]] = (),
    db_name: str = "main",
    always_note_global_max: int = 8,
    always_note_char_max: int = 2000,
) -> PromptContext:
    """Resolve retrieval ids + the licensed table scope into a :class:`PromptContext`.

    ``licensed_table_ids`` is the L4 scope the agent core computes (retrieved tables +
    FK join-neighborhood + Steiner points). Tables are ordered retrieval-first
    (in retrieval order) then the remaining licensed tables (sorted), each flagged
    ``retrieved``. Joins shown are every join asset internal to the licensed set,
    so the generator can bridge to a neighbor; low-confidence joins are flagged.
    ``corpus`` is expected to be the ``for_analyst()`` view. The licensed scope is
    schema-qualified throughout (see :meth:`PromptContext.allowed_table_names`).
    """
    retrieved_order = [tid for tid in retrieval.table_ids if tid in licensed_table_ids]
    retrieved_set = set(retrieved_order)
    extra = sorted(tid for tid in licensed_table_ids if tid not in retrieved_set)

    tables: list[TableView] = []
    for tid in [*retrieved_order, *extra]:
        table = corpus.by_id(tid)
        if isinstance(table, TableAsset):
            tables.append(_table_view(table, retrieved=tid in retrieved_set))

    # Joins internal to the licensed set (both endpoints licensed).
    joins: list[JoinView] = []
    for asset in corpus.assets:
        if not isinstance(asset, JoinAsset):
            continue
        if asset.left_table in licensed_table_ids and asset.right_table in licensed_table_ids:
            conf = asset.confidence
            joins.append(
                JoinView(
                    on=asset.on,
                    cardinality=asset.cardinality.value if asset.cardinality else None,
                    confidence=conf,
                    low_confidence=conf is not None and conf < low_confidence_join,
                )
            )
    joins.sort(key=lambda j: j.on)

    terms: list[TermView] = []
    for term_id in retrieval.term_ids:
        term = corpus.by_id(term_id)
        if isinstance(term, TermAsset):
            terms.append(
                TermView(
                    name=term.name,
                    synonyms=list(term.synonyms),
                    binds_to=_describe_binding(corpus, term),
                )
            )

    metrics: list[MetricView] = []
    for metric_id in retrieval.metric_ids:
        metric = corpus.by_id(metric_id)
        if isinstance(metric, MetricAsset):
            base = corpus.by_id(metric.base_table)
            base_name = base.physical_name if isinstance(base, TableAsset) else metric.base_table
            metrics.append(
                MetricView(
                    name=metric.name,
                    expression=metric.expression,
                    base_table=base_name,
                    dimensions=list(metric.dimensions),
                )
            )

    few_shots: list[FewShotView] = []
    for fs_id in retrieval.few_shot_ids:
        from ..corpus.schemas import FewShotAsset

        fs = corpus.by_id(fs_id)
        if isinstance(fs, FewShotAsset):
            few_shots.append(FewShotView(question=fs.question, sql=fs.sql))

    # Aggregate suspect-column caveats across the licensed tables (decoy avoidance).
    caveats: list[str] = []
    for tv in tables:
        for col in tv.columns:
            if col.suspect:
                note = col.caveat or "flagged unreliable"
                caveats.append(f"{tv.physical_name}.{col.physical_name}: {note}")

    from .note_inject import (
        format_note_lines,
        licensed_scope_from_tables,
        select_notes_for_injection,
    )

    licensed = licensed_scope_from_tables(
        corpus, licensed_table_ids, db_name=db_name
    )
    injected = select_notes_for_injection(
        corpus,
        retrieval,
        licensed,
        global_max=always_note_global_max,
        char_max=always_note_char_max,
    )
    rules, advisory_notes = format_note_lines(injected)

    return PromptContext(
        question=retrieval.question,
        tables=tables,
        joins=joins,
        terms=terms,
        metrics=metrics,
        few_shots=few_shots,
        caveats=caveats,
        rules=rules,
        advisory_notes=advisory_notes,
        conversation=list(history),
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _render_column(col: ColumnView) -> str:
    bits = [col.physical_name, f"({col.logical_type}"]
    bits[-1] += f", {col.role})" if col.role else ")"
    line = "    - " + " ".join(bits)
    if col.description:
        line += f": {col.description}"
    if col.suspect:
        line += f"  [SUSPECT - DO NOT USE: {col.caveat or 'flagged unreliable'}]"
    return line


def _render(ctx: PromptContext) -> str:
    lines: list[str] = []

    if ctx.conversation:
        lines.append(
            "## Conversation so far (oldest first; use ONLY to resolve references "
            "in the latest question, e.g. 'that', 'last year')"
        )
        for role, content in ctx.conversation:
            lines.append(f"  {role}: {content}")
        lines.append("")

    lines.append("## Tables (use ONLY these physical identifiers)")
    for tv in ctx.tables:
        tag = "" if tv.retrieved else "  [reachable only via a join]"
        # Present the fully-qualified schema.table the guardrail requires.
        name = f"{tv.schema}.{tv.physical_name}"
        header = f"### {name}{tag}"
        if tv.grain:
            header += f"  (grain: {tv.grain})"
        lines.append(header)
        if tv.description:
            lines.append(f"  {tv.description}")
        for col in tv.columns:
            lines.append(_render_column(col))

    if ctx.joins:
        lines.append("")
        lines.append("## Joins (physical equality; prefer high-confidence)")
        for j in ctx.joins:
            note = []
            if j.cardinality:
                note.append(j.cardinality)
            if j.confidence is not None:
                note.append(f"confidence {j.confidence:.2f}")
            if j.low_confidence:
                note.append("LOW CONFIDENCE")
            suffix = f"  ({', '.join(note)})" if note else ""
            lines.append(f"  {j.on}{suffix}")

    if ctx.terms:
        lines.append("")
        lines.append("## Business terms")
        for t in ctx.terms:
            syn = f" (synonyms: {', '.join(t.synonyms)})" if t.synonyms else ""
            binds = f" -> {t.binds_to}" if t.binds_to else ""
            lines.append(f"  {t.name}{syn}{binds}")

    if ctx.metrics:
        lines.append("")
        lines.append("## Metrics (meaning; map to physical columns)")
        for m in ctx.metrics:
            dims = f"  (dimensions: {', '.join(m.dimensions)})" if m.dimensions else ""
            lines.append(f"  {m.name} = {m.expression}  over {m.base_table}{dims}")

    if ctx.caveats:
        lines.append("")
        lines.append("## Reliability caveats (DO NOT USE these columns)")
        for c in ctx.caveats:
            lines.append(f"  {c}")

    if ctx.rules:
        lines.append("")
        lines.append("## Governance notes (must honour)")
        for r in ctx.rules:
            for part in r.splitlines() or [r]:
                lines.append(f"  {part}")

    if ctx.advisory_notes:
        lines.append("")
        lines.append("## Governance notes (advisory)")
        for r in ctx.advisory_notes:
            for part in r.splitlines() or [r]:
                lines.append(f"  {part}")

    if ctx.few_shots:
        lines.append("")
        lines.append("## Example questions with gold SQL")
        for fs in ctx.few_shots:
            lines.append(f"  Q: {fs.question}")
            lines.append(f"  A: {fs.sql}")

    return "\n".join(lines)
