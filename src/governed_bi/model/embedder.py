"""The part of :class:`~governed_bi.ports.Embedder` that is the same on every adapter.

Three of the port's guarantees are adapter-independent — one vector per input in input
order, every vector exactly ``dimensions`` wide, a refusal on blank input — so they live
here once and each adapter supplies only ``_embed_batch``. A template method the adapter
cannot bypass, rather than a helper it can forget to call.

Blank input is refused here and not only at the caller: OpenAI returns a vector for a blank
string that can score above zero, and a rule only the caller enforces holds until a new
caller exists.

Batching is the only place ordering can break, and a reorder is silent —
``retrieve/index.py`` zips with ``strict=True``, which catches a *short* result but lets a
reordered one give every asset another asset's vector.
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

#: Inputs per provider request. Not a provider maximum (OpenAI accepts 2048) but a size that
#: keeps one failed request from discarding a large batch's worth of paid work.
DEFAULT_BATCH_SIZE = 256

#: Shortest non-blank probe, for adapters that can only learn their served model or their
#: vector width from a response. Non-blank because :func:`refuse_blank` refuses one, and an
#: adapter exempting its own probe from its own rule is exactly what that function closes.
#: Here rather than in each adapter: two spellings of one probe.
PROBE_TEXT = "probe"


def refuse_blank(texts: Sequence[str]) -> None:
    """Raise if any element is empty or whitespace-only. ``ports.Embedder``'s no-blank-input rule.

    Raises rather than dropping (which would make the result shorter than the input) or
    substituting a zero vector, which renders "not measured" as "scores nothing" —
    indistinguishable to ``cosine`` from a real orthogonal vector.
    """
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
    """Every adapter's shared half. Subclasses implement ``_embed_batch`` only.

    **Inherits the declaration rather than restating it.** ``model`` and ``dimensions`` were
    declared here as abstract properties *and* in :class:`~governed_bi.ports.Embedder`, which
    made two answers to "what must an embedder do" — and the port is the one ``retrieve/`` and
    ``serve/`` annotate against, so a rule added to only this copy would bind nothing they can
    see. Subclassing the Protocol keeps this class to what it actually contributes: the
    ``batch_size`` policy, the three checks in :meth:`embed`, and the ``_embed_batch`` hook.

    Explicit inheritance, not the structural conformance this used to rely on, because the
    abstract members are what stop an adapter shipping without ``model``; the port's own
    docstring records why they carry ``@abstractmethod``. It costs nothing — ``model`` is layer
    11 and ``ports`` is layer 3 — and the Protocol stays ``runtime_checkable``, so a test
    double still satisfies it without coming through here.
    """

    #: Inputs per call to :meth:`_embed_batch`.
    batch_size: int = DEFAULT_BATCH_SIZE

    @abstractmethod
    def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        """Embed one batch, at most :attr:`batch_size` long, **in input order**."""

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """Embed ``texts``, one vector each, in input order.

        The width check runs after the batches, so an adapter whose ``dimensions`` is only
        knowable from a response does not pay a probe request it is about to answer anyway.
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
    """The prefix of a provider-qualified embedder model, or a refusal if it has none.

    Guessing a gateway for an unqualified id records a measurement that did not happen.
    """
    prefix, separator, _rest = model.partition(":")
    if not separator or not prefix.strip():
        raise ValueError(
            f"embedder model {model!r} is not provider-qualified, so the gateway behind it "
            "cannot be recorded. ports.py requires a 'gateway:id' prefix"
        )
    return prefix.strip()


def embedding_settings(embedder: Any) -> dict[str, Any]:
    """The embedder's model and width, both declared settings.

    An unqualified model is refused: the prefix is the only thing that keeps two
    gateways out of one cache, and it already lives on ``embedding_model``.
    """
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
