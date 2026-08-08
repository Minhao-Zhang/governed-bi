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

from governed_bi.ports import Vector
from governed_bi.register.knobs import knob_names

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "BaseEmbedder",
    "refuse_blank",
    "embedding_knobs",
]

#: Inputs per provider request. Not a provider maximum (OpenAI accepts 2048) but a size that
#: keeps one failed request from discarding a large batch's worth of paid work.
DEFAULT_BATCH_SIZE = 256


def refuse_blank(texts: Sequence[str]) -> None:
    """Raise if any element is empty or whitespace-only. ``ports.py:118``.

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
                "ranking (ports.py:118)"
            )


class BaseEmbedder(ABC):
    """Every adapter's shared half. Subclasses implement ``_embed_batch`` only.

    Satisfies :class:`~governed_bi.ports.Embedder` structurally — the Protocol is
    ``runtime_checkable``, so ``isinstance`` holds without inheriting from it.
    """

    #: Inputs per call to :meth:`_embed_batch`.
    batch_size: int = DEFAULT_BATCH_SIZE

    @property
    @abstractmethod
    def model(self) -> str:
        """Provider-qualified model identity. Part of every cache key."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector width. Part of every cache key."""

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
                    "(ports.py:113)"
                )
            out.extend(vectors)

        width = int(self.dimensions)
        for position, vector in enumerate(out):
            if len(vector) != width:
                raise ValueError(
                    f"vector {position} is {len(vector)} wide but "
                    f"{type(self).__name__}.dimensions declares {width}; the declared "
                    "width is in every cache key, so a disagreement here is a "
                    "cross-model cache hit waiting to happen (ports.py:117)"
                )
        return out


def embedding_knobs(embedder: Any) -> dict[str, Any]:
    """The two declared knobs an embedder contributes to ``knobs_resolved``.

    ``embedding_model`` and ``embedding_dimensions`` are ``Role.comparability``; without
    them two ladders differing only in embedder compare as one experiment. Names are checked
    against :func:`~governed_bi.register.knobs.knob_names` because a typo'd name ships a
    literal no knob backs, so the config hash does not move when the real knob does.
    """
    values: dict[str, Any] = {
        "embedding_model": str(embedder.model),
        "embedding_dimensions": int(embedder.dimensions),
    }
    undeclared = sorted(set(values) - knob_names())
    if undeclared:
        raise KeyError(
            f"{undeclared} are not declared knobs; a knob outside KNOB_REGISTER is "
            "outside the config hash and outside the comparability set"
        )
    return values
