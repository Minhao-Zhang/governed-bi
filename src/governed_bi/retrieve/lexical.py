"""Scores are ``s / (s + k)``, absolute. ``restrict_to`` reuses this IDF. See ADR 0005."""


from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

__all__ = ["BM25"]

_TOKEN = re.compile(r"[^\W_]+(?:[_\-/][^\W_]+)*", re.UNICODE)

_JOINERS = re.compile(r"[_\-/]")

#: Scoring already discounts a term in most documents, via IDF. ``coverage`` counts
#: membership, so function words floored unanswerable questions. A document-frequency
#: ceiling also catches real column words, so this list is English function words only.
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

#: Left out of the list above: each is a plausible content word (a month, a time,
#: "invoice no", ordinary nouns). Excluding one understates coverage.
_REJECTED_AS_TOO_CONTENTFUL = frozenset({"may", "am", "no", "can", "will"})

# Constructor ``k`` saturates the score. These two are the term-frequency parameters.
_K1 = 1.2
_B = 0.75

def _tokenize(text: str) -> list[str]:
    """Lowercased tokens, applied to both the query and the document.

    A compound is emitted whole and as its parts, so ``food type`` meets ``food_type`` and an exact identifier still
    outranks a word match. A plain word is not emitted twice.
    """
    out: list[str] = []
    for match in _TOKEN.findall(text):
        token = match.lower()
        out.append(token)
        parts = _JOINERS.split(token)
        if len(parts) > 1:
            out.extend(parts)
    return out


class BM25:
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
            # Length is the word count, not the expanded term list. A compound's parts
            # are synonyms of the whole token, and counting them lets ``_B`` tax
            # identifier-dense summaries that did not gain content.
            self._dl.append(len(_TOKEN.findall(text)))
            df.update(tf.keys())

        n = len(self._doc_ids)
        self._avgdl = (sum(self._dl) / n) if n else 0.0
        # Non-negative IDF, once, over the full corpus. ``restrict_to`` must not recompute it.
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
        # Distinct terms only. Weighting by how often the query says a word let a
        # rewriter that repeats a keyword outrank one that says it once. A saturated
        # query-side factor was tried; a double mention still scored far higher.
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
        """Share of the query's distinct content terms the corpus contains.

        ``None`` when there are no content terms: zero would claim a match nobody asked for. Measured on the full-corpus
        vocabulary, and :data:`_STOPWORDS` apply only here.
        """
        # Whole tokens, not the split forms. Splitting is right for scoring and wrong
        # here: one query word would become three terms, and a compound the corpus
        # lacks would look partly covered.
        terms = {m.lower() for m in _TOKEN.findall(query)} - _STOPWORDS
        if not terms:
            return None
        return sum(1 for term in terms if term in self._idf) / len(terms)

    def search(self, query: str) -> list[tuple[str, float]]:
        query_tf = Counter(_tokenize(query))
        results: list[tuple[str, float]] = []
        for idx, doc_id in enumerate(self._doc_ids):
            if self._allow is not None and doc_id not in self._allow:
                continue
            raw = self._raw_score(idx, query_tf)
            saturated = raw / (raw + self.k) if raw > 0.0 else 0.0
            results.append((doc_id, saturated))
        return results
