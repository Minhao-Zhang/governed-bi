"""BM25 lexical channel with saturating normalisation and global IDF.

Scores are absolute: ``s / (s + k)`` so a weak query's top hit is not normalised
up to the same value as a strong query's. ``restrict_to`` narrows the candidate
set without recomputing IDF — otherwise a second routing pass rescores on a
different scale than the first.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

__all__ = ["BM25"]

_TOKEN = re.compile(r"\S+")

# Okapi term-frequency parameters; constructor ``k`` is saturating normalisation.
_K1 = 1.2
_B = 0.75


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


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
            self._dl.append(len(tokens))
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
        for term, qf in query_tf.items():
            idf = self._idf.get(term)
            if idf is None:
                continue
            f = tf.get(term, 0)
            if f == 0:
                continue
            denom = f + _K1 * (1.0 - _B + _B * dl / avgdl)
            score += idf * (f * (_K1 + 1.0) / denom) * qf
        return score

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
