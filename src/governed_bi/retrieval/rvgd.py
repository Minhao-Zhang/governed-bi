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

import hashlib
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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
    from ..config import Settings
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


# Question words and function words carry no evidence about coverage. BM25's IDF is
# supposed to discount them, but on a single-schema corpus (a handful of documents)
# every term looks rare, so "what is the airspeed of a swallow" scores as well as a
# real question. Coverage is measured on content terms only.
_QUESTION_STOPWORDS = frozenset(
    """
    a an and any are as at be been by can did do doe for from get give had ha have how
    i in is it many me much of on or our show that the their there these this those to
    total us was we were what when where which who why will with you your
    """.split()
)


def content_terms(text: str) -> list[str]:
    """Question tokens that could carry coverage evidence (see ``_QUESTION_STOPWORDS``)."""
    return [tok for tok in tokenize(text) if len(tok) > 2 and tok not in _QUESTION_STOPWORDS]


def lexical_coverage(question: str, vocabulary: "set[str] | frozenset[str]") -> float:
    """Fraction of the question's content terms that appear in the corpus at all.

    ``0.0`` means the corpus contains no table, column, term or description word the
    question actually asks about — the signature of an out-of-corpus question. This
    is the evidence signal the assurance stamp was missing (AUDIT C2): the per-type
    budget in :func:`retrieve` has no minimum, and with an embedder every asset
    scores above zero, so ``top_k`` tables come back regardless of topicality and a
    clean run over them stamps ``unflagged``.

    Deliberately crude and vocabulary-level, not a threshold on a similarity score:
    a fused RRF rank is not comparable across questions, and raw BM25 on a
    few-document corpus is dominated by IDF noise. Coverage answers a narrower
    question honestly — did the user name anything this corpus knows about.

    A question with no content terms at all ("how many are there?") returns ``1.0``:
    there is nothing to be uncovered, and flagging it would report missing evidence
    where the real problem is an underspecified question.
    """
    terms = content_terms(question)
    if not terms:
        return 1.0
    return sum(1 for tok in terms if tok in vocabulary) / len(terms)


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

    def vocabulary(self) -> frozenset[str]:
        """Every term any indexed document contains — the corpus's known words.

        Document frequencies are already computed at construction, so this is a view
        over existing state rather than a second pass.
        """
        return frozenset(self._df)

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

    ``scores`` maps asset id -> **ranking score** for the selected assets that
    scored above zero; grounded additions (bound targets, base tables, columns) that
    did not themselves match are present in the id lists but not in ``scores``.

    The scale depends on the channel: raw BM25 with no embedder, and **Reciprocal
    Rank Fusion** when one is configured. RRF values are ~1/(60+rank) — small,
    bounded, and not comparable to BM25 magnitudes. They were documented and
    displayed to the model as "BM25 score" regardless (AUDIT R8), so anything
    reasoning about the magnitude was reading the wrong scale.
    """

    question: str
    table_ids: list[str] = field(default_factory=list)
    column_ids: list[str] = field(default_factory=list)
    term_ids: list[str] = field(default_factory=list)
    metric_ids: list[str] = field(default_factory=list)
    few_shot_ids: list[str] = field(default_factory=list)
    note_ids: list[str] = field(default_factory=list)
    # Keyword/PIN trigger hits (R7); never blended into RRF — unioned for on_match inject.
    triggered_note_ids: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    # Fraction of the question's content terms the corpus knows at all (see
    # :func:`lexical_coverage`). ``0.0`` means the question is about something this
    # corpus does not contain, however many tables the budget returned. ``None``
    # when retrieval did not run.
    lexical_coverage: float | None = None


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


def corpus_index_key(corpus: "Corpus") -> tuple[str, ...]:
    """A cache key for the *content* of a retrieval corpus.

    Asset ids, sorted. Object identity is useless here because the caller rebuilds
    the retrieval corpus per question (``filter_corpus_for_retrieval`` returns a fresh
    ``Corpus`` every time), and a content hash over every asset's full text would cost
    what it saves. Within one run a corpus is immutable and an id set determines its
    assets, so the id tuple is exactly as discriminating as the alternatives and
    O(assets) to compute — against an O(assets) *network* call, which is the point.
    """
    return tuple(sorted(a.id for a in corpus.assets))


#: Process-wide memo for the ASSET embedding index, keyed on the CONTENT of the
#: documents plus the embedder's identity — the same shape (and for the same
#: reason) as ``schema_router._SCHEMA_VECTOR_MEMO``. See
#: :meth:`RetrievalIndexCache.embedding` for why the per-graph layer above it is
#: not enough.
_ASSET_VECTOR_MEMO: dict[str, dict[str, list[float]]] = {}
_ASSET_VECTOR_LOCK = threading.Lock()


def _asset_vector_key(pairs: list[tuple[str, str]], embedder: "Embedder") -> str:
    """Identity of (these exact asset documents, this embedder).

    Content-hashed, NOT id-hashed. :func:`corpus_index_key` keys the per-graph
    layer on asset ids, which is exactly as discriminating as content *within one
    graph* — the corpus is immutable there. It is not sufficient process-wide: the
    eval ladder serves a baseline / seeded / curated corpus in the same process,
    and curation rewrites descriptions **in place under the same asset id**. On an
    id key the curated arm would silently score against the baseline arm's vectors,
    which is a wrong-answer bug that no test and no artifact would show.

    The embedder is in the key for the reason ``schema_router`` documents: vectors
    are comparable only within one model AND one width, and ``cosine`` returns 0.0
    on a length mismatch instead of raising, so a cross-model hit degrades silently.

    Hashing costs one pass over text we are about to send over the network anyway,
    and it runs only on a per-graph MISS (hundreds of times per run), never per
    question.
    """
    h = hashlib.sha256()
    for asset_id, doc in pairs:  # already in corpus order, which is stable
        h.update(asset_id.encode("utf-8"))
        h.update(b"\0")
        h.update(doc.encode("utf-8"))
        h.update(b"\0")
    model = getattr(getattr(embedder, "model", None), "model", None)
    dims = getattr(getattr(embedder, "model", None), "dimensions", None)
    h.update(f"|{type(embedder).__name__}|{model}|{dims}".encode())
    return h.hexdigest()


def _reset_asset_vector_memo_for_tests() -> None:
    """Drop the process-wide asset-vector memo (conftest autouse).

    A content-keyed process-wide cache makes test order load-bearing: two tests
    that build the same tiny fixture corpus with the same fake embedder would
    otherwise share a build, and whichever ran second would count zero embed calls
    and fail an assertion about cost through no fault of its own. Same argument as
    ``conftest._fresh_default_stack``.
    """
    with _ASSET_VECTOR_LOCK:
        _ASSET_VECTOR_MEMO.clear()


#: How many question vectors one cache keeps. A turn embeds its question twice
#: (schema routing, then ``retrieve``) and then embeds one query per
#: ``search_corpus`` tool call, so a handful is enough to collapse the duplicate
#: without holding a run's worth of 3072-float vectors alive.
_QUESTION_VECTOR_MAX = 8


class RetrievalIndexCache:
    """Per-graph memo for the two indexes ``retrieve`` would otherwise rebuild.

    ``retrieve`` used to call :func:`build_index` and ``build_embedding_index`` on
    every question. The BM25 rebuild is merely wasteful; the embedding rebuild is a
    live network round-trip that re-embeds every asset in the routed corpus — text
    that is identical for every question landing on that schema. On a pooled
    69-schema run that is thousands of redundant embedding calls where tens suffice.

    Deliberately an explicit object owned by the caller rather than a module-level
    dict: each eval worker thread owns its own serve graph, so a graph-scoped cache
    needs no lock and cannot leak across runs or bleed one arm's corpus into another's
    measurements. Unbounded on purpose — it is keyed by routed-corpus content, so its
    size is bounded by the number of distinct schema neighbourhoods a run actually
    visits, and every entry is live for the whole run.
    """

    __slots__ = (
        "_bm25",
        "_embed",
        "_schema_docs",
        "_schema_bm25",
        "_qvec",
        "hits",
        "misses",
        "embed_builds",
        "embed_shared",
        "qvec_hits",
    )

    def __init__(self) -> None:
        self._bm25: dict[tuple[str, ...], BM25Index] = {}
        self._embed: dict[tuple[str, ...], Any] = {}
        self._schema_docs: dict[tuple[str, ...], dict[str, str]] = {}
        self._schema_bm25: dict[tuple[str, ...], BM25Index] = {}
        self._qvec: dict[tuple[str, str], list[float]] = {}
        self.hits = 0
        self.misses = 0
        #: Asset-embedding builds that reached the network from this cache.
        self.embed_builds = 0
        #: Asset-embedding builds this cache got from the process-wide memo — i.e.
        #: the network calls a sibling worker's build paid for.
        self.embed_shared = 0
        #: Question embeddings served from the per-turn memo instead of the network.
        self.qvec_hits = 0

    def schema_docs(self, corpus: "Corpus") -> dict[str, str]:
        """Per-schema documents for the router, computed once per corpus.

        ``schema_documents`` runs ``Corpus.for_analyst()`` internally, and that
        deep-copies every asset via pydantic ``model_copy(deep=True)``. Called per
        question — which the router's BM25 path did — it measured **55% of the serve
        path's entire non-model CPU cost**: 24,768 asset deep-copies across 94
        questions, for a value that is identical every time because the corpus is
        fixed for the life of the graph.

        It matters beyond wall-clock. ``deepcopy`` is pure Python and holds the GIL, so
        this was the largest GIL-bound block on the hot path — it caps what raising
        ``--workers`` can actually buy, which is the opposite of what a concurrency
        knob is for.
        """
        key = corpus_index_key(corpus)
        got = self._schema_docs.get(key)
        if got is None:
            from .schema_router import schema_documents

            got = self._schema_docs[key] = schema_documents(corpus)
        return got

    def schema_bm25(self, corpus: "Corpus") -> BM25Index:
        """BM25 over the schema documents. Same argument as :meth:`schema_docs`, plus
        the index build itself, which was also per question."""
        key = corpus_index_key(corpus)
        got = self._schema_bm25.get(key)
        if got is None:
            got = self._schema_bm25[key] = BM25Index.from_documents(
                self.schema_docs(corpus)
            )
        return got

    def bm25(self, corpus: "Corpus") -> BM25Index:
        key = corpus_index_key(corpus)
        got = self._bm25.get(key)
        if got is None:
            self.misses += 1
            got = self._bm25[key] = build_index(corpus)
        else:
            self.hits += 1
        return got

    def embedding(self, corpus: "Corpus", embedder: "Embedder"):
        """Asset embedding index for ``corpus``. Two layers, on purpose.

        The per-graph dict below is the hot path: one dict lookup per question, no
        hashing, no lock, keyed on asset ids. It removes the *per-question* rebuild.

        It does **not** remove the *per-worker* rebuild, because each eval worker
        owns its own graph and therefore its own cache, and the workers all walk the
        same pooled question list. Measured on the 20260801 three-arm ladder
        (`runs/datalake/luna-max/20260801T-ladder`): 994 asset-embedding builds where
        171 distinct routed corpora were ever visited — 1.21M embedding tokens sent
        for 212k tokens of distinct text, 83% of the asset-embedding spend duplicated
        across threads. Worse than the token bill, the builds are *correlated in
        time*: workers start together and advance through the same region of the
        question list, so every schema boundary is a simultaneous N-way burst against
        a shared org TPM ceiling. That is the shape that took a run down on
        2026-08-01 — the schema-document embed had exactly this bug
        (``schema_router.embed_schema_documents``) and this is its sibling.

        So a miss falls through to a process-wide, CONTENT-keyed memo. Content, not
        ids: see :func:`_asset_vector_key`. The stored value is an immutable
        ``EmbeddingIndex`` (built once, read-only ``rank``), so sharing one object
        across worker threads needs no copy and no lock beyond the dict.

        Memory is the quiet half of the same win. A 3072-dim vector is 97 KB as a
        Python ``list[float]``, so ONE full set of the curated arm's 3686 asset
        vectors is 351 MB — and every worker's cache is unbounded and held one. The
        workers now share the vector lists rather than each holding their own, which
        on the 6-worker curated arm is roughly 1.7 GB of resident memory that stops
        existing.

        The key hashes ``pairs`` in corpus order, so two callers whose asset order
        differs would MISS rather than collide — a lost saving, never a wrong vector.
        In practice ``filter_corpus_for_retrieval`` preserves corpus order, so they
        agree.
        """
        from .embedding import EmbeddingIndex, index_documents

        key = corpus_index_key(corpus)
        got = self._embed.get(key)
        if got is not None:
            return got

        pairs = index_documents(corpus)
        if not pairs:
            got = self._embed[key] = EmbeddingIndex({})
            return got

        shared_key = _asset_vector_key(pairs, embedder)
        with _ASSET_VECTOR_LOCK:
            vectors = _ASSET_VECTOR_MEMO.get(shared_key)
        if vectors is not None:
            self.embed_shared += 1
            got = self._embed[key] = EmbeddingIndex(vectors)
            return got

        # Deliberately OUTSIDE the lock, for the reason
        # ``embed_schema_documents`` gives: holding it across a network round-trip
        # serialises every worker behind the first one. A race costs one redundant
        # request, not N.
        self.embed_builds += 1
        built = dict(
            zip(
                [asset_id for asset_id, _doc in pairs],
                embedder.embed([doc for _id, doc in pairs]),
            )
        )
        with _ASSET_VECTOR_LOCK:
            vectors = _ASSET_VECTOR_MEMO.setdefault(shared_key, built)
        got = self._embed[key] = EmbeddingIndex(vectors)
        return got

    def question_vector(self, embedder: "Embedder", text: str) -> list[float]:
        """Embed ``text`` once per turn instead of once per call site.

        Every turn embeds its question **twice** — once to rank schemas
        (``schema_router._embedding_ranking``) and once to rank assets
        (:func:`retrieve`) — with the same string, the same embedder, and two
        separate HTTP round-trips. On the 20260801 ladder the modal serve turn made
        exactly 2 embedding requests and 1145 of 2294 logged turns made no others, so
        this duplicate is roughly **half of all embedding traffic** the eval sends.
        Tokens are trivial (a question is ~20 of them); *requests* are not, and the
        embedding endpoint is the one that 429s first because it is shared org-wide
        with whatever else is running.

        Bounded at :data:`_QUESTION_VECTOR_MAX` with FIFO eviction. The cache is
        per-graph and each eval worker owns its graph, so there is no cross-thread
        access and no lock. Keyed on the embedder's identity as well as the text —
        vectors from two models are not interchangeable (see
        :func:`_asset_vector_key`).
        """
        model = getattr(getattr(embedder, "model", None), "model", None)
        dims = getattr(getattr(embedder, "model", None), "dimensions", None)
        key = (f"{type(embedder).__name__}|{model}|{dims}", text)
        got = self._qvec.get(key)
        if got is not None:
            self.qvec_hits += 1
            return got
        vec = embedder.embed_one(text)
        if len(self._qvec) >= _QUESTION_VECTOR_MAX:
            # dicts preserve insertion order, so this evicts the oldest entry.
            del self._qvec[next(iter(self._qvec))]
        self._qvec[key] = vec
        return vec


def phys_name_to_table_id(corpus: "Corpus") -> dict[str, str | None]:
    """Map physical table names to asset ids for few-shot SQL grounding.

    Qualified ``schema.table`` keys always resolve. Bare names resolve only when
    exactly one table corpus-wide carries that name; an ambiguous bare maps to
    ``None`` (same contract as :meth:`Corpus.table_by_name`). Built in one O(n)
    pass — callers must not replace this with per-name ``table_by_name`` loops.
    """
    phys_to_table: dict[str, str | None] = {}
    bare_seen: dict[str, int] = {}
    for a in corpus.assets:
        if not isinstance(a, TableAsset):
            continue
        bare = a.physical_name.lower()
        phys_to_table[f"{a.schema}.{bare}".lower()] = a.id
        bare_seen[bare] = bare_seen.get(bare, 0) + 1
        phys_to_table[bare] = a.id if bare_seen[bare] == 1 else None
    return phys_to_table


def _sql_table_ids(sql: str, phys_to_table: "dict[str, str | None]") -> list[str]:
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
        # Qualified first. Keying on the BARE name was last-write-wins across
        # schemas, so in a pooled lake a few-shot's `users` could ground to schema
        # B's table for a question routed to schema A (AUDIT R8). A bare reference in
        # the few-shot's own SQL still resolves by name, but only when that name is
        # unambiguous corpus-wide — see the ``None`` entries built below.
        qualified = f"{t.db}.{t.name}".lower() if t.db else None
        tid = phys_to_table.get(qualified) if qualified else None
        if tid is None:
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
    settings: "Settings | None" = None,
    triggered_note_ids: list[str] | None = None,
    index_cache: "RetrievalIndexCache | None" = None,
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

    ``index_cache`` (a :class:`RetrievalIndexCache`) memoises the BM25 and embedding
    indexes across questions that retrieve over the same corpus content. Without it
    every call re-embeds every asset — a network round-trip per question over text
    that never changes. Only the *question* embedding is genuinely per-call.
    """
    index = index_cache.bm25(corpus) if index_cache is not None else build_index(corpus)
    ranked = index.rank(question)
    # Measured against the index vocabulary, not against a score: see
    # :func:`lexical_coverage` for why a score threshold cannot do this job.
    coverage = lexical_coverage(question, index.vocabulary())
    if embedder is not None:
        from .embedding import build_embedding_index, fuse_rankings

        emb_index = (
            index_cache.embedding(corpus, embedder)
            if index_cache is not None
            else build_embedding_index(corpus, embedder)
        )
        # Via the cache when there is one: the schema router already embedded this
        # exact question a moment ago on the serve path (see
        # :meth:`RetrievalIndexCache.question_vector`).
        q_vec = (
            index_cache.question_vector(embedder, question)
            if index_cache is not None
            else embedder.embed_one(question)
        )
        emb_ranked = emb_index.rank(q_vec)
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
    # Two keyings for few-shot grounding: the qualified `schema.table` (always
    # unambiguous) and the bare name (only when ONE table corpus-wide carries it —
    # an ambiguous bare name maps to None and grounds nothing, rather than to
    # whichever table happened to be loaded last).
    phys_to_table = phys_name_to_table_id(corpus)
    for a in corpus.assets:
        if isinstance(a, TableAsset):
            for c in a.columns:
                col_owner[derive_column_id(a.id, c.physical_name)] = a.id

    # Ground/expand to a fixpoint so term -> metric -> base_table and
    # few-shot -> referenced-table chains close.
    selected: set[str] = set(top_ids)
    # Keyword PIN (never RRF): hard-include triggered notes into selected.
    pinned: list[str] = list(triggered_note_ids or [])
    if not pinned and settings is not None:
        from .triggers import fire_triggers

        pinned = fire_triggers(corpus, question, settings=settings)
    for nid in pinned:
        selected.add(nid)
    frontier: list[str] = list(selected)
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

    # scores: ranking score (BM25, or RRF when fused) for any selected asset that
    # actually matched (> 0),
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
        triggered_note_ids=list(pinned),
        scores=scores,
        lexical_coverage=coverage,
    )
