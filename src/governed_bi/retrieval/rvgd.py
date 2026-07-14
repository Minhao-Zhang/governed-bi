"""RVGD retrieval over the server-visible corpus view (docs/server.md step 5).

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

Input is expected to be ``Corpus.for_server()`` so the tier contract is
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
    RuleAsset,
    TableAsset,
    TermAsset,
)

if TYPE_CHECKING:
    from ..corpus import Asset, Corpus
    from ..llm import Embedder

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase and split on any non-alphanumeric run (keeps digits)."""
    return _TOKEN_RE.findall(text.lower())


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
    if isinstance(asset, RuleAsset):
        return asset.statement
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
    """Typed, deterministic retrieval output (the contract the agent core, ``server.agent``, reads).

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
    rule_ids: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


def build_index(corpus: "Corpus") -> BM25Index:
    """Build a BM25 index over one document per asset in ``corpus``."""
    return BM25Index.from_documents({a.id: asset_document(a) for a in corpus.assets})


def retrieve(
    corpus: "Corpus",
    question: str,
    *,
    top_k: int = 8,
    embedder: "Embedder | None" = None,
) -> RetrievalResult:
    """Rank corpus assets against ``question``, then ground/expand.

    1. Rank every asset by BM25 (lexical). When an ``embedder`` is given, also rank
       by embedding cosine (the V channel) and fuse the two with Reciprocal Rank
       Fusion; keep the ``top_k`` fused matches. With no embedder the ranking is
       pure BM25 (byte-for-byte the prior behavior).
    2. Ground deterministically (fixpoint): a ``term`` pulls in its binding, a
       ``metric`` pulls in its base table, and every selected table contributes
       its columns.
    3. Partition the selected ids into the typed id lists (score desc, id asc).

    ``corpus`` is expected to be a ``Corpus.for_server()`` view.
    """
    index = build_index(corpus)
    ranked = index.rank(question)
    if embedder is not None:
        from .embedding import build_embedding_index, fuse_rankings

        emb_index = build_embedding_index(corpus, embedder)
        emb_ranked = emb_index.rank(embedder.embed_one(question))
        ranked = fuse_rankings(ranked, emb_ranked)
    score_map: dict[str, float] = dict(ranked)
    top_ids = [doc_id for doc_id, _ in ranked[:top_k]]

    # A term may bind to a column, but columns are inline (not top-level assets),
    # so grounding resolves a bound column id to its owning table. This map makes
    # that resolution deterministic and mirrors validate.py's reference check.
    col_owner: dict[str, str] = {}
    for a in corpus.assets:
        if isinstance(a, TableAsset):
            for c in a.columns:
                col_owner[derive_column_id(a.id, c.physical_name)] = a.id

    # Ground/expand to a fixpoint so term -> metric -> base_table chains close.
    selected: set[str] = set(top_ids)
    frontier: list[str] = list(top_ids)
    while frontier:
        asset = corpus.by_id(frontier.pop())
        expansions: list[str] = []
        if isinstance(asset, TermAsset) and asset.binding is not None:
            # A column binding grounds the owning table (surfacing the column too);
            # a table/metric binding grounds that asset directly.
            expansions.append(col_owner.get(asset.binding.asset_id, asset.binding.asset_id))
        elif isinstance(asset, MetricAsset):
            expansions.append(asset.base_table)
        for target in expansions:
            if target and target not in selected:
                selected.add(target)
                frontier.append(target)

    def _ordered(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda i: (-score_map.get(i, 0.0), i))

    table_ids: list[str] = []
    term_ids: list[str] = []
    metric_ids: list[str] = []
    few_shot_ids: list[str] = []
    rule_ids: list[str] = []
    for asset_id in selected:
        asset = corpus.by_id(asset_id)
        if isinstance(asset, TableAsset):
            table_ids.append(asset_id)
        elif isinstance(asset, TermAsset):
            term_ids.append(asset_id)
        elif isinstance(asset, MetricAsset):
            metric_ids.append(asset_id)
        elif isinstance(asset, FewShotAsset):
            few_shot_ids.append(asset_id)
        elif isinstance(asset, RuleAsset):
            rule_ids.append(asset_id)

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
        rule_ids=_ordered(rule_ids),
        scores=scores,
    )
