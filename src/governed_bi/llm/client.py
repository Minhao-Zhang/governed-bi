"""LLM + embedding seams (OpenAI by project decision), with offline defaults.

Two protocols the rest of the system programs against:

- :class:`ChatClient` - one system+user prompt in, one completion string out.
  The server SQL generator and the curator proposer call this.
- :class:`Embedder` - texts in, vectors out. The retrieval vector channel and the
  SQL semantic cache call this.

Two implementations of each:

- OpenAI-backed (:class:`OpenAiEmbedder`): the real
  clients. ``openai`` is imported **lazily** inside the methods, so importing this
  module never requires the dependency; construct via ``from_config(ModelConfig)``
  and the API key is read from the environment at call time (never stored).
- Deterministic (:class:`StaticChatClient`, :class:`HashingEmbedder`): no network,
  no key, no dependency. They are the test doubles *and* the offline default (the
  same "deterministic core + real seam" split as ``TemplateSqlGenerator`` /
  ``HeuristicProposer``), so the whole pipeline runs end to end without a model.

Model choices come from :class:`governed_bi.config.ModelConfig`. The concrete
provider is swappable: nothing outside this module imports ``openai``.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..config import ModelConfig


# --------------------------------------------------------------------------- #
# Protocols
# --------------------------------------------------------------------------- #


@runtime_checkable
class ChatClient(Protocol):
    """A single-shot chat completion: system + user prompt -> completion text."""

    def complete(self, system: str, user: str) -> str:
        ...


@runtime_checkable
class Embedder(Protocol):
    """Turns text into dense vectors. ``embed`` batches; ``embed_one`` is sugar."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_one(self, text: str) -> list[float]:
        ...


# --------------------------------------------------------------------------- #
# Vector helpers
# --------------------------------------------------------------------------- #


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is a zero
    vector or lengths differ)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------- #
# OpenAI-backed clients (lazy import; key from env)
# --------------------------------------------------------------------------- #


def _require_openai():
    """Import ``openai`` lazily with a clear install hint if it is missing."""
    try:
        import openai  # noqa: PLC0415
    except ModuleNotFoundError as err:  # pragma: no cover - exercised only sans dep
        raise ModuleNotFoundError(
            "The OpenAI clients need the 'openai' package. Install the extra: "
            "`uv sync --extra openai` (or `pip install 'governed-bi[openai]'`)."
        ) from err
    return openai


class OpenAiEmbedder:
    """:class:`Embedder` over the OpenAI embeddings API (lazy import, key from env)."""

    def __init__(
        self,
        *,
        model: str,
        dimensions: int | None = None,
        api_key_env: str = "OPENAI_API_KEY",
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.api_key_env = api_key_env
        self._client = None

    @classmethod
    def from_config(cls, models: "ModelConfig") -> "OpenAiEmbedder":
        return cls(
            model=models.embedding_model,
            dimensions=models.embedding_dimensions,
            api_key_env=models.api_key_env,
        )

    def _ensure_client(self):
        if self._client is None:
            import os  # noqa: PLC0415

            openai = _require_openai()
            key = os.environ.get(self.api_key_env)
            if not key:
                raise RuntimeError(
                    f"No API key in ${self.api_key_env}. Set it before embedding."
                )
            self._client = openai.OpenAI(api_key=key)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._ensure_client()
        kwargs: dict = {"model": self.model, "input": texts}
        if self.dimensions:
            kwargs["dimensions"] = self.dimensions
        response = client.embeddings.create(**kwargs)
        # The API returns embeddings in input order (index-stamped defensively).
        ordered = sorted(response.data, key=lambda d: d.index)
        return [list(d.embedding) for d in ordered]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


# --------------------------------------------------------------------------- #
# Deterministic offline implementations (test doubles + no-model default)
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class StaticChatClient:
    """A scripted :class:`ChatClient` for offline runs and tests.

    Returns queued responses in order; once exhausted it repeats the last one (so
    a repair loop that over-asks still terminates deterministically). Every
    ``(system, user)`` pair is recorded in :attr:`calls` for assertions.
    """

    def __init__(self, responses: str | list[str]) -> None:
        self._responses = [responses] if isinstance(responses, str) else list(responses)
        if not self._responses:
            raise ValueError("StaticChatClient needs at least one response")
        self._i = 0
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        idx = min(self._i, len(self._responses) - 1)
        self._i += 1
        return self._responses[idx]


class HashingEmbedder:
    """A deterministic, dependency-free :class:`Embedder`.

    Hashes each token into one of ``dimensions`` buckets (feature hashing) and
    L2-normalises the resulting bag-of-words vector. It has no semantic
    understanding - it is a stand-in that makes the vector channel and the SQL
    cache runnable and testable offline, and a swappable default for
    :class:`OpenAiEmbedder`. Identical text always yields an identical vector.
    """

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def _bucket(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dimensions

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for token in _TOKEN_RE.findall(text.lower()):
            vec[self._bucket(token)] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0.0:
            vec = [v / norm for v in vec]
        return vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]
