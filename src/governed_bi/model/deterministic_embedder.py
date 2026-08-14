"""A real embedder that needs no provider. ``ports.py``'s ``Embedder`` adapter list names it as an adapter of
:class:`~governed_bi.ports.Embedder`. Three constraints hold it.

**Stable across processes, not merely within one.** Python salts ``hash()`` for ``str``
per process, so a ``hash()``-seeded embedder makes every cross-run comparison in a
model-free step meaningless while every single-run test still passes. The seed is SHA-256
and the acceptance criterion checks it in a subprocess under a different ``PYTHONHASHSEED``.

**Hashing, not random.** A digest of the whole text is deterministic but cannot rank: two
texts sharing every content word score no closer than two unrelated ones. Signed hashing
into buckets plus L2 normalisation makes cosine monotone in token overlap, which is what
lets a ``Channel.semantic``-only facet retrieve anything without a key.

**``model`` is a fingerprint of the algorithm's own output**, not a constant, because it is
in every cache key: edit the tokeniser, bucket function, sign bit or normalisation and it
moves on its own. A hand-maintained version string is what gets forgotten.

Not a general-purpose semantic model: no subword handling, no cross-lingual behaviour,
collisions grow as ``dimensions`` shrinks.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from typing import Sequence

from governed_bi.ports import Vector

from .embedder import BaseEmbedder

__all__ = ["DeterministicEmbedder"]

#: Default width. Wide enough that bucket collisions do not dominate a summary-length
#: text, narrow enough that a whole fixture corpus of vectors stays small.
DETERMINISTIC_DIMENSIONS = 256

#: Bucket keys: lowercased alphanumeric runs. The crudest rule that works, because a
#: tokeniser with options is a second knob nobody records.
#:
#: Deliberately **not** ``retrieve.lexical``'s ``\\S+`` split: that one keeps punctuation
#: attached because BM25 scores surface forms. Making them agree would assert an invariant
#: between the lexical channel and *one* semantic adapter that cannot hold for OpenAI's
#: subword vocabulary either.
_BUCKET_TOKEN = re.compile(r"[a-z0-9]+")

#: The probe :attr:`DeterministicEmbedder.model` fingerprints. Fixed text and fixed
#: width, so the identity tracks the *algorithm* and not the configured width — width
#: is already a separate component of every cache key.
_FINGERPRINT_PROBE = "governed-bi deterministic embedder fingerprint probe"
_FINGERPRINT_WIDTH = 64


class DeterministicEmbedder(BaseEmbedder):
    """Signed hashing-trick embedder over SHA-256. No network, no key, no provider.

    ``salt`` exists so a model-free experiment can hold two different embedders of the
    **same width** — the pair the vector cache has to tell apart, standing in for a
    1536-wide 3-large beside a 1536-wide 3-small.
    """

    def __init__(
        self,
        *,
        dimensions: int = DETERMINISTIC_DIMENSIONS,
        salt: str = "",
        batch_size: int | None = None,
    ) -> None:
        if int(dimensions) < 1:
            raise ValueError(f"dimensions must be positive, got {dimensions!r}")
        self._dimensions = int(dimensions)
        self._salt = str(salt)
        self._model: str | None = None
        if batch_size is not None:
            self.batch_size = int(batch_size)

    @property
    def model(self) -> str:
        """``deterministic:hashed-bow-sha256:<fingerprint>``, memoised.

        A digest of this algorithm's own vector for :data:`_FINGERPRINT_PROBE`: moves with
        the algorithm and the salt, stays put when only ``dimensions`` moves (width is
        already a separate cache-key component).
        """
        if self._model is None:
            probe = _hashed_bow(_FINGERPRINT_PROBE, _FINGERPRINT_WIDTH, self._salt)
            packed = struct.pack(f"<{len(probe)}d", *probe)
            fingerprint = hashlib.sha256(packed).hexdigest()[:12]
            self._model = f"deterministic:hashed-bow-sha256:{fingerprint}"
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        return [_hashed_bow(text, self._dimensions, self._salt) for text in texts]


def _hashed_bow(text: str, dimensions: int, salt: str) -> list[float]:
    """One L2-normalised vector. Pure: same arguments, same result, any process."""
    buckets = [0.0] * dimensions
    for token in _BUCKET_TOKEN.findall(text.lower()):
        digest = hashlib.sha256(f"{salt}\x00{token}".encode()).digest()
        bucket = int.from_bytes(digest[:8], "big") % dimensions
        buckets[bucket] += 1.0 if digest[8] & 1 else -1.0

    norm = math.sqrt(sum(x * x for x in buckets))
    if norm == 0.0:
        # No content tokens, or signed contributions that cancelled exactly. Rare but
        # possible, and ``semantic.cosine`` raises on a zero vector while LanceDB drops a
        # stored one from a cosine result in silence (``retrieve/vectors.py`` refuses it at
        # write time too). The fallback is strictly positive, so its norm cannot be zero.
        buckets = _dense_from_digest(text, dimensions, salt)
        norm = math.sqrt(sum(x * x for x in buckets))
    return [x / norm for x in buckets]


def _dense_from_digest(text: str, dimensions: int, salt: str) -> list[float]:
    """Strictly positive digest expansion. Only reached when the token pass cancels."""
    out: list[float] = []
    counter = 0
    while len(out) < dimensions:
        digest = hashlib.sha256(f"{salt}\x00{counter}\x00{text}".encode()).digest()
        for byte in digest:
            out.append((byte + 1) / 256.0)
            if len(out) == dimensions:
                break
        counter += 1
    return out
