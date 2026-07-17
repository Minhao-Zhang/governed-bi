"""Join-aware schema router (D15 retrieval pre-stage).

On the multi-schema Postgres/Redshift path, thousands of tables across many
schemas must stay tractable. This module shortlists the schemas relevant to a
question (BM25 over per-schema documents), then **expands along curated
cross-schema ``JoinAsset`` edges** so a bridge table in an un-mentioned schema
is not dropped. A similarity-only shortlist would cause spurious
``missing_edge`` refusals.

Single-schema / SQLite callers skip this module entirely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..corpus.ids import derive_column_id
from ..corpus.schemas import (
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    NegativeExampleAsset,
    RuleAsset,
    TableAsset,
    TermAsset,
)
from .rvgd import BM25Index, asset_document

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..llm import Embedder

DEFAULT_SCHEMA_TOP_K = 3


def list_schemas(corpus: "Corpus") -> list[str]:
    """Distinct table schemas in ``corpus``, sorted ascending (deterministic)."""
    return sorted({a.schema for a in corpus.assets if isinstance(a, TableAsset)})


def _term_binding_table(corpus: "Corpus", term: TermAsset) -> str | None:
    """Owning table id for a term binding, or None when unbound / unresolved."""
    if term.binding is None:
        return None
    bid = term.binding.asset_id
    kind = term.binding.asset_type
    if kind == "table":
        return bid
    if kind == "metric":
        m = corpus.by_id(bid)
        return m.base_table if isinstance(m, MetricAsset) else None
    if kind == "column":
        for a in corpus.assets:
            if not isinstance(a, TableAsset):
                continue
            for c in a.columns:
                if derive_column_id(a.id, c.physical_name) == bid:
                    return a.id
    return None


def schema_document(corpus: "Corpus", schema: str) -> str:
    """Concatenate language surfaces for assets that belong to ``schema``.

    Tables in the schema contribute their full ``asset_document``. Metrics /
    few-shots / terms are included when grounded to a table in the schema.
    """
    table_ids = {
        a.id for a in corpus.assets if isinstance(a, TableAsset) and a.schema == schema
    }
    parts: list[str] = [schema]
    for a in corpus.assets:
        if isinstance(a, TableAsset) and a.schema == schema:
            parts.append(asset_document(a))
        elif isinstance(a, MetricAsset) and a.base_table in table_ids:
            parts.append(asset_document(a))
        elif isinstance(a, FewShotAsset) and a.schema == schema:
            parts.append(asset_document(a))
        elif isinstance(a, TermAsset):
            owner = _term_binding_table(corpus, a)
            if owner in table_ids:
                parts.append(asset_document(a))
    return " ".join(p for p in parts if p)


def schema_documents(corpus: "Corpus") -> dict[str, str]:
    """All per-schema documents in a **single pass** over the corpus.

    Equivalent to ``{s: schema_document(corpus, s) for s in list_schemas(corpus)}``
    but O(assets) instead of O(schemas × assets) — it buckets each asset into its
    schema once rather than rescanning the whole corpus per schema.
    """
    table_schema = {
        a.id: a.schema for a in corpus.assets if isinstance(a, TableAsset)
    }
    parts: dict[str, list[str]] = {s: [s] for s in list_schemas(corpus)}
    for a in corpus.assets:
        if isinstance(a, TableAsset):
            parts[a.schema].append(asset_document(a))
        elif isinstance(a, MetricAsset):
            s = table_schema.get(a.base_table)
            if s in parts:
                parts[s].append(asset_document(a))
        elif isinstance(a, FewShotAsset):
            if a.schema in parts:
                parts[a.schema].append(asset_document(a))
        elif isinstance(a, TermAsset):
            owner = _term_binding_table(corpus, a)
            s = table_schema.get(owner) if owner else None
            if s in parts:
                parts[s].append(asset_document(a))
    return {s: " ".join(p for p in ps if p) for s, ps in parts.items()}


def shortlist_schemas(
    corpus: "Corpus",
    question: str,
    *,
    top_k: int = DEFAULT_SCHEMA_TOP_K,
    embedder: "Embedder | None" = None,
) -> list[str]:
    """Rank schemas by BM25 (+ optional embedder RRF) against ``question``.

    Returns up to ``top_k`` schema names, score desc then name asc. When nothing
    scores, falls back to every schema in the corpus (fail-open to full span).
    """
    schemas = list_schemas(corpus)
    if not schemas:
        return []
    if len(schemas) == 1:
        return schemas

    docs = schema_documents(corpus)
    ranked = BM25Index.from_documents(docs).rank(question)
    if embedder is not None and ranked:
        from ..llm import cosine
        from .embedding import fuse_rankings

        # Batch every schema document into ONE embed call (was one call per
        # schema — O(schemas) round-trips on the hot path).
        named = [(s, docs[s]) for s in docs if docs[s].strip()]
        q_vec = embedder.embed_one(question)
        vecs = embedder.embed([text for _s, text in named])
        emb_ranked = [
            (s, sc)
            for (s, _text), vec in zip(named, vecs)
            if (sc := cosine(q_vec, vec)) > 0.0
        ]
        emb_ranked.sort(key=lambda p: (-p[1], p[0]))
        if emb_ranked:
            ranked = fuse_rankings(ranked, emb_ranked)

    if not ranked:
        return schemas  # fail-open: no lexical signal → keep all
    return [s for s, _ in ranked[:top_k]]


def expand_schemas_via_curated_joins(
    corpus: "Corpus", seeds: set[str]
) -> frozenset[str]:
    """Fixpoint-expand ``seeds`` along curated cross-schema ``JoinAsset`` edges.

    Within-schema joins do not add schemas. Only edges whose endpoints live in
    different schemas pull a new schema into the set.
    """
    table_schema = {
        a.id: a.schema for a in corpus.assets if isinstance(a, TableAsset)
    }
    neighbors: dict[str, set[str]] = {}
    for a in corpus.assets:
        if not isinstance(a, JoinAsset):
            continue
        left = table_schema.get(a.left_table)
        right = table_schema.get(a.right_table)
        if left is None or right is None or left == right:
            continue
        neighbors.setdefault(left, set()).add(right)
        neighbors.setdefault(right, set()).add(left)

    out = set(seeds)
    frontier = list(seeds)
    while frontier:
        s = frontier.pop()
        for nbr in neighbors.get(s, ()):
            if nbr not in out:
                out.add(nbr)
                frontier.append(nbr)
    return frozenset(out)


def route_schemas(
    corpus: "Corpus",
    question: str,
    *,
    top_k: int = DEFAULT_SCHEMA_TOP_K,
    embedder: "Embedder | None" = None,
) -> frozenset[str]:
    """Shortlist schemas for ``question``, then expand via curated joins."""
    seeds = set(shortlist_schemas(corpus, question, top_k=top_k, embedder=embedder))
    if not seeds:
        return frozenset()
    return expand_schemas_via_curated_joins(corpus, seeds)


def _schema_pick_summary(corpus: "Corpus", schema: str, *, max_tables: int = 15) -> str:
    """Compact one-block summary of a schema for the LLM picker: name + tables
    (physical name + short description). Kept small to bound context."""
    tables = [
        a for a in corpus.assets if isinstance(a, TableAsset) and a.schema == schema
    ]
    tables.sort(key=lambda a: a.physical_name)
    lines = [f"schema: {schema}"]
    for a in tables[:max_tables]:
        desc = (a.description or "").strip().replace("\n", " ")
        if len(desc) > 90:
            desc = desc[:90] + "…"
        lines.append(f"  - {a.physical_name}" + (f": {desc}" if desc else ""))
    if len(tables) > max_tables:
        lines.append(f"  … ({len(tables) - max_tables} more tables)")
    return "\n".join(lines)


def select_schema(
    corpus: "Corpus",
    question: str,
    candidates: list[str],
    *,
    chat,
    max_tables: int = 15,
) -> str:
    """LLM picks the single best schema from ``candidates`` (pipeline-design §5.1).

    Retrieval has already shortlisted ``candidates`` (BM25, ~top-3). This node
    shows the LLM each candidate's tables and asks for exactly one schema name,
    so the serve path can scope to a single schema (no cross-schema joins).

    Deterministic guards: 0 candidates → ``""``; 1 candidate → it, no LLM call.
    On an unparseable / out-of-set reply, falls back to ``candidates[0]`` (the
    top BM25 rank) rather than raising.
    """
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    summaries = "\n\n".join(
        _schema_pick_summary(corpus, s, max_tables=max_tables) for s in candidates
    )
    system = (
        "You route a natural-language question to exactly ONE database schema. "
        "You are given candidate schemas and their tables. Reply with ONLY the "
        "single schema name (verbatim, no punctuation) that can answer the "
        "question. It must be exactly one of the candidate names."
    )
    user = (
        f"Question: {question}\n\n"
        f"Candidate schemas:\n{summaries}\n\n"
        f"Answer with exactly one of: {', '.join(candidates)}"
    )
    try:
        reply = (chat.complete(system, user) or "").strip()
    except Exception:
        return candidates[0]

    # Exact, then case-insensitive, then substring — else fall back to top rank.
    if reply in candidates:
        return reply
    low = reply.lower()
    for c in candidates:
        if c.lower() == low:
            return c
    for c in candidates:
        if c.lower() in low or low in c.lower():
            return c
    return candidates[0]


def filter_corpus_for_retrieval(corpus: "Corpus", schemas: frozenset[str]) -> "Corpus":
    """Subset of ``corpus`` whose assets are in scope for the routed schemas.

    - Tables: ``table.schema in schemas``
    - Joins: both endpoints' schemas ⊆ routed set
    - Metrics: ``base_table`` in kept tables
    - Few-shots: ``few_shot.schema in schemas``
    - Terms: unbound, or binding resolves to a kept table
    - Rules / negatives: always kept (global governance / refuse-gate)
    """
    from ..corpus.loader import Corpus

    if not schemas:
        return corpus

    kept_tables = {
        a.id
        for a in corpus.assets
        if isinstance(a, TableAsset) and a.schema in schemas
    }
    table_schema = {
        a.id: a.schema for a in corpus.assets if isinstance(a, TableAsset)
    }

    kept: list = []
    for a in corpus.assets:
        if isinstance(a, TableAsset):
            if a.id in kept_tables:
                kept.append(a)
        elif isinstance(a, JoinAsset):
            left_s = table_schema.get(a.left_table)
            right_s = table_schema.get(a.right_table)
            if (
                left_s is not None
                and right_s is not None
                and left_s in schemas
                and right_s in schemas
            ):
                kept.append(a)
        elif isinstance(a, MetricAsset):
            if a.base_table in kept_tables:
                kept.append(a)
        elif isinstance(a, FewShotAsset):
            if a.schema in schemas:
                kept.append(a)
        elif isinstance(a, TermAsset):
            owner = _term_binding_table(corpus, a)
            if owner is None or owner in kept_tables:
                kept.append(a)
        elif isinstance(a, (RuleAsset, NegativeExampleAsset)):
            kept.append(a)

    skills = [
        s
        for s in corpus.skills
        if getattr(s.frontmatter, "schema", None) in schemas
    ]
    return Corpus(assets=kept, skills=skills)
