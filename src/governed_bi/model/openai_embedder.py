"""``text-embedding-3-large`` through the OpenAI SDK directly.

**Not a wrapper over ``langchain-openai``.** Decision #2 records that this port exists
*because* LangChain's ``Embeddings`` lacks ``model`` and ``dimensions``, so re-wrapping
it would put the two missing facts back out of reach and leave the port as pure
indirection over someone else's seam — which is the reason ``ChatModel`` was *rejected*
as a port (``ports.py:22``). ``openai>=1.40`` is already a declared dependency.

**``model`` reports what the provider served, not what was requested.** The house rule,
in the one place it applies to a third party: the record must describe what happened. A
provider may serve an alias, a dated snapshot, or a silently upgraded version of the name
it was handed, and ``embedding_model`` is a comparability knob — so a run that records the
*requested* name and compares against one that got a different snapshot is v1's
reasoning-effort incident again, where two ladders differed only in a live config field
recorded nowhere and effort moved the baseline arm **+2.5pp against a 2.3pp detection
threshold**.

That is why ``served_model`` is ``None`` until a response has been seen, and why reading
:attr:`OpenAIEmbedder.model` on a cold object issues a one-token probe rather than
returning the requested string. ``ports.py:124`` also requires ``model`` to be *stable
for the lifetime of the object*, and those two requirements together rule out the obvious
design — return the request, replace it after the first real call — because that changes
identity underneath a cache key that has already been formed. One probe, memoised, is the
price of holding both.

**Cost shape.** One request per batch of :attr:`~.embedder.BaseEmbedder.batch_size`
inputs, plus at most one two-token probe per object, and only when ``model`` or an
unspecified ``dimensions`` is read before the first ``embed``. This port does not cache;
``retrieve.index`` owns that.

**A rate limit or a dead endpoint raises** (``ports.py:127``). It must never be absorbed
into a low score: a rate-limited embedder published a schema-pick accuracy that
re-measured **21 points higher** once quota was free.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from governed_bi.ports import Vector

from .embedder import BaseEmbedder

__all__ = ["OPENAI_API_KEY_VAR", "OPENAI_EMBEDDING_MODEL", "OpenAIEmbedder"]

#: The environment variable, by **name**. Never log, print or record its value.
OPENAI_API_KEY_VAR = "OPENAI_API_KEY"

#: ``measure/price.py`` prices exactly this model, and its note says "every ladder embeds
#: through this model, so omitting it understates every run". Changing the default here
#: without adding a ``Price`` row makes every USD figure a floor of unknown depth.
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"

#: Shortest non-blank probe. Non-blank because this adapter refuses a blank string, and
#: an adapter exempting its own probe from its own rule is the shape of hazard
#: ``embedder.refuse_blank`` exists to close.
_PROBE_TEXT = "probe"


class OpenAIEmbedder(BaseEmbedder):
    """``text-embedding-3-large`` (or any OpenAI embedding model) as an ``Embedder``.

    ``dimensions`` is passed through to the API. 3-large supports a shortened width via
    Matryoshka truncation, which is why "the width the API returned" and "the width the
    adapter declares" are two facts and not one: a ``dimensions`` request the provider
    ignored would otherwise pass unnoticed, and the declared width is what every cache
    key carries. Leave it ``None`` for the model's native width, learned from a response.
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
        # **The embedder is the second provider surface, and leaving it out is what would make
        # "a global retry limit" a false claim.** It is not an LLM call, but its 429 exposure is
        # identical and it sits on the same critical path — `accept` embeds the question before
        # any facet runs. `None` keeps the SDK's own defaults, which is what an injected `client`
        # already implies; `graph_app` passes the knobs.
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

        Deliberately does **not** resolve. That this can be ``None`` on a fresh object is
        the observable difference between an adapter that reads the response and one that
        echoes the request, and it is the only difference visible while the provider
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

        A requested width is returned without a probe — it *is* what this object
        declares, and what it carries into every cache key. The check that the provider
        honoured it happens on the first real response, in :meth:`_record_identity`,
        which raises rather than quietly declaring one width and returning another.
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

            # Only what was actually configured. Passing `max_retries=None` to the SDK sets it
            # to None rather than leaving the default, which would turn "unconfigured" into
            # "no retries at all" — the opposite of the intent.
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

        # Sorted by the provider's own ``index`` rather than trusted to arrive in order.
        # The API documents order preservation; ``ports.py:113`` says a reordered result
        # is a bug in the adapter, and "the provider promised" is not a check. This is
        # one sort over a batch, against a failure that is silent by construction: every
        # asset would take another asset's vector and nothing would disagree.
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
