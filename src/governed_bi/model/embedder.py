"""Blank input is refused here: OpenAI returns a vector for a blank string that can score above zero.
A zero vector looks orthogonal to ``cosine``. Dropping the blank would shorten the batch.
A reorder is silent. ``retrieve/index.py`` zips with ``strict=True`` and only a short result fails.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

from governed_bi.ports import Embedder, Vector
from governed_bi.registry.settings import setting_names

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "PROBE_TEXT",
    "BaseEmbedder",
    "refuse_blank",
    "embedding_settings",
    "embedding_provider",
]

#: Not OpenAI's 2048 limit. One failed request should not throw away a large batch of paid work.
DEFAULT_BATCH_SIZE = 256

#: Non-blank: blank is refused here, and an adapter must not exempt its own probe.
#: One spelling, shared, so two adapters cannot drift.
PROBE_TEXT = "probe"


def refuse_blank(texts: Sequence[str]) -> None:
    for position, text in enumerate(texts):
        if not isinstance(text, str):
            raise TypeError(
                f"embed input {position} is {type(text).__name__}, not str"
            )
        if not text.strip():
            raise ValueError(
                f"refusing to embed a blank string at input {position}: "
                "an empty or whitespace-only summary is a corpus defect, and OpenAI "
                "would return a vector for it that can score above zero and pollute a "
                "ranking (``ports.Embedder``'s no-blank-input rule)"
            )


class BaseEmbedder(Embedder, ABC):
    """Inherits ``ports.Embedder``. ``retrieve/`` and ``serve/`` see that declaration, not a second copy here.
    """

    batch_size: int = DEFAULT_BATCH_SIZE

    @abstractmethod
    def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        """In input order, at most ``batch_size`` texts."""

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """Check width after the batches, so a width learned from the response needs no extra probe.
        """
        items = list(texts)
        refuse_blank(items)
        if not items:
            return []

        out: list[Vector] = []
        for start in range(0, len(items), max(1, int(self.batch_size))):
            chunk = items[start:start + max(1, int(self.batch_size))]
            vectors = self._embed_batch(chunk)
            if len(vectors) != len(chunk):
                raise ValueError(
                    f"{type(self).__name__}._embed_batch returned {len(vectors)} "
                    f"vectors for {len(chunk)} inputs; one per input is the contract "
                    "(``ports.Embedder.embed``'s ordering rule)"
                )
            out.extend(vectors)

        width = int(self.dimensions)
        for position, vector in enumerate(out):
            if len(vector) != width:
                raise ValueError(
                    f"vector {position} is {len(vector)} wide but "
                    f"{type(self).__name__}.dimensions declares {width}; the declared "
                    "width is in every cache key, so a disagreement here is a "
                    "cross-model cache hit waiting to happen (``ports.Embedder.dimensions``)"
                )
        return out


def embedding_provider(model: str) -> str:
    """An unqualified id is refused. Guessing a gateway would record a call that did not happen.
    """
    prefix, separator, _rest = model.partition(":")
    if not separator or not prefix.strip():
        raise ValueError(
            f"embedder model {model!r} is not provider-qualified, so the gateway behind it "
            "cannot be recorded. ports.py requires a 'gateway:id' prefix"
        )
    return prefix.strip()


def embedding_settings(embedder: Any) -> dict[str, Any]:
    model = str(embedder.model)
    embedding_provider(model)
    values: dict[str, Any] = {
        "embedding_model": model,
        "embedding_dimensions": int(embedder.dimensions),
    }
    undeclared = sorted(set(values) - setting_names())
    if undeclared:
        raise KeyError(
            f"{undeclared} are not declared settings; a setting outside SETTING_REGISTER is "
            "outside the config hash and outside the comparability set"
        )
    return values
