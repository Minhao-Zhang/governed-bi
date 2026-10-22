"""SHA-256, not ``hash()``: Python salts ``hash(str)`` per process, so another process would not match.
Signed buckets and L2 normalisation make cosine follow token overlap. A whole-text digest cannot rank.
``model`` fingerprints this algorithm's own output. A hand-kept version string would not move in cache keys.
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

#: 256: wide enough that bucket collisions do not dominate a summary, narrow enough for a fixture corpus.
DETERMINISTIC_DIMENSIONS = 256

#: Lowercased alphanumeric runs. A tokeniser with options would be a second unrecorded setting.
#: Not ``retrieve.lexical``'s ``\S+`` split: BM25 keeps punctuation, and that split
#: cannot match OpenAI's subword vocabulary either.
_BUCKET_TOKEN = re.compile(r"[a-z0-9]+")

#: Fixed text and width. The fingerprint tracks the algorithm, not ``dimensions``,
#: which is already its own cache-key component.
_FINGERPRINT_PROBE = "governed-bi deterministic embedder fingerprint probe"
_FINGERPRINT_WIDTH = 64


class DeterministicEmbedder(BaseEmbedder):
    """``salt`` separates two embedders of one width, the pair a vector cache must tell apart.
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
    buckets = [0.0] * dimensions
    for token in _BUCKET_TOKEN.findall(text.lower()):
        digest = hashlib.sha256(f"{salt}\x00{token}".encode()).digest()
        bucket = int.from_bytes(digest[:8], "big") % dimensions
        buckets[bucket] += 1.0 if digest[8] & 1 else -1.0

    norm = math.sqrt(sum(x * x for x in buckets))
    if norm == 0.0:
        # No tokens, or signs that cancel. ``semantic.cosine`` raises on a zero vector
        # and LanceDB drops one silently. The fallback is strictly positive.
        buckets = _dense_from_digest(text, dimensions, salt)
        norm = math.sqrt(sum(x * x for x in buckets))
    return [x / norm for x in buckets]


def _dense_from_digest(text: str, dimensions: int, salt: str) -> list[float]:
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
