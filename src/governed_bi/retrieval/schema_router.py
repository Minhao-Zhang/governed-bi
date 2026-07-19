"""Join-aware schema router (D15 retrieval pre-stage).

On the multi-schema Postgres/Redshift path, thousands of tables across many
schemas must stay tractable. This module shortlists the schemas relevant to a
question (embedding similarity over per-schema documents, with a BM25 fallback
when no embedder is available), then **expands along curated cross-schema
``JoinAsset`` edges** so a bridge table in an un-mentioned schema is not dropped.
A similarity-only shortlist would cause spurious ``missing_edge`` refusals.

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


def embed_schema_documents(
    corpus: "Corpus", embedder: "Embedder"
) -> dict[str, list[float]]:
    """Embed each schema's document once. Schema vectors are constant per corpus,
    so serve callers precompute them at graph-build time and hand them to
    :func:`shortlist_schemas` (``schema_vectors=``) instead of re-embedding all
    schema docs on every question."""
    docs = schema_documents(corpus)
    named = [(s, docs[s]) for s in docs if docs[s].strip()]
    if not named:
        return {}
    vecs = embedder.embed([text for _s, text in named])
    return dict(zip([s for s, _ in named], vecs))


def shortlist_schemas(
    corpus: "Corpus",
    question: str,
    *,
    top_k: int = DEFAULT_SCHEMA_TOP_K,
    embedder: "Embedder | None" = None,
    schema_vectors: "dict[str, list[float]] | None" = None,
) -> list[str]:
    """Rank schemas against ``question`` and return up to ``top_k`` names.

    With an ``embedder``, rank by embedding similarity alone; without one, fall
    back to BM25. Embedding recall dominates for schema routing: BIRD questions
    rarely share identifiers with schema/table names, so lexical matching is weak.
    A probe over the 2030-question pool measured embedding-only recall@3 = 0.70 vs
    BM25 0.35 vs BM25+embedder RRF 0.535 — fusing the weak lexical signal
    measurably *drags the strong embedding ranking down*, so we do not fuse. When
    nothing scores, fail open to every schema (full span).

    ``schema_vectors`` (precomputed via :func:`embed_schema_documents`) skips
    re-embedding the schema docs on the hot path; only the question is embedded
    per call. Pass it on the serve path where the corpus is fixed.
    """
    schemas = list_schemas(corpus)
    if not schemas:
        return []
    if len(schemas) == 1:
        return schemas

    ranked: list[tuple[str, float]] = []
    if embedder is not None:
        from ..llm import cosine

        if schema_vectors is not None:
            vec_items = list(schema_vectors.items())
        else:  # embed the per-schema documents now (one batched call)
            docs = schema_documents(corpus)
            named = [(s, docs[s]) for s in docs if docs[s].strip()]
            vec_items = list(
                zip([s for s, _ in named], embedder.embed([t for _s, t in named]))
            )
        if vec_items:
            q_vec = embedder.embed_one(question)
            ranked = [
                (s, sc) for s, vec in vec_items if (sc := cosine(q_vec, vec)) > 0.0
            ]
            ranked.sort(key=lambda p: (-p[1], p[0]))
    if not ranked:  # no embedder, or it scored nothing → BM25 fallback
        ranked = BM25Index.from_documents(schema_documents(corpus)).rank(question)
    if not ranked:
        return schemas  # fail-open: no signal → keep all
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
