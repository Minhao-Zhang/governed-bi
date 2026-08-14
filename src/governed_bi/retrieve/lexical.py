"""BM25 lexical channel with saturating normalisation and global IDF.

Scores are absolute: ``s / (s + k)``. ``restrict_to`` narrows candidates without
recomputation of IDF. Tokenizer keeps ``_``/``-``/``/`` inside tokens and strips
attached punctuation.
"""


from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

__all__ = ["BM25"]

#: Word token: keep ``_``/``-``/``/`` inside; strip attached punctuation.
_TOKEN = re.compile(r"[^\W_]+(?:[_\-/][^\W_]+)*", re.UNICODE)

#: The separators :data:`_TOKEN` holds inside a token, for splitting a compound into its parts.
_JOINERS = re.compile(r"[_\-/]")

#: English function words, excluded from :meth:`BM25.coverage` and from **nothing else**.
#:
#: Scoring needs no such list: a term in most documents has an IDF near zero, so BM25 already
#: discounts it by construction. ``coverage`` counts *membership* rather than weight, so it had no
#: such protection — measured on the 13,304-asset BIRD corpus, ``the`` appears in 76% of summaries
#: and ``a`` in 45%, and an unanswerable question therefore floored at 0.50 rather than near zero
#: (audit I4). That is the maximum-weakness reading of the one field ``weak_retrieval`` consults.
#:
#: **A document-frequency ceiling was tried first and does not work on this corpus.** The function
#: words are spread across 0.26 (``is``, ``on``) to 0.76 (``the``), while a schema corpus's real
#: content words — ``name``, ``id``, ``count`` — sit in the same band. Any ceiling low enough to
#: catch ``on`` also catches those. Hence a list, whose cost is that it is English-only.
#:
#: Deliberately narrow: function words only. ``count``, ``total``, ``average``, ``name``,
#: ``number`` and ``list`` are all plausible column names in a BI corpus and stay out — excluding
#: a word the corpus really does have would understate coverage, which is the same failure in the
#: other direction.
_STOPWORDS = frozenset(
    """
    a an the this that these those
    and or but nor if then than as so because
    of in on at to for from by with within without into onto about over under
    is are was were be been being do does did done has have had having
    i you he she it we they them him her his hers its their theirs our ours my mine your yours
    what which who whom whose when where why how
    all any both each every some not none only other others same such
    many much more most less least few fewer several very too also just even still
    there here could might must shall should would
    """.split()
)

#: Removed from the list above after review, because each is a plausible *content* word and
#: excluding one understates coverage — the I4 defect in the other direction. ``may`` is a month,
#: ``am`` is a time of day, ``no`` heads "invoice no", and ``can``/``will`` are ordinary nouns and
#: names. Measured: ``[("a", "orders order_date may june july revenue")]`` scored
#: ``coverage("orders in may")`` at **1.0** with only ``orders`` surviving, so a question the corpus
#: cannot answer reported maximum coverage. Kept as a list rather than deleted, because the next
#: person to extend the stopwords needs to see which candidates were rejected and why.
_REJECTED_AS_TOO_CONTENTFUL = frozenset({"may", "am", "no", "can", "will"})

# Okapi term-frequency parameters; constructor ``k`` is saturating normalisation.
_K1 = 1.2
_B = 0.75

def _tokenize(text: str) -> list[str]:
    """Lowercased word tokens with surrounding punctuation removed, compounds emitted **both ways**.

    Applied to **both** sides, so query and document meet in one vocabulary. No stemming and
    no stopword list: both are known gaps, and neither is needed to make ``food_type`` match
    ``food_type,``.

    **A compound yields the whole token and its parts** (``food_type`` → ``food_type``, ``food``,
    ``type``). Keeping only the whole token was audit I2, and it cost recall on the most ordinary
    question shape there is: measured before this change, ``search("food type")`` against a
    document containing ``food_type`` returned **0.0**, while ``search("food_type")`` returned
    0.193. A user types words; a corpus is full of snake_case identifiers, and column and table
    summaries are the densest source of them in the index.

    Both, rather than parts only, because the whole token is what makes an exact identifier query
    rank above a coincidental word match — dropping it would trade one recall failure for another.
    The cost is real and is the reason this is measured rather than assumed: emitting parts raises
    every compound document's length, which BM25 normalises against, and it puts the parts in the
    IDF vocabulary. The parts of a rare identifier are usually common words, so they carry little
    weight, which is the behaviour we want.
    """
    out: list[str] = []
    for match in _TOKEN.findall(text):
        token = match.lower()
        out.append(token)
        parts = _JOINERS.split(token)
        if len(parts) > 1:
            # Only when there was a separator, or every plain word would be emitted twice and its
            # term frequency would double.
            out.extend(parts)
    return out


class BM25:
    """Okapi-ish BM25 over ``(id, text)`` pairs, scores saturated into ``[0, 1]``."""

    def __init__(
        self,
        docs: Sequence[tuple[str, str]],
        *,
        k: float = 1.2,
    ) -> None:
        self.k = k
        self._doc_ids: list[str] = []
        self._tf: list[Counter[str]] = []
        self._dl: list[int] = []
        self._id_to_idx: dict[str, int] = {}
        self._allow: frozenset[str] | None = None
        df: Counter[str] = Counter()

        for doc_id, text in docs:
            tokens = _tokenize(text)
            tf = Counter(tokens)
            self._id_to_idx[doc_id] = len(self._doc_ids)
            self._doc_ids.append(doc_id)
            self._tf.append(tf)
            # **Length is the number of words, not the number of index terms** (review finding 11).
            # Counting the expanded list made a summary of four snake_case identifiers 13 tokens
            # long while a plain-English one stayed at 5, so `avgdl` moved and `_B` then penalised
            # the document that had not changed: measured on that pair, `search("customer")` scored
            # the plain document 0.3559 before audit I2 and **0.1566** after. A compound's parts are
            # synonyms of its whole token, not extra content, so they must not lengthen it — which
            # is the point of `_B` and the reason identifier-dense summaries were being taxed by the
            # very change meant to make them reachable.
            self._dl.append(len(_TOKEN.findall(text)))
            df.update(tf.keys())

        n = len(self._doc_ids)
        self._avgdl = (sum(self._dl) / n) if n else 0.0
        # Lucene-style IDF: always non-negative, built once from the full corpus.
        self._idf = {
            term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    @classmethod
    def _from_shared(
        cls,
        *,
        k: float,
        doc_ids: list[str],
        tf: list[Counter[str]],
        dl: list[int],
        avgdl: float,
        idf: dict[str, float],
        id_to_idx: dict[str, int],
        allow: frozenset[str],
    ) -> BM25:
        view = object.__new__(cls)
        view.k = k
        view._doc_ids = doc_ids
        view._tf = tf
        view._dl = dl
        view._avgdl = avgdl
        view._idf = idf
        view._id_to_idx = id_to_idx
        view._allow = allow
        return view

    def restrict_to(self, ids: Iterable[str]) -> BM25:
        """View over ``ids`` that reuses this index's global IDF."""
        return BM25._from_shared(
            k=self.k,
            doc_ids=self._doc_ids,
            tf=self._tf,
            dl=self._dl,
            avgdl=self._avgdl,
            idf=self._idf,
            id_to_idx=self._id_to_idx,
            allow=frozenset(ids),
        )

    def _raw_score(self, idx: int, query_tf: Counter[str]) -> float:
        score = 0.0
        dl = self._dl[idx]
        tf = self._tf[idx]
        avgdl = self._avgdl if self._avgdl else 1.0
        # **Distinct terms: the query's term frequency is not a weight** (audit I3).
        #
        # This loop used to multiply each term by its raw `qf`, which is Okapi's `k3 -> infinity`
        # limit, so a repeated word bought unbounded score. Measured against one document:
        # `search("cuisine")` = 0.4498 and `search("cuisine cuisine cuisine")` = 0.7103. Not
        # hypothetical, because the five facet rewriters *generate* these queries — a rewriter that
        # happens to say a keyword twice then outranks one that says it once on identical evidence,
        # and its phrasing is a model's, not a person's.
        #
        # Dropped rather than saturated with a `k3`, which is what Lucene does. A first attempt
        # used `k3 = 8` and the numbers said it was theatre: `qf = 2` still scored 1.8x and the
        # triple-repeat case moved only 0.6833 -> 0.6384. Evidence strength is the *document*
        # side's job and `_K1` already saturates it; the query side was only ever a long-query
        # weighting. What stays sensitive to phrasing is which terms appear at all, which is the
        # point.
        for term in query_tf:
            idf = self._idf.get(term)
            if idf is None:
                continue
            f = tf.get(term, 0)
            if f == 0:
                continue
            denom = f + _K1 * (1.0 - _B + _B * dl / avgdl)
            score += idf * (f * (_K1 + 1.0) / denom)
        return score

    def coverage(self, query: str) -> float | None:
        """Share of the query's distinct **content** terms this corpus has any document for.

        ``None``, never 0.0, when the query has no content terms: a blank query — or one that is
        nothing but function words — has no coverage to measure, and zero would claim the corpus
        matched something nobody asked.

        This is the out-of-corpus signal cosine cannot give — with an embedder every asset
        scores above zero, so an unanswerable question still returns ``top_k`` tables and
        stamps confidence. Distinct terms, not occurrences. Measured against ``_idf``, the
        full corpus vocabulary, not this restricted view: the question is whether *the
        corpus* has the vocabulary, not whether this facet's candidates do.

        :data:`_STOPWORDS` is subtracted **here and nowhere else** (audit I4). Counting them
        floored an unanswerable question at 0.50 on the BIRD corpus, because ``the``, ``a`` and
        ``of`` are in it and every English question contains them.

        Measured on corpus ``86ed1dbf``, 1,351 in-corpus questions against a 20-question
        hand-written out-of-corpus probe, against a criterion fixed before the run:

        =================  ==========  ==========
        arm                in-corpus   probe
        =================  ==========  ==========
        counting them        0.9488      0.5989
        this                 0.9135      0.3247
        =================  ==========  ==========

        Separation 0.3499 → 0.5888. In-corpus coverage falls 3.5pp, which is the cost of the
        list and is the number to watch: a larger fall would mean it is eating content words.
        The probe bounds nothing — it is 20 questions written by the author of the change — so
        what carries is the in-corpus column and the direction of the other.
        """
        # **Whole tokens, not the split forms** (review finding 9). Audit I2 made ``_tokenize``
        # emit a compound's parts beside it, which is right for *scoring* — a user types words and
        # the corpus is full of identifiers — and wrong here: it turned one query word into three
        # coverage terms whose parts are ordinary English. Measured against a corpus holding
        # ``food`` and ``type`` as separate words but no ``food_type``, ``coverage("food_type")``
        # read 0.667 where it had been 0.0. This field's declared unit is the share of the
        # question's own content terms the corpus knows, and a compound is one term.
        terms = {m.lower() for m in _TOKEN.findall(query)} - _STOPWORDS
        if not terms:
            return None
        return sum(1 for term in terms if term in self._idf) / len(terms)

    def search(self, query: str) -> list[tuple[str, float]]:
        """Return ``(id, saturated_score)`` for every document in this view."""
        query_tf = Counter(_tokenize(query))
        results: list[tuple[str, float]] = []
        for idx, doc_id in enumerate(self._doc_ids):
            if self._allow is not None and doc_id not in self._allow:
                continue
            raw = self._raw_score(idx, query_tf)
            saturated = raw / (raw + self.k) if raw > 0.0 else 0.0
            results.append((doc_id, saturated))
        return results
