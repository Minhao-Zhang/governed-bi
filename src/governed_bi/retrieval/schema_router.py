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

import logging
import re
from typing import TYPE_CHECKING, NamedTuple

from .. import prompts
from ..corpus.ids import derive_column_id
from ..corpus.schemas import (
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    NegativeExampleAsset,
    NoteAsset,
    TableAsset,
    TermAsset,
)
from .rvgd import BM25Index, asset_document

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..llm import Embedder
    from .rvgd import RetrievalIndexCache

logger = logging.getLogger("governed_bi.retrieval")

DEFAULT_SCHEMA_TOP_K = 3


def list_schemas(corpus: "Corpus") -> list[str]:
    """Distinct table schemas in ``corpus``, sorted ascending (deterministic).

    Namespace names only, so no exclusion filter runs here (it would cost a copy
    of the whole corpus on the per-question routing path). A schema whose every
    table is ``governance.excluded`` therefore still appears — but it contributes
    no table text to its routing document and no tables to the picker prompt, so
    it can only ever be ranked on its own name and supplies nothing downstream.
    """
    return sorted({a.schema for a in corpus.assets if isinstance(a, TableAsset)})


def _analyst_tables(
    corpus: "Corpus", schemas: "frozenset[str] | None" = None
) -> dict[str, TableAsset]:
    """Analyst-visible tables (optionally only ``schemas``'), keyed by asset id.

    Every text surface in this module is either indexed for ranking or pasted
    into the picker prompt, so all of them owe D6: no ``governance.excluded``
    table, no excluded column. The filter delegates to ``Corpus.for_analyst()``
    rather than re-testing ``governance.excluded`` here, because a second
    definition of "excluded" is exactly how the picker summary and the routing
    index came to disagree — the summary hid excluded columns while the index
    that ranks schemas embedded them verbatim.

    Callers are documented as passing the ``for_analyst()`` view, but an
    unenforced caller contract is not a boundary: the pooled data-lake driver fed
    this module a raw corpus, and a routing document naming an excluded PII column
    has already leaked it into the index with nothing raised. ``schemas`` narrows
    the copy ``for_analyst()`` makes to the tables about to be rendered.
    """
    from ..corpus.loader import Corpus

    src = [
        a
        for a in corpus.assets
        if isinstance(a, TableAsset) and (schemas is None or a.schema in schemas)
    ]
    return {t.id: t for t in Corpus(assets=src).for_analyst().tables()}


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

    Only Analyst-visible tables (:func:`_analyst_tables`) contribute, so an
    excluded table's name/description — and a metric or term grounded to it —
    never enter the ranking index.
    """
    visible = _analyst_tables(corpus, frozenset({schema}))
    parts: list[str] = [schema]
    for a in corpus.assets:
        if isinstance(a, TableAsset) and a.schema == schema:
            if (table := visible.get(a.id)) is not None:
                parts.append(asset_document(table))
        elif isinstance(a, MetricAsset) and a.base_table in visible:
            parts.append(asset_document(a))
        elif isinstance(a, FewShotAsset) and a.schema == schema:
            parts.append(asset_document(a))
        elif isinstance(a, TermAsset):
            owner = _term_binding_table(corpus, a)
            if owner in visible:
                parts.append(asset_document(a))
    return " ".join(p for p in parts if p)


def schema_documents(corpus: "Corpus") -> dict[str, str]:
    """All per-schema documents in a **single pass** over the corpus.

    Equivalent to ``{s: schema_document(corpus, s) for s in list_schemas(corpus)}``
    but O(assets) instead of O(schemas × assets) — it buckets each asset into its
    schema once rather than rescanning the whole corpus per schema.
    """
    visible = _analyst_tables(corpus)
    table_schema = {tid: t.schema for tid, t in visible.items()}
    parts: dict[str, list[str]] = {s: [s] for s in list_schemas(corpus)}
    for a in corpus.assets:
        if isinstance(a, TableAsset):
            if (table := visible.get(a.id)) is not None:
                parts[a.schema].append(asset_document(table))
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
    settings: "Settings | None" = None,
    index_cache: "RetrievalIndexCache | None" = None,
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

    ``index_cache`` memoises the per-schema documents and the BM25 index over them.
    Both are pure functions of the corpus, and building them per question dominated
    the serve path's non-model CPU — ``schema_documents`` runs ``for_analyst()``,
    which deep-copies every asset. Pass it wherever the corpus outlives one call.

    When ``settings.pin_triggers_enabled``, keyword-triggered notes whose scope
    includes ``schema:`` PINs are hard-prepended (cap ``pin_max``), never RRF-blended.
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
            docs = (
                index_cache.schema_docs(corpus)
                if index_cache is not None
                else schema_documents(corpus)
            )
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
        ranked = (
            index_cache.schema_bm25(corpus)
            if index_cache is not None
            else BM25Index.from_documents(schema_documents(corpus))
        ).rank(question)
    if not ranked:
        return schemas  # fail-open: no signal → keep all
    out = [s for s, _ in ranked[:top_k]]

    if settings is not None and settings.pin_triggers_enabled:
        from .triggers import fire_triggers

        pinned_schemas: list[str] = []
        for nid in fire_triggers(corpus, question, settings=settings):
            note = corpus.by_id(nid)
            if note is None:
                continue
            for sid in getattr(note, "scope", ()) or ():
                if isinstance(sid, str) and sid.startswith("schema:"):
                    name = sid.removeprefix("schema:")
                    if name in schemas and name not in pinned_schemas:
                        pinned_schemas.append(name)
        if pinned_schemas:
            # PINs are ADDITIVE: prepend pins but keep every top_k-ranked schema,
            # so a wrong/uncertified pin can never evict the correct schema from
            # the shortlist (GATE-ADV-WRONG-NOTE). Cap = top_k + pins (<= top_k+pin_max).
            merged = list(pinned_schemas)
            for s in out:
                if s not in merged:
                    merged.append(s)
            out = merged[: top_k + len(pinned_schemas)]
    return out


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


def _schema_pick_summary(
    corpus: "Corpus",
    schema: str,
    *,
    max_tables: int = 15,
    max_columns: int = 0,
) -> str:
    """Compact one-block summary of a schema for the LLM picker: name + tables
    (physical name + short description), optionally with each table's column names.

    Sibling schemas on the same topic often have table descriptions that read
    alike, leaving the picker nothing to separate them on; their column names
    differ. ``max_columns > 0`` puts that vocabulary in front of the model. It is
    capped per table rather than emitted whole: a wide table would otherwise
    dominate the picker context across every candidate. ``max_columns = 0``
    restores the names-only summary.

    Excluded tables and columns are absent (:func:`_analyst_tables`): the picker
    prompt is a path to a model like any other, so D6 applies to it too.
    """
    tables = sorted(
        _analyst_tables(corpus, frozenset({schema})).values(),
        key=lambda a: a.physical_name,
    )
    lines = [f"schema: {schema}"]
    for a in tables[:max_tables]:
        desc = (a.description or "").strip().replace("\n", " ")
        if len(desc) > 90:
            desc = desc[:90] + "…"
        line = f"  - {a.physical_name}" + (f": {desc}" if desc else "")
        if max_columns > 0:
            cols = [c.physical_name for c in a.columns]
            if cols:
                shown = ", ".join(cols[:max_columns])
                if len(cols) > max_columns:
                    shown += "…"
                line += f" [cols: {shown}]"
        lines.append(line)
    if len(tables) > max_tables:
        lines.append(f"  … ({len(tables) - max_tables} more tables)")
    return "\n".join(lines)


#: Default picker system prompt. Derived from the registry (see
#: ``governed_bi.prompts``) so the text a run stamps as ``schema_pick=v1`` is the
#: text it actually sent.
SCHEMA_PICK_SYSTEM = prompts.get("schema_pick").text


def _is_word_char(ch: str) -> bool:
    """Schema names are snake_case, so ``_`` is part of a name, not a boundary."""
    return ch.isalnum() or ch == "_"


def _candidate_mentions(probe: str, candidates: list[str]) -> list[str]:
    """Candidates genuinely named in ``probe``, matched by whole-token span.

    Two rules, both load-bearing:

    - **Word boundaries.** A bare substring test matches inside unrelated words —
      a candidate named ``sales`` "appears" in ``wholesales``, and with short
      names almost any prose matches something. Only occurrences delimited by a
      non-word character on each side count.
    - **Span subsumption.** An occurrence is dropped when a longer candidate's
      occurrence covers that exact position, so ``"the answer is
      food_inspection_2"`` yields one mention rather than two.

    Together these keep ``"not ice_hockey_draft; use hockey"`` at two mentions
    (``hockey`` also occurs outside the longer span, so the reply is ambiguous)
    while ``"use world_development_indicators"`` stays at one.
    """
    low = probe.lower()
    spans: list[tuple[int, int, str]] = []
    for c in candidates:
        cl = c.lower()
        if not cl:
            continue
        start = low.find(cl)
        while start != -1:
            end = start + len(cl)
            before_ok = start == 0 or not _is_word_char(low[start - 1])
            after_ok = end == len(low) or not _is_word_char(low[end])
            if before_ok and after_ok:
                spans.append((start, end, c))
            start = low.find(cl, start + 1)
    kept: set[str] = set()
    for s, e, c in spans:
        covered = any(
            os_ <= s and e <= oe and (oe - os_) > (e - s)
            for os_, oe, oc in spans
            if oc != c
        )
        if not covered:
            kept.add(c)
    return sorted(kept)


# An answer the model *labelled*, e.g. "Final answer: x" / "Pick - x". ``schema``
# is deliberately not a label: the candidate summaries are themselves headed
# "schema: <name>", so a reply that echoes them would resolve to the last-listed
# candidate — the exact silent mis-pick this parser exists to prevent.
_ANSWER_LABEL_RE = re.compile(
    r"(?:final\s+answer|final|answer|chosen(?:\s+schema)?|selected(?:\s+schema)?"
    r"|selection|pick(?:ed)?|result|conclusion|decision)\s*[:=\-–—]\s*(?P<value>.+)",
    re.IGNORECASE,
)


def _parse_schema_reply(reply: str, candidates: list[str]) -> "SchemaPick | None":
    """Resolve a picker reply to one of ``candidates``, or ``None`` if it cannot.

    The prompt makes the model *reason about every candidate* before answering, so
    a reply almost always names the schemas it rejected. It also designates one
    place for the answer — a bare name on the final line — so the passes are
    ordered by distance from that instruction, and a pick read from further away
    comes back carrying a :class:`SchemaPick` ``fallback`` reason:

    1. **Final line, exact bare name.** Precisely what was asked for.
    2. **A labelled answer** ("``Final answer: x``"), scanned bottom-up. A label
       states the decision, so it outranks an earlier *incidental* mention
       wherever it sits. Without this pass, a bare candidate name used as a
       reasoning heading beat the model's real labelled answer — and the row
       still scored as a genuine pick, so the bias was invisible.
    3. **Exact bare name above the final line** — flagged. The answer really is
       often on a line of its own with a parenthetical about the rejected sibling
       after it, so this still beats reading that parenthetical as the answer;
       but the designated line did not resolve, which is what a heading match
       looks like too, and the two are not separable here. Flagging keeps both
       out of ``schema_pick_accuracy_excl_fallback`` rather than letting a
       heading match inflate it.
    4. **Prose mention**, when a line names exactly ONE candidate (see
       :func:`_candidate_mentions`) — clean on the final line, flagged above it.

    A line naming several candidates is genuinely ambiguous and is skipped.
    Picking the longest — or the first — is an arbitrary tiebreak that resolves
    ``"not ice_hockey_draft; use hockey"`` confidently and wrongly. ``None`` sends
    the caller to the logged rank-1 fallback, which is honest and visible.
    """
    text = (reply or "").strip()
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    by_lower = {c.lower(): c for c in candidates}
    last = len(lines) - 1

    final_exact = by_lower.get(lines[last].lower())
    if final_exact is not None:
        return SchemaPick(final_exact)

    for probe in reversed(lines):
        # Leading markdown only, never ``_``: it is part of a snake_case name.
        labelled = _ANSWER_LABEL_RE.match(probe.lstrip("*#> \t`"))
        if labelled is None:
            continue
        mentions = _candidate_mentions(labelled.group("value"), candidates)
        if len(mentions) == 1:
            return SchemaPick(mentions[0])

    for probe in reversed(lines[:last]):
        exact = by_lower.get(probe.lower())
        if exact is not None:
            return SchemaPick(exact, "parsed_nonfinal_line")

    # Per line only: candidate names never span a newline and subsumption is
    # positional, so a whole-text pass could not surface a mention that no single
    # line already contains.
    for idx in range(last, -1, -1):
        mentions = _candidate_mentions(lines[idx], candidates)
        if len(mentions) == 1:
            reason = None if idx == last else "parsed_nonfinal_line"
            return SchemaPick(mentions[0], reason)
    return None


class SchemaPick(NamedTuple):
    """One routing decision, and whether the model cleanly made it.

    ``fallback`` is ``None`` only for a pick the model stated where the prompt
    asked for it. Otherwise it names why the row is not clean evidence of a model
    decision: the rank-1 candidate was substituted (``"call_failed"`` /
    ``"unparseable_reply"``), or the pick had to be read off a line other than the
    designated final one (``"parsed_nonfinal_line"`` — the model's own name is
    still used, only the *measurement* is qualified). Without it, a stretch of
    proxy timeouts is scored as that many rank-1 picks the model never made, and a
    reasoning heading mistaken for an answer is scored as a real pick — both
    biasing pick accuracy in an unknown direction.
    """

    schema: str
    fallback: str | None = None


def pick_schema(
    corpus: "Corpus",
    question: str,
    candidates: list[str],
    *,
    chat,
    max_tables: int = 15,
    max_columns: int = 0,
    system_prompt: str | None = None,
) -> SchemaPick:
    """LLM picks the single best schema from ``candidates`` (pipeline-design §5.1).

    Retrieval has already shortlisted ``candidates`` in relevance order. This node
    shows the LLM each candidate's tables (and, when ``max_columns > 0``, their
    column names) and asks for exactly one schema, so the serve path can scope to a
    single schema with no cross-schema joins.

    ``system_prompt`` injects a registered ``schema_pick`` variant (resolved once
    where the serve stack is built, never per turn); ``None`` keeps ``v1``.

    Deterministic guards: 0 candidates → ``""``; 1 candidate → it, no LLM call.
    An unparseable reply or a failed call degrades to ``candidates[0]`` (top
    retrieval rank) rather than raising, logged **and** flagged on the returned
    :class:`SchemaPick` so the eval can separate degraded rows from genuine ones —
    as is a reply the parser could only resolve off a non-final line.
    """
    if not candidates:
        return SchemaPick("")
    if len(candidates) == 1:
        return SchemaPick(candidates[0])

    summaries = "\n\n".join(
        _schema_pick_summary(corpus, s, max_tables=max_tables, max_columns=max_columns)
        for s in candidates
    )
    user = (
        f"Question: {question}\n\n"
        f"Candidate schemas (most relevant first):\n{summaries}\n\n"
        f"Answer with exactly one of: {', '.join(candidates)}"
    )
    try:
        reply = (
            chat.complete(system_prompt or SCHEMA_PICK_SYSTEM, user) or ""
        ).strip()
    except Exception:
        logger.warning(
            "schema-pick LLM call failed; falling back to top-ranked candidate %r",
            candidates[0],
            exc_info=True,
        )
        return SchemaPick(candidates[0], "call_failed")

    picked = _parse_schema_reply(reply, candidates)
    if picked is None:
        logger.warning(
            "schema-pick reply matched no candidate (%r); falling back to "
            "top-ranked candidate %r",
            reply[:200],
            candidates[0],
        )
        return SchemaPick(candidates[0], "unparseable_reply")
    if picked.fallback:
        # INFO, not WARNING: the pick stands and this shape is common enough that
        # a warning per question would bury the two that mean data loss.
        logger.info(
            "schema-pick reply put no bare candidate on its final line; read %r "
            "off an earlier line (%s), reply=%r",
            picked.schema,
            picked.fallback,
            reply[:200],
        )
    return picked


def filter_corpus_for_retrieval(corpus: "Corpus", schemas: frozenset[str]) -> "Corpus":
    """Subset of ``corpus`` whose assets are in scope for the routed schemas.

    - Tables: ``table.schema in schemas``
    - Joins: both endpoints' schemas ⊆ routed set
    - Metrics: ``base_table`` in kept tables
    - Few-shots: ``few_shot.schema in schemas``
    - Terms: unbound, or binding resolves to a kept table
    - Notes / negatives: always kept (governance / refuse-gate)

    The result is an Analyst view: this corpus is what the RVGD index is built
    from next, so an excluded asset surviving routing would be ranked and
    surfaced by id. Scoping first keeps ``for_analyst()``'s copy to the routed
    schemas instead of the whole lake.
    """
    from ..corpus.loader import Corpus

    if not schemas:
        return corpus.for_analyst()

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
        elif isinstance(a, (NoteAsset, NegativeExampleAsset)):
            kept.append(a)

    return Corpus(assets=kept).for_analyst()
