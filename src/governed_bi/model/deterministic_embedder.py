"""A real embedder that needs no provider. ``ports.py:108``.

*"the last is what makes ADR 0005's implementation steps 6-9 model-free, and it is a
third adapter, not a courtesy fake."* Two consequences follow from that sentence and
neither is optional.

**It must be stable across processes, not merely within one.** Python salts ``hash()``
for ``str`` per process by default, so an embedder seeded from ``hash()`` is stable
inside a run and different in the next. Every single-run test passes and every
cross-run comparison in a model-free step is meaningless — including the ones ADR
0005 §1.7 says are the point of the seed ("implementation steps 6-9 are measurable
without a single curator run"). So the seed is SHA-256, and the acceptance criterion
checks it in a subprocess under a different ``PYTHONHASHSEED``.

**It must be a hashing embedder, not a random one.** A vector derived from a digest of
the whole text is deterministic and useless: two texts that share every content word
score no closer than two unrelated ones, so a model-free run measures a semantic
channel that cannot rank. This is the hashing trick — tokens are hashed into buckets
with a signed contribution and the result is L2-normalised — which makes cosine a
monotone function of token overlap. That is what lets the ``facet_example`` facet, whose
only declared channel is ``Channel.semantic`` (``register/facets.py:116``), retrieve
anything at all without a key.

**Why ``model`` is a fingerprint rather than a constant.** ``ports.py:125`` puts
``model`` in every cache key, and a fake whose identity never moves makes every cached
vector from every past version look current. So the identity is *derived from the
algorithm's own output* on a fixed probe at a fixed width: edit the tokeniser, the
bucket function, the sign bit or the normalisation and ``model`` moves on its own. A
hand-maintained version string is the thing that gets forgotten, and this repository has
already shipped one identity that failed to identify (``corpus_content_hash ==
"unknown"``, compared equal to itself).

Not a general-purpose semantic model: no subword handling, no cross-lingual behaviour,
and collisions grow as ``dimensions`` shrinks. It is a measurement instrument for the
model-free steps, and it is honest about being one.
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

#: Bucket keys: lowercased alphanumeric runs. Deliberately the crudest rule that works,
#: because a tokeniser with options is a second knob nobody records.
#:
#: **Not** ``retrieve.lexical``'s tokeniser, and the difference is deliberate rather than
#: an oversight the one-implementation gate should waive. That one is ``\\S+`` over
#: whitespace, which keeps punctuation attached because BM25's term frequencies are
#: computed over surface forms and its IDF is built from the same split. This is a
#: hash-bucket key inside one adapter — the provider-side analogue is OpenAI's own
#: subword vocabulary, which bears no relation to BM25's split either. Making the two
#: agree would assert an invariant between the lexical channel and *one* semantic
#: adapter that cannot hold for the other one.
_BUCKET_TOKEN = re.compile(r"[a-z0-9]+")

#: The probe :attr:`DeterministicEmbedder.model` fingerprints. Fixed text and fixed
#: width, so the identity tracks the *algorithm* and not the configured width — width
#: is already a separate component of every cache key.
_FINGERPRINT_PROBE = "governed-bi deterministic embedder fingerprint probe"
_FINGERPRINT_WIDTH = 64


class DeterministicEmbedder(BaseEmbedder):
    """Signed hashing-trick embedder over SHA-256. No network, no key, no provider.

    ``salt`` exists so a model-free experiment can hold two *different* embedders of
    the **same width**: that is the pair the vector cache has to tell apart, and
    ``retrieve/semantic.py:18`` cannot help there because the widths agree. It is the
    stand-in for a 1536-wide ``text-embedding-3-large`` beside a 1536-wide
    ``text-embedding-3-small`` — width-identical and semantically unrelated.
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

        The fingerprint is a digest of this algorithm's own vector for
        :data:`_FINGERPRINT_PROBE`, so it moves when the algorithm moves and when the
        salt moves, and stays put when only ``dimensions`` moves.
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
        # No content tokens, or a set whose signed contributions cancelled exactly.
        # Vanishingly rare and not impossible, and ``cosine`` raises on a zero vector
        # (semantic.py:35) — so falling through would turn a curiosity into a dead
        # turn. The fallback is strictly positive, so its norm cannot be zero either.
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
