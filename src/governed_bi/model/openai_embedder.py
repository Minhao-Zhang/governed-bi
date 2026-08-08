"""``text-embedding-3-large`` through the OpenAI SDK directly.

**Not a wrapper over ``langchain-openai``**, whose ``Embeddings`` lacks ``model`` and
``dimensions`` — the two facts this port exists to expose (the same reason ``ChatModel``
was rejected as a port, ``ports.py:22``).

**``model`` reports what the provider served, not what was requested.** A provider may
serve an alias, a dated snapshot or a silent upgrade, and ``embedding_model`` is a
comparability knob, so recording the request would let two runs on different snapshots
compare as one (v1's reasoning-effort incident: an unrecorded live config field moved the
baseline arm past that ladder's own detection threshold — sizes retired, mechanism is not).
``ports.py:124`` also requires ``model`` to be stable for the object's
lifetime, which rules out returning the request and replacing it after the first call —
that changes identity under a cache key already formed. One memoised probe holds both.

**Cost shape.** One request per :attr:`~.embedder.BaseEmbedder.batch_size` inputs, plus at
most one two-token probe per object, and only when ``model`` or an unspecified
``dimensions`` is read before the first ``embed``. No caching here; ``retrieve.index``
owns that.

**A rate limit or a dead endpoint raises** (``ports.py:127``) — a rate-limited embedder
once published a schema-pick accuracy that rose sharply when re-measured with quota free
(both figures and the gap between them retired; see ``register/citations.py``).
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from governed_bi.ports import Vector

from .embedder import BaseEmbedder

__all__ = ["OPENAI_API_KEY_VAR", "OPENAI_EMBEDDING_MODEL", "OpenAIEmbedder"]

#: The environment variable, by **name**. Never log, print or record its value.
OPENAI_API_KEY_VAR = "OPENAI_API_KEY"

#: Every ladder embeds through this model, so changing the default without adding a
#: ``Price`` row makes every USD figure a floor of unknown depth.
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"

#: Shortest non-blank probe. Non-blank because this adapter refuses a blank string, and an
#: adapter exempting its own probe from its own rule is what ``refuse_blank`` closes.
_PROBE_TEXT = "probe"


class OpenAIEmbedder(BaseEmbedder):
    """``text-embedding-3-large`` (or any OpenAI embedding model) as an ``Embedder``.

    ``dimensions`` is passed through to the API (3-large supports Matryoshka truncation).
    The returned width and the declared width are tracked as two facts, so a ``dimensions``
    request the provider ignored cannot pass unnoticed into a cache key. ``None`` means the
    native width, learned from a response.
    """

    def __init__(
        self,
        *,
        model: str = OPENAI_EMBEDDING_MODEL,
        dimensions: int | None = None,
        batch_size: int | None = None,
        client: Any | None = None,
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> None:
        if dimensions is not None and int(dimensions) < 1:
            raise ValueError(f"dimensions must be positive, got {dimensions!r}")
        self._requested_model = str(model)
        self._requested_dimensions = None if dimensions is None else int(dimensions)
        self._served_model: str | None = None
        self._served_width: int | None = None
        self._client = client
        # The embedder is the second provider surface: same 429 exposure, same critical path
        # (`accept` embeds the question before any facet runs), so a "global retry limit" that
        # skipped it would be false. `None` keeps the SDK defaults; `graph_app` passes the knobs.
        self._max_retries = None if max_retries is None else int(max_retries)
        self._timeout = None if timeout is None else float(timeout)
        if batch_size is not None:
            self.batch_size = int(batch_size)

    # ── identity ──────────────────────────────────────────────────────────────

    @property
    def requested_model(self) -> str:
        """The name this object was constructed with. **Not** the cache-key identity."""
        return self._requested_model

    @property
    def served_model(self) -> str | None:
        """What the provider last reported, or ``None`` if no response has been seen.

        Deliberately does **not** probe: ``None`` on a fresh object is the only observable
        difference between reading the response and echoing the request while the provider
        happens to serve the name it was asked for.
        """
        return self._served_model

    @property
    def model(self) -> str:
        """``openai:<served model>``. Provider-qualified, per ``ports.py:140``."""
        if self._served_model is None:
            self._probe()
        return f"openai:{self._served_model}"

    @property
    def dimensions(self) -> int:
        """The declared width: the requested one when given, else the served one.

        A requested width is returned without a probe — it *is* what this object carries
        into every cache key. Whether the provider honoured it is checked on the first real
        response, in :meth:`_record_identity`.
        """
        if self._requested_dimensions is not None:
            return self._requested_dimensions
        if self._served_width is None:
            self._probe()
        assert self._served_width is not None
        return self._served_width

    # ── the provider ──────────────────────────────────────────────────────────

    def _openai_client(self) -> Any:
        if self._client is None:
            if not os.environ.get(OPENAI_API_KEY_VAR):
                raise RuntimeError(
                    f"{OPENAI_API_KEY_VAR} is not set, so the OpenAI embedder cannot "
                    "run. Use model.DeterministicEmbedder for a model-free path "
                    "(ADR 0005 implementation steps 6-9)."
                )
            from openai import OpenAI

            # Only what was configured: passing `max_retries=None` to the SDK sets it to None
            # rather than leaving the default, turning "unconfigured" into "no retries".
            options: dict[str, Any] = {}
            if self._max_retries is not None:
                options["max_retries"] = self._max_retries
            if self._timeout is not None:
                options["timeout"] = self._timeout
            self._client = OpenAI(**options)
        return self._client

    def _probe(self) -> None:
        """Learn the served model and native width from one two-token request."""
        self._call([_PROBE_TEXT])

    def _record_identity(self, served: str, width: int) -> None:
        if self._served_model is None:
            self._served_model = served
        elif served != self._served_model:
            raise ValueError(
                f"the provider switched model mid-object: {self._served_model!r} then "
                f"{served!r}. Both vectors are already keyed under the first identity, "
                "so continuing would put two models in one cache entry"
            )

        if self._requested_dimensions is not None and width != self._requested_dimensions:
            raise ValueError(
                f"requested dimensions={self._requested_dimensions} but "
                f"{served!r} returned {width}; a dimensions request the provider "
                "ignored must not pass as an honoured one"
            )
        if self._served_width is not None and width != self._served_width:
            raise ValueError(
                f"the provider changed width mid-object: {self._served_width} then {width}"
            )
        self._served_width = width

    def _call(self, texts: Sequence[str]) -> list[Vector]:
        kwargs: dict[str, Any] = {"model": self._requested_model, "input": list(texts)}
        if self._requested_dimensions is not None:
            kwargs["dimensions"] = self._requested_dimensions

        response = self._openai_client().embeddings.create(**kwargs)

        # Sorted by the provider's own ``index`` rather than trusted to arrive in order. The
        # API documents order preservation, but a reorder is silent by construction — every
        # asset takes another asset's vector and nothing disagrees (``ports.py:113``).
        items = sorted(response.data, key=lambda item: int(item.index))
        if len(items) != len(texts):
            raise ValueError(
                f"asked for {len(texts)} embeddings and got {len(items)}"
            )
        if [int(item.index) for item in items] != list(range(len(texts))):
            raise ValueError(
                f"provider returned indices {[int(i.index) for i in items]} for "
                f"{len(texts)} inputs; the batch cannot be aligned to its inputs"
            )

        vectors = [list(item.embedding) for item in items]
        self._record_identity(str(response.model), len(vectors[0]))
        return list(vectors)

    def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        return self._call(texts)
