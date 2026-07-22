"""RVGD retrieval over the Analyst-visible corpus view (docs/analyst.md step 5).

This slice implements the deterministic core of RVGD: a pure-Python **BM25**
index over the corpus assets (the "V"/lexical channel) plus a small **Ground**
expansion that walks the same relationships the graph projection encodes
(``docs/architecture.md`` "Storage ... (RVGD)"):

- **term -> binding**: a bound ``term`` pulls in the table or metric it binds to
  (the BINDS_TO edge in ``graph/projection.py``).
- **metric -> base_table**: a selected ``metric`` pulls in the table it is
  derived from (the DERIVED_FROM edge).
- **table -> columns**: a selected ``table`` contributes its column ids, using
  the loader's column-id derivation (``corpus.ids.derive_column_id``).

Input is expected to be ``Corpus.for_analyst()`` so the tier contract is
structurally guaranteed (no Audit, no ``governance.excluded`` assets); the index
is built from whatever assets the passed corpus exposes. The index is a
rebuildable projection, so it is rebuilt per call rather than cached here.

BM25 (Robertson) with the Lucene non-negative idf variant and defaults
``k1=1.5``, ``b=0.75``. No third-party dependency: document frequencies and
lengths are computed from the asset corpus itself.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..corpus.ids import derive_column_id
from ..corpus.schemas import (
    FewShotAsset,
    MetricAsset,
    NegativeExampleAsset,
    NoteAsset,
    TableAsset,
    TermAsset,
)

if TYPE_CHECKING:
    from ..corpus import Asset, Corpus
    from ..llm import Embedder

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# camelCase / PascalCase boundaries: a lower/digit followed by an upper
# (customerID -> customer ID), and an acronym run followed by a word
# (HTTPServer -> HTTP Server), so physical names split into their words.
_CAMEL_1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")


def _stem(token: str) -> str:
    """A minimal, symmetric plural stemmer (applied to both index and query).

    Only collapses simple English plurals so ``transactions`` matches
    ``transaction`` and ``companies`` matches ``company``. Applied identically on
    both sides, so even an imperfect stem stays consistent (never splits a match).
    Short tokens and ``-ss`` words (``address``, ``class``) are left alone.
    """
    if len(token) > 3 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Split into normalized terms: camelCase-aware, lowercased, plural-stemmed.

    ``CustomerID`` -> ``customer``, ``id``; ``PurchasePrice`` -> ``purchase``,
    ``price``; ``transactions`` -> ``transaction``. Digits are kept. BM25 indexes
    and queries both run through this, so the two stay consistent.
    """
    split = _CAMEL_2.sub(r"\1 \2", _CAMEL_1.sub(r"\1 \2", text))
    return [_stem(tok) for tok in _TOKEN_RE.findall(split.lower())]


def asset_document(asset: "Asset") -> str:
    """Build the human-language text document indexed for ``asset``.

    Only the fields a curator writes in natural language are indexed, per asset
    type. Types without a language surface (e.g. ``join``) yield an empty
    document and so never match.
    """
    if isinstance(asset, TableAsset):
        parts: list[str] = [asset.physical_name, asset.description or "", asset.grain or ""]
        for col in asset.columns:
            parts.append(col.physical_name)
            parts.append(col.description or "")
            if col.role is not None:
                parts.append(col.role.value)
        return " ".join(parts)
    if isinstance(asset, TermAsset):
        return " ".join([asset.name, *asset.synonyms])
    if isinstance(asset, MetricAsset):
        return " ".join([asset.name, asset.expression, *asset.dimensions])
    if isinstance(asset, FewShotAsset):
        return asset.question
    if isinstance(asset, NoteAsset):
        return asset.summary
    if isinstance(asset, NegativeExampleAsset):
        return " ".join([asset.pattern, *asset.example_questions])
    return ""


@dataclass
class BM25Index:
    """A small, self-contained BM25 index over pre-tokenized documents.

    Build with :meth:`from_documents` (raw text) or the constructor (tokens).
    Document frequencies, lengths, and the average length are computed once at
    construction; :meth:`rank` scores every document against a query.
    """

    documents: dict[str, list[str]]
    k1: float = 1.5
    b: float = 0.75

    def __post_init__(self) -> None:
        self._doc_ids: list[str] = list(self.documents)
        self._tf: dict[str, Counter[str]] = {
            doc_id: Counter(tokens) for doc_id, tokens in self.documents.items()
        }
        self._len: dict[str, int] = {
            doc_id: len(tokens) for doc_id, tokens in self.documents.items()
        }
        self._n = len(self._doc_ids)
        total_len = sum(self._len.values())
        self._avgdl = (total_len / self._n) if self._n else 0.0
        self._df: Counter[str] = Counter()
        for tf in self._tf.values():
            for term in tf:  # Counter keys are the unique terms in the doc
                self._df[term] += 1

    @classmethod
    def from_documents(
        cls, texts: dict[str, str], *, k1: float = 1.5, b: float = 0.75
    ) -> "BM25Index":
        """Build an index from raw ``asset_id -> text`` documents."""
        return cls({doc_id: tokenize(text) for doc_id, text in texts.items()}, k1=k1, b=b)

    def _idf(self, term: str) -> float:
        # Lucene-style idf: always non-negative, so common terms never subtract.
        df = self._df.get(term, 0)
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def score(self, doc_id: str, query_terms: list[str]) -> float:
        """BM25 score of one document against the (de-duplicated) query terms."""
        tf = self._tf[doc_id]
        dl = self._len[doc_id]
        length_norm = 1.0 - self.b + self.b * (dl / self._avgdl if self._avgdl else 0.0)
        total = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            total += self._idf(term) * (f * (self.k1 + 1.0)) / (f + self.k1 * length_norm)
        return total

    def rank(self, question: str) -> list[tuple[str, float]]:
        """Score every document against ``question``; return the > 0 matches.

        Deterministically ordered by score descending, then id ascending. The
        query is reduced to its unique terms (sorted, for stable summation).
        """
        query_terms = sorted(set(tokenize(question)))
        if not query_terms:
            return []
        scored = [(doc_id, self.score(doc_id, query_terms)) for doc_id in self._doc_ids]
        scored = [(doc_id, s) for doc_id, s in scored if s > 0.0]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored


@dataclass(frozen=True)
class RetrievalResult:
    """Typed, deterministic retrieval output (the contract the agent core, ``analyst.agent``, reads).

    ``scores`` maps asset id -> BM25 score for the selected assets that scored
    above zero; grounded additions (bound targets, base tables, columns) that
    did not themselves match are present in the id lists but not in ``scores``.
    """

    question: str
    table_ids: list[str] = field(default_factory=list)
    column_ids: list[str] = field(default_factory=list)
    term_ids: list[str] = field(default_factory=list)
    metric_ids: list[str] = field(default_factory=list)
    few_shot_ids: list[str] = field(default_factory=list)
    note_ids: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


# Field weight for the lexical index (BM25F-by-repetition). Governed-BI thesis:
# the curator-authored NATURAL LANGUAGE (a table's description / grain, a column's
# description) is the trusted match surface, and the raw physical identifiers are
# a weak, possibly-adversarial signal (cryptic or decoy names under obfuscation).
# Raising this boost leans retrieval onto the curated semantics; ``1`` is flat
# (raw names and curated language weigh the same).
#
# TUNING: this is one of the retrieval knobs to calibrate for production, together
# with ``vector_weight`` and the per-type budgets in ``retrieve()``, the schema
# shortlist ``DEFAULT_SCHEMA_TOP_K``, and BM25 ``k1``/``b``. Calibrate against
# ``eval/retrieval_eval.py`` on the OBFUSCATED set (``--gold-sql-field sql_rename``),
# where curated-vs-raw actually diverges. Held flat (=1) for now, pending that run.
_SEMANTIC_BOOST = 1  # flat for now; raise (>1) to prefer curated language (see TUNING)


def bm25_tokens(asset: "Asset") -> list[str]:
    """Field-weighted token stream a table indexes for BM25 (see ``_SEMANTIC_BOOST``).

    Curated natural-language fields are boosted over the raw physical identifiers.
    Non-table assets tokenize their :func:`asset_document` unchanged — their whole
    document is already curator-authored language (term synonyms, metric names,
    few-shot questions), so no per-field reweighting applies.
    """
    if isinstance(asset, TableAsset):
        toks: list[str] = list(tokenize(asset.physical_name))  # raw identifier: weight 1
        toks += tokenize(asset.description or "") * _SEMANTIC_BOOST
        toks += tokenize(asset.grain or "") * _SEMANTIC_BOOST
        for col in asset.columns:
            toks += tokenize(col.physical_name)  # raw identifier: weight 1
            toks += tokenize(col.description or "") * _SEMANTIC_BOOST
            if col.role is not None:
                toks += tokenize(col.role.value)
        return toks
    return tokenize(asset_document(asset))


def build_index(corpus: "Corpus") -> BM25Index:
    """Build a BM25 index over one field-weighted document per asset."""
    return BM25Index({a.id: bm25_tokens(a) for a in corpus.assets})


def _sql_table_ids(sql: str, phys_to_table: dict[str, str]) -> list[str]:
    """Table asset ids referenced by ``sql`` (best-effort, for few-shot grounding).

    Parses the SQL and maps each base-table name to a table id by physical name
    (case-insensitive). A parse failure or an unknown name simply yields fewer
    ids — this feeds grounding, never a safety gate.
    """
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql)
    except Exception:
        return []
    if tree is None:
        return []
    ids: list[str] = []
    for t in tree.find_all(exp.Table):
        tid = phys_to_table.get(t.name.lower())
        if tid is not None:
            ids.append(tid)
    return ids


def retrieve(
    corpus: "Corpus",
    question: str,
    *,
    top_k: int = 8,
    embedder: "Embedder | None" = None,
    few_shot_k: int = 3,
    term_k: int = 5,
    metric_k: int = 5,
    note_k: int = 5,
    vector_weight: float = 1.0,
) -> RetrievalResult:
    """Rank corpus assets against ``question``, then ground/expand.

    1. Rank every asset by BM25 (lexical). When an ``embedder`` is given, also rank
       by embedding cosine (the V channel) and fuse the two with Reciprocal Rank
       Fusion.
    2. Keep the top matches **per asset type** — up to ``top_k`` tables plus
       separate budgets for few-shots / terms / metrics / notes. A single pooled
       cut let a flood of matching few-shots crowd every table out of the result
       (and grounding cannot recover a table nothing points to); per-type budgets
       guarantee tables their slots.
    3. Ground deterministically (fixpoint): a ``term`` pulls in its binding, a
       ``metric`` pulls in its base table, a ``few-shot`` pulls in the tables its
       gold SQL references, and every selected table contributes its columns.
    4. Partition the selected ids into the typed id lists (score desc, id asc).

    ``corpus`` is expected to be a ``Corpus.for_analyst()`` view.
    """
    index = build_index(corpus)
    ranked = index.rank(question)
    if embedder is not None:
        from .embedding import build_embedding_index, fuse_rankings

        emb_index = build_embedding_index(corpus, embedder)
        emb_ranked = emb_index.rank(embedder.embed_one(question))
        # ``vector_weight`` tunes the semantic channel's pull relative to lexical
        # (1.0 = equal). For governed BI an exact lexical name-match is usually the
        # stronger signal, so this can be dialed below 1.
        ranked = fuse_rankings(ranked, emb_ranked, weights=[1.0, vector_weight])

    # One id -> asset map for this call; ``corpus.by_id`` is a linear scan, and the
    # steps below look assets up across the whole ranked list (confidence sort,
    # budgeting, grounding, partition), so scanning per lookup would be O(assets^2).
    by_id: dict[str, "Asset"] = {a.id: a for a in corpus.assets}

    # Curator confidence is a mild prior: on an otherwise-tied score, prefer the
    # higher-confidence (more trusted) asset. It only breaks ties — it never
    # reorders assets whose scores differ — so a weak-but-curated asset can't leapfrog
    # a strong lexical match.
    def _conf(doc_id: str) -> float:
        c = getattr(by_id.get(doc_id), "confidence", None)
        if isinstance(c, (int, float)):
            return float(c)
        v = getattr(c, "value", None)
        return float(v) if isinstance(v, (int, float)) else 0.5

    ranked.sort(key=lambda pair: (-pair[1], -_conf(pair[0]), pair[0]))
    score_map: dict[str, float] = dict(ranked)

    # Per-type budgets: tables get ``top_k`` slots regardless of how many few-shots
    # / terms match, so lexically-noisy curated content never starves the tables.
    budgets: dict[type, int] = {
        TableAsset: top_k,
        FewShotAsset: few_shot_k,
        TermAsset: term_k,
        MetricAsset: metric_k,
        NoteAsset: note_k,
    }
    kept: dict[type, int] = {}
    top_ids: list[str] = []
    for doc_id, _score in ranked:
        asset = by_id.get(doc_id)
        cls = type(asset)
        budget = budgets.get(cls, 0)  # unbudgeted types (e.g. negatives) are dropped
        if kept.get(cls, 0) < budget:
            kept[cls] = kept.get(cls, 0) + 1
            top_ids.append(doc_id)

    # A term may bind to a column, but columns are inline (not top-level assets),
    # so grounding resolves a bound column id to its owning table. This map makes
    # that resolution deterministic and mirrors validate.py's reference check.
    col_owner: dict[str, str] = {}
    phys_to_table: dict[str, str] = {}  # lower(physical_name) -> table id, for few-shot grounding
    for a in corpus.assets:
        if isinstance(a, TableAsset):
            phys_to_table[a.physical_name.lower()] = a.id
            for c in a.columns:
                col_owner[derive_column_id(a.id, c.physical_name)] = a.id

    # Ground/expand to a fixpoint so term -> metric -> base_table and
    # few-shot -> referenced-table chains close.
    selected: set[str] = set(top_ids)
    frontier: list[str] = list(top_ids)
    while frontier:
        asset = by_id.get(frontier.pop())
        expansions: list[str] = []
        if isinstance(asset, TermAsset) and asset.binding is not None:
            # A column binding grounds the owning table (surfacing the column too);
            # a table/metric binding grounds that asset directly.
            expansions.append(col_owner.get(asset.binding.asset_id, asset.binding.asset_id))
        elif isinstance(asset, MetricAsset):
            expansions.append(asset.base_table)
        elif isinstance(asset, FewShotAsset):
            # A retrieved exemplar is strong evidence of which tables answer a
            # similar question; ground the tables its (curated) gold SQL references.
            expansions.extend(_sql_table_ids(asset.sql, phys_to_table))
        for target in expansions:
            if target and target not in selected:
                selected.add(target)
                frontier.append(target)

    def _ordered(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda i: (-score_map.get(i, 0.0), -_conf(i), i))

    table_ids: list[str] = []
    term_ids: list[str] = []
    metric_ids: list[str] = []
    few_shot_ids: list[str] = []
    note_ids: list[str] = []
    for asset_id in selected:
        asset = by_id.get(asset_id)
        if isinstance(asset, TableAsset):
            table_ids.append(asset_id)
        elif isinstance(asset, TermAsset):
            term_ids.append(asset_id)
        elif isinstance(asset, MetricAsset):
            metric_ids.append(asset_id)
        elif isinstance(asset, FewShotAsset):
            few_shot_ids.append(asset_id)
        elif isinstance(asset, NoteAsset):
            note_ids.append(asset_id)

    table_ids = _ordered(table_ids)

    column_ids: list[str] = []
    for table_id in table_ids:
        table = corpus.by_id(table_id)
        if isinstance(table, TableAsset):
            for col in table.columns:
                column_ids.append(derive_column_id(table_id, col.physical_name))
    column_ids = _ordered(column_ids)

    # scores: BM25 score for any selected asset that actually matched (> 0),
    # inserted in the deterministic display order.
    scores = {
        asset_id: score_map[asset_id]
        for asset_id in sorted(selected, key=lambda i: (-score_map.get(i, 0.0), i))
        if asset_id in score_map
    }

    return RetrievalResult(
        question=question,
        table_ids=table_ids,
        column_ids=column_ids,
        term_ids=_ordered(term_ids),
        metric_ids=_ordered(metric_ids),
        few_shot_ids=_ordered(few_shot_ids),
        note_ids=_ordered(note_ids),
        scores=scores,
    )
