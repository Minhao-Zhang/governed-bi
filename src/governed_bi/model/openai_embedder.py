"""SDK embeddings, not ``langchain-openai``: its ``Embeddings`` type has no ``model`` or ``dimensions``.
``model`` is the served name, not the request. An alias or a snapshot would otherwise share a cache key,
and renaming after the first call would change identity under a key already formed. One probe, then stable.
"""

from __future__ import annotations

from typing import Any, Sequence

from governed_bi.ports import Vector

from .embedder import PROBE_TEXT, BaseEmbedder

__all__ = ["OPENAI_API_KEY_VAR", "OPENAI_EMBEDDING_MODEL", "OpenAIEmbedder"]

#: The variable's name. Never log, print or record its value.
OPENAI_API_KEY_VAR = "OPENAI_API_KEY"

#: Changing this id changes the vector space. ``cache_key`` is ``model|dimensions|text``,
#: so a row from another model must not be reused.
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"


class OpenAIEmbedder(BaseEmbedder):
    """Requested and served widths are separate, so an ignored ``dimensions`` cannot enter a cache key.
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
        # `None` keeps the SDK default. Passing `max_retries=None` sets None and disables retries.
        # A retry cap that skipped this call would miss the embed, which runs before any channel.
        self._max_retries = None if max_retries is None else int(max_retries)
        self._timeout = None if timeout is None else float(timeout)
        if batch_size is not None:
            self.batch_size = int(batch_size)

    @property
    def requested_model(self) -> str:
        return self._requested_model

    @property
    def served_model(self) -> str | None:
        """Last served name, or ``None`` before any response. Does not probe.
        ``None`` on a fresh object is the only sign the name was read, not echoed.
        """
        return self._served_model

    @property
    def model(self) -> str:
        if self._served_model is None:
            self._probe()
        return f"openai:{self._served_model}"

    @property
    def dimensions(self) -> int:
        if self._requested_dimensions is not None:
            return self._requested_dimensions
        if self._served_width is None:
            self._probe()
        assert self._served_width is not None
        return self._served_width

    def _openai_client(self) -> Any:
        if self._client is None:
            from governed_bi.credentials import have

            if not have(OPENAI_API_KEY_VAR):
                raise RuntimeError(
                    f"{OPENAI_API_KEY_VAR} is not set, so the OpenAI embedder cannot "
                    "run. Use model.DeterministicEmbedder for a model-free path "
                    "(ADR 0005 implementation steps 6-9)."
                )
            from openai import OpenAI

            options: dict[str, Any] = {}
            if self._max_retries is not None:
                options["max_retries"] = self._max_retries
            if self._timeout is not None:
                options["timeout"] = self._timeout
            self._client = OpenAI(**options)
        return self._client

    def _probe(self) -> None:
        self._call([PROBE_TEXT])

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

        # Sorted by the provider's ``index``. Documented order can still arrive shuffled,
        # and a shuffle gives every input another input's vector with nothing to disagree.
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
