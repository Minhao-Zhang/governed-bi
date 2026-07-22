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
from langgraph.types import Command, interrupt

from ..corpus.schemas import TableAsset
from ..retrieval import retrieve
from .clarify import clarification_request, parse_response

# Sentinel the agent sees when the user declines a clarification. The rails
# short-circuit to a refusal before the agent runs again (contract §4), so this
# is a defensive fallback only.
CLARIFY_DECLINED = "USER_DECLINED: the user did not answer; do not guess."

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


def render_columns(asset: TableAsset) -> str:
    """Columns + types for ``inspect_schema`` (physical identifiers the SQL must use)."""
    qual = f"{asset.schema}.{asset.physical_name}"
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


def render_notes(corpus: "Corpus", note_ids: list, *, include_body: bool = False) -> list[str]:
    """Governed notes that bear on the query.

    Summary is the default surface; ``include_body`` is for ``read_notes``.
    Excluded notes are omitted.
    """
    from ..corpus.schemas import NoteAsset
    from ..corpus.validate import _excluded_identifier_tokens

    excluded = _excluded_identifier_tokens(list(corpus.assets))
    lines: list[str] = []
    for note_id in note_ids:
        note = corpus.by_id(note_id)
        if not isinstance(note, NoteAsset) or _is_excluded(note):
            continue
        # C5: never surface prose that still names a governance-excluded identifier.
        if _text_names_excluded(f"{note.summary}\n{note.body or ''}", excluded):
            continue
        kind = getattr(note.kind, "value", note.kind)
        scope = f" (applies to: {', '.join(note.scope)})" if note.scope else ""
        lines.append(f"  [{kind}] {note.summary}{scope}")
        if include_body and note.body:
            lines.append(note.body)
    return lines


_GREP_NOTES_MAX_HITS = 20
_GREP_NOTES_MAX_CHARS = 4000
_GREP_PATTERN_MAX_LEN = 128
_GREP_TEXT_SCAN_MAX = 20000  # cap text length fed to a compiled regex (ReDoS input bound)


def _safe_grep_pattern(pattern: str):
    """Compile a ReDoS-bounded pattern, or fall back to literal substring.

    A quantifier applied to a group (``)`` followed by ``* + ? {``) is the
    necessary ingredient for catastrophic backtracking, so ANY quantified group
    (``(a+)+``, ``(a*)*``, ``([a-z]+)*``, ``(a|a)+`` …) and the ``.*.*`` form fall
    back to a linear literal-substring match. Conservative but safe; legitimate
    note-grep patterns rarely need a quantified group. Callers additionally cap
    the searched text length.
    """
    import re as _re

    pat = (pattern or "").strip()
    if not pat or len(pat) > _GREP_PATTERN_MAX_LEN:
        raise ValueError(f"pattern must be 1..{_GREP_PATTERN_MAX_LEN} chars")
    if _re.search(r"\)[*+?{]|(\.\*){2,}", pat):
        return pat.casefold()
    try:
        return _re.compile(pat, _re.IGNORECASE)
    except _re.error:
        return pat.casefold()


def _text_names_excluded(text: str, excluded_tokens) -> bool:
    """Case-insensitive C5 check: does ``text`` name any excluded identifier?

    Postgres folds unquoted identifiers to lowercase, so a case-sensitive match
    would leak a differently-cased name; both sides are casefolded.
    """
    blob = text.casefold()
    return any(tok.casefold() in blob for tok in excluded_tokens)


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
    enable_clarify: bool = False,
):
    """Factory: the governed read-only tools closed over deployment deps.

    ``gateway`` / ``identity`` are accepted for signature symmetry with the
    middleware (which owns execution for ``run_query`` / ``sample_rows``).

    ``enable_clarify`` adds the ``ask_user`` HITL tool (serve path only); it calls
    ``interrupt`` and therefore needs the inner agent compiled with a checkpointer
    (see ``build_agent_core``). The eval/offline path leaves it off, so the tool
    set and behaviour are unchanged there.
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
        notes = render_notes(corpus, r.note_ids)
        if notes:
            out += ["", "governed notes:", *notes]
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
                        content=render_columns(asset),
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

    @tool
    def ask_user(question: str, why: str) -> str:
        """Ask the user ONE short clarifying question and wait for their answer.

        Use ONLY when the question is genuinely ambiguous and the governed context
        cannot resolve it (e.g. two competing definitions of a term) — never for
        things you can answer by inspecting the schema or corpus. State plainly in
        ``why`` what is ambiguous. Returns the user's answer; continue with it.
        """
        response = interrupt(clarification_request(question, why))
        parsed = parse_response(response)
        if parsed["declined"]:
            return CLARIFY_DECLINED
        return parsed["answer"]

    @tool
    def read_notes(note_id: str) -> str:
        """Read one governed note by id (summary + body). Does NOT license tables.

        Naming a table inside a note does not authorize ``run_query`` against it —
        call ``inspect_schema`` first. Excluded notes are hidden.
        """
        from ..corpus.schemas import NoteAsset
        from ..corpus.validate import _excluded_identifier_tokens

        note = corpus.by_id(note_id)
        if not isinstance(note, NoteAsset) or _is_excluded(note):
            return f"{note_id}: not available"
        # Refuse to return prose that still names excluded identifiers (C5,
        # case-insensitive so a differently-cased name cannot slip through).
        excluded = _excluded_identifier_tokens(list(corpus.assets))
        if _text_names_excluded(f"{note.summary}\n{note.body or ''}", excluded):
            return f"{note_id}: withheld (names excluded identifiers)"
        kind = getattr(note.kind, "value", note.kind)
        lines = [f"id: {note.id}", f"kind: {kind}", f"summary: {note.summary}"]
        if note.body:
            lines.append("body:")
            lines.append(note.body)
        return "\n".join(lines)

    @tool
    def grep_notes(pattern: str) -> str:
        """Search note summaries and bodies for a pattern (read-only, capped).

        Does NOT license tables. ReDoS-bounded; output capped. Excluded notes skip.
        """
        from ..corpus.schemas import NoteAsset
        from ..corpus.validate import _excluded_identifier_tokens

        try:
            compiled = _safe_grep_pattern(pattern)
        except ValueError as exc:
            return f"error: {exc}"
        excluded = _excluded_identifier_tokens(list(corpus.assets))
        hits: list[str] = []
        total_chars = 0
        for asset in corpus.assets:
            if not isinstance(asset, NoteAsset) or _is_excluded(asset):
                continue
            text = f"{asset.summary}\n{asset.body or ''}"
            if _text_names_excluded(text, excluded):
                continue
            matched = False
            if hasattr(compiled, "search"):
                # Cap regex input length as a second ReDoS bound (bodies uncapped).
                matched = compiled.search(text[:_GREP_TEXT_SCAN_MAX]) is not None
            else:
                matched = compiled in text.casefold()
            if not matched:
                continue
            line = f"{asset.id}: {asset.summary}"
            if total_chars + len(line) > _GREP_NOTES_MAX_CHARS:
                hits.append("…(output capped)")
                break
            hits.append(line)
            total_chars += len(line)
            if len(hits) >= _GREP_NOTES_MAX_HITS:
                hits.append("…(hit cap)")
                break
        return "\n".join(hits) if hits else "(no matching notes)"

    tools = [search_corpus, inspect_schema, sample_rows, run_query, read_notes, grep_notes]
    if enable_clarify:
        tools.append(ask_user)
    return tools
