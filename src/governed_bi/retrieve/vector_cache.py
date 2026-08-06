"""Persistent vector cache across process restarts.

One store per vector width; keys carry embedder identity via
:func:`~governed_bi.retrieve.semantic.cache_key`. Lives in ``retrieve/`` so
``api``, ``serve``, and eval can share it under the import layering.
"""


from __future__ import annotations

import os
from pathlib import Path

from governed_bi.paths import REPO_ROOT

from .vectors import MEMORY_URI, VectorStore

__all__ = ["VECTOR_CACHE_VAR", "VectorCache", "vector_cache_from_environment"]

#: Persistent cache directory (env override). One LanceDB DB per model, table per width.
VECTOR_CACHE_VAR = "GOVERNED_BI_VECTOR_CACHE"


class VectorCache:
    """Cache keys to vectors, persistent, across every width an embedder produces.

    A thin router over :class:`VectorStore` — one per width, opened on first use — plus the
    aggregate figures the server prints. It holds no vectors and does no scoring; splitting it
    out is what lets ``VectorStore`` keep "one width" as an invariant rather than an argument.
    """

    def __init__(self, *, uri: str | Path = MEMORY_URI) -> None:
        self._uri = str(uri)
        self._stores: dict[int, VectorStore] = {}

    @property
    def uri(self) -> str:
        """Where the rows are. Printed by the server so "the cache worked" is checkable."""
        return self._uri

    @property
    def opened_with(self) -> int:
        """Rows already present at open, over the widths touched. Aggregated so the server can
        print it: a cache nobody can measure is one that can silently stop working."""
        return sum(store.opened_with for store in self._stores.values())

    @property
    def written(self) -> int:
        """Rows this process added; zero is what the reload loop depends on."""
        return sum(store.written for store in self._stores.values())

    def __len__(self) -> int:
        return sum(len(store) for store in self._stores.values())

    def keys(self) -> list[str]:
        """Every key, over the widths touched. Widths nobody asked for are not opened, so
        they are not counted — a cache reports on what this run actually consulted."""
        return [key for store in self._stores.values() for key in store.keys()]

    def at_width(self, dimensions: int) -> VectorStore:
        """The store for ``dimensions``, opened or created on first use."""
        store = self._stores.get(int(dimensions))
        if store is None:
            store = VectorStore(dimensions, uri=self._uri)
            self._stores[int(dimensions)] = store
        return store


def vector_cache_from_environment(*, model: str) -> VectorCache:
    """The persistent cache the server, the CLI and the eval driver share.

    One database directory per model, so a human can delete one model's vectors correctly; one
    table per width inside it. The key already carries both, so the layout is defence in depth
    rather than the mechanism.

    ``model`` is the **requested** name, never ``Embedder.model``: reading that property on a
    cold ``OpenAIEmbedder`` issues a network probe to report what the provider actually
    served, and a directory name is not worth a request at boot.
    """
    configured = os.environ.get(VECTOR_CACHE_VAR)
    root = Path(configured) if configured else REPO_ROOT / "runs" / "vectors"
    return VectorCache(uri=root / model)
