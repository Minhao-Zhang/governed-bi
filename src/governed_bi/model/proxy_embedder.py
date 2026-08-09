"""``Embedder`` over the internal proxy's embeddings route.

Wraps the LangChain ``OpenAIEmbeddings`` that :func:`~.proxy_gateway.build_embeddings` returns —
which carries the proxy auth and the input clipping — and exposes it through
:class:`~.embedder.BaseEmbedder`, so ``build_index`` and ``Session`` treat it like any other
adapter. Identity is ``proxy:<model>``, provider-qualified per ``ports.py:140``, which is what
keeps a the internal proxy-served vector out of the cache entry an OpenAI-served one of the same width owns.

**Unlike ``OpenAIEmbedder``, ``model`` reports the request, not what the provider served.**
LangChain's ``Embeddings`` does not surface the response's ``model`` field, so the served name
is not reachable through this client. That is a real gap, recorded here rather than papered
over: a silent snapshot upgrade behind the proxy would not move the ``embedding_model`` knob.
The width still is checked against every response.
"""

from __future__ import annotations

from typing import Sequence

from governed_bi.ports import Vector

from .embedder import BaseEmbedder
from .openai_embedder import OPENAI_EMBEDDING_MODEL
from .proxy_gateway import build_embeddings

__all__ = ["ProxyEmbedder"]


class ProxyEmbedder(BaseEmbedder):
    """``text-embedding-3-large`` (or any model the proxy serves) as an ``Embedder``.

    ``proxy_secret_name`` / ``proxy_region`` default to ``None``, meaning *read the
    environment* — see :data:`~.proxy_gateway.PROXY_SECRET_NAME_VAR`. Nothing about the endpoint
    or the credential is a constructor default.
    """

    def __init__(
        self,
        *,
        embedding_model: str = OPENAI_EMBEDDING_MODEL,
        embedding_dimensions: int | None = None,
        proxy_secret_name: str | None = None,
        proxy_region: str | None = None,
        session_id: str | None = None,
    ) -> None:
        if embedding_dimensions is not None and int(embedding_dimensions) < 1:
            raise ValueError(f"dimensions must be positive, got {embedding_dimensions!r}")
        self._requested_model = str(embedding_model)
        self._requested_dims = None if embedding_dimensions is None else int(embedding_dimensions)
        self._served_width: int | None = None
        self._lc = build_embeddings(
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            proxy_secret_name=proxy_secret_name,
            proxy_region=proxy_region,
            session_id=session_id,
        )

    @property
    def requested_model(self) -> str:
        """The id this object was constructed with. **Not** the cache-key identity.

        ``vector_cache_from_environment`` takes this and not :attr:`model`, because it only
        names a directory and reading :attr:`model` on a cold adapter can cost a request.
        ``OpenAIEmbedder`` and ``BedrockEmbedder`` have carried it all along; this one did
        not, so the eval driver's proxy path raised ``AttributeError`` the moment it was
        routed through the shared builder. Every adapter owes the whole identity surface,
        not the half its own caller happened to use.
        """
        return self._requested_model

    @property
    def model(self) -> str:
        """``proxy:<requested id>``. Provider-qualified, per ``ports.py:140``."""
        return f"proxy:{self._requested_model}"

    @property
    def dimensions(self) -> int:
        """The requested width when given, else the native one learned from one probe call."""
        if self._requested_dims is not None:
            return self._requested_dims
        if self._served_width is None:
            self._embed_batch(["probe"])
        assert self._served_width is not None
        return self._served_width

    def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        vectors = self._lc.embed_documents(list(texts))
        if vectors:
            width = len(vectors[0])
            if self._served_width is not None and width != self._served_width:
                raise ValueError(
                    f"the provider changed width mid-object: {self._served_width} then {width}"
                )
            if self._requested_dims is not None and width != self._requested_dims:
                raise ValueError(
                    f"requested dimensions={self._requested_dims} but the proxy returned {width}; "
                    "a dimensions request the provider ignored must not pass as an honoured one"
                )
            self._served_width = width
        return [list(v) for v in vectors]
