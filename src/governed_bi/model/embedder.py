"""The part of :class:`~governed_bi.ports.Embedder` that is the same on every adapter.

``ports.py:112-125`` states four things beyond the signatures, and three of them are
adapter-independent: one vector per input in input order, every vector exactly
``dimensions`` wide, and a refusal on an empty or whitespace-only string. Those live
here, once, and each adapter supplies only ``_embed_batch``.

**Why a base class rather than a shared helper each adapter calls.** The one-implementation
gate (``tools/check_one_implementation.py``) is aimed at exactly this shape: two adapters
written by two hands, each locally correct, differing in the invariant neither of them
owns. v1's version of that was ``cosine`` returning ``0.0`` on a width mismatch in one
place and raising in another. A helper an adapter *may* call is a helper an adapter can
forget to call; a template method it cannot bypass is a boundary.

**The empty-string refusal is here and not only at the caller.** ``ports.py:118`` records
the reason the adapters cannot paper over it — OpenAI accepts a blank string and returns a
vector that can score above zero, Bedrock Titan rejects it and takes the whole turn down —
and then says callers must not pass one. ``corpus.assets`` and ``corpus.index`` both
enforce it upstream, which is two enforcement points for a rule with three enforcers'
worth of callers. A rule only the caller enforces holds until a new caller exists, so it
is restated at the boundary that would otherwise have to trust them.

**Batching is here too, and the order guarantee lives with it.** ``ports.py:113`` calls a
reordered result "a bug in the adapter, never something the caller reconciles", and
batching is the only place order can break: ``retrieve/index.py`` zips vectors against the
texts it asked for with ``strict=True``, so a *short* result raises and a *reordered* one
does not — every asset silently takes another asset's vector and no artifact anywhere
disagrees.
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

#: Inputs per provider request. Not a provider maximum — OpenAI accepts 2048 — but a
#: size that keeps one failed request from discarding a large batch's worth of paid
#: work, and that a test can cross cheaply to exercise the ordering path.
DEFAULT_BATCH_SIZE = 256


def refuse_blank(texts: Sequence[str]) -> None:
    """Raise if any element is empty or whitespace-only. ``ports.py:118``.

    Raises rather than dropping or substituting, and the distinction is the whole point:
    a dropped element makes the result **shorter than the input**, which is the other half
    of the guarantee this module exists to hold, and a substituted zero vector is
    "not measured" rendered as "scores nothing" — a value ``cosine`` cannot tell apart
    from a real vector that happens to be orthogonal.
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

    Satisfies :class:`~governed_bi.ports.Embedder` structurally; the Protocol is
    ``runtime_checkable`` so ``isinstance`` holds without inheriting from it, and
    inheriting from a Protocol *and* an ABC buys nothing here.
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

        The width check runs **after** the batches rather than before, so an adapter
        whose ``dimensions`` is only knowable from a response does not pay a probe
        request it is about to learn the answer to anyway.
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

    ``embedding_model`` and ``embedding_dimensions`` are ``Role.comparability``
    (``register/knobs.py:208-212``) and v1 recorded neither, so two ladders differing
    only in embedder compared as one experiment. The names are checked against
    :func:`~governed_bi.register.knobs.knob_names` on every call, for the reason
    ``knob_default`` gives for its own raise: a typo'd name ships a plausible literal
    that no knob backs, so the config hash does not move when the real knob does — and a
    field outside the comparability hash is v1's ``serve_config_hash`` defect exactly.
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
