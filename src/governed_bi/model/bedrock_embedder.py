"""Bedrock embeddings as an :class:`~governed_bi.ports.Embedder`.

Wraps ``langchain_aws.BedrockEmbeddings`` rather than calling ``bedrock-runtime`` directly,
so model-family quirks stay LangChain's problem. The wrapper exists because the LangChain
``Embeddings`` interface is missing the two facts this port is *for*: ``model`` and
``dimensions`` both go into every vector cache key, and neither is on that interface --
the same reason ``OpenAIEmbedder`` is not a wrapper over ``langchain-openai``
(``ports.py``'s no-single-adapter rule).

**Width is learned, not assumed.** Titan v2 serves 1024 by default but is configurable to
512 or 256, and Cohere's Bedrock models serve 1024. A hard-coded table would go stale
silently, and the declared width is what enters every cache key -- so a wrong one keys this
adapter's vectors under a width the provider never served, and ``BaseEmbedder.embed``'s width
check is what turns that into a failure rather than a silent cross-model cache hit. One
memoised probe settles it, the same shape ``OpenAIEmbedder`` uses for its served-model id.

(This note read *"``cosine`` returns 0.0 on a width mismatch instead of raising"* until
2026-08-12 and gave that as the reason for probing. ``retrieve/semantic.py``'s ``cosine``
raises ``ValueError`` on a width mismatch and has since v2 -- audit N2.)
"""

from __future__ import annotations

from typing import Any, Sequence

from governed_bi.ports import Vector

from .embedder import PROBE_TEXT, BaseEmbedder

__all__ = ["BEDROCK_EMBEDDING_MODEL", "BedrockEmbedder"]

#: Titan Text Embeddings v2, 1024 wide by default. Changing it changes the vector space and
#: invalidates every cached row, the same rule the OpenAI default carries. (Not a ``Price``
#: rule any more: ``measure/price.py`` is deleted and USD is the provider's number.)
#:
#: Titan and not ``cohere.embed-v4:0``, which is also on-demand: Cohere is trained on an
#: ``input_type`` asymmetry and ``langchain_aws`` hard-codes ``search_document``, while this
#: engine's port is a single ``embed(texts)`` with no ``embed_query`` -- so the per-turn
#: question would be embedded on the document side and config could not say otherwise.
BEDROCK_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"


class BedrockEmbedder(BaseEmbedder):
    """A Bedrock embedding model behind the engine's ``Embedder`` port."""

    def __init__(
        self,
        *,
        model: str = BEDROCK_EMBEDDING_MODEL,
        dimensions: int | None = None,
        region: str | None = None,
        batch_size: int | None = None,
        client: Any | None = None,
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> None:
        if dimensions is not None and int(dimensions) < 1:
            raise ValueError(f"dimensions must be positive, got {dimensions!r}")
        self._model = str(model)
        self._declared_dimensions = None if dimensions is None else int(dimensions)
        self._observed_dimensions: int | None = None
        if batch_size is not None:
            self.batch_size = int(batch_size)
        self._client = client
        self._region = region
        self._max_retries = max_retries
        self._timeout = timeout
        self._impl: Any | None = None

    # ── construction ──────────────────────────────────────────────────────────

    def _embeddings(self) -> Any:
        """The ``langchain-aws`` object, built once, lazily.

        Lazy because importing this module must not drag boto3 into a bare interpreter --
        ``model/__init__.py`` promises that every model-free test can reach
        ``DeterministicEmbedder`` without a provider tree installed.
        """
        if self._impl is not None:
            return self._impl
        try:
            from langchain_aws import BedrockEmbeddings  # noqa: PLC0415
        except ImportError as err:  # pragma: no cover - exercised by the install, not a test
            raise RuntimeError(
                "Bedrock embeddings need `langchain-aws`, which is not installed. "
                "Install the extra: `uv sync --extra bedrock`"
            ) from err

        from .provider import aws_region  # noqa: PLC0415

        kwargs: dict[str, Any] = {"model_id": self._model}
        region = self._region or aws_region()
        if region:
            kwargs["region_name"] = region
        if self._client is not None:
            kwargs["client"] = self._client
        elif self._timeout is not None or self._max_retries is not None:
            kwargs["config"] = self._boto_config()
        if self._declared_dimensions is not None and self._supports_dimensions():
            # Titan v2 takes the width as a model kwarg; Cohere's do not, and passing it
            # there is a 400 rather than an ignored field.
            kwargs["model_kwargs"] = {"dimensions": self._declared_dimensions}
        self._impl = BedrockEmbeddings(**kwargs)
        return self._impl

    def _supports_dimensions(self) -> bool:
        return "titan-embed-text-v2" in self._model

    def _boto_config(self) -> Any:
        from botocore.config import Config  # noqa: PLC0415

        config: dict[str, Any] = {}
        if self._timeout is not None:
            config["read_timeout"] = float(self._timeout)
            config["connect_timeout"] = float(self._timeout)
        if self._max_retries is not None:
            # max_attempts counts the first try; the knob counts retries after it.
            config["retries"] = {"max_attempts": int(self._max_retries) + 1, "mode": "adaptive"}
        return Config(**config)

    # ── the port ──────────────────────────────────────────────────────────────

    @property
    def requested_model(self) -> str:
        """The id this object was constructed with. **Not** the cache-key identity.

        ``vector_cache_from_environment`` takes this and not :attr:`model`: it only names a
        directory, and on a cold embedder :attr:`model` may probe. Same property, same
        reason, as ``OpenAIEmbedder`` and ``ProxyEmbedder``.
        """
        return self._model

    @property
    def model(self) -> str:
        """``bedrock:<model id>``. Provider-qualified, per ``ports.Embedder.model``.

        The prefix is not decoration: ``cache_key`` is ``model|dimensions|text`` and takes
        no provider of its own, so the qualification here is the only thing keeping two
        gateways serving one nominal id out of each other's cached vectors.

        Unlike OpenAI, Bedrock's response carries no served-model field to read back, so the
        id is the requested one. An alias resolving elsewhere would be invisible; Bedrock ids
        are versioned (``:0``) rather than floating, which is what makes that acceptable here
        where it was not for OpenAI.
        """
        return f"bedrock:{self._model}"

    @property
    def dimensions(self) -> int:
        """Vector width: declared if given, else learned from one probe."""
        if self._declared_dimensions is not None:
            return self._declared_dimensions
        if self._observed_dimensions is None:
            vector = self._embeddings().embed_query(PROBE_TEXT)
            self._observed_dimensions = len(vector)
        return self._observed_dimensions

    def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        vectors = self._embeddings().embed_documents(list(texts))
        if self._observed_dimensions is None and vectors:
            self._observed_dimensions = len(vectors[0])
        return [list(v) for v in vectors]
