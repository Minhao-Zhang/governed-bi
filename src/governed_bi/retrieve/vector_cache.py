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

#: Characters no path component may contain on Windows. NTFS reads ``name:stream`` as an
#: alternate data stream, so ``mkdir`` raises rather than creating a directory. POSIX reserves
#: only ``/``; the stricter set is applied on both so one cache has one name everywhere.
_UNSAFE_IN_PATH = '<>:"/\\|?*'


def _directory_name(model: str) -> str:
    """``model`` reduced to something that can be a single path component.

    **Bedrock ids are versioned with a colon** -- ``amazon.titan-embed-text-v2:0`` -- and that
    is not a legal directory name on Windows: it raises ``NotADirectoryError`` (WinError 267).
    Nothing degraded, the server simply could not boot the first time the embedding surface
    moved to Bedrock, and the traceback named LanceDB rather than the model id.

    Only illegal characters are replaced, so every name that already works is byte-identical
    and ``text-embedding-3-large`` keeps resolving to the directory already holding its rows.

    **Not** :func:`corpus.identity.slug`, though the layering would allow it. That function is
    injective by construction because an asset id keys the retrieval index, and its charset
    excludes ``-``, so it would rename the existing store instead of leaving it alone. The
    contract here is the opposite: this directory is *not* the identity. ``cache_key`` is
    ``model|dimensions|text`` over the provider-qualified id with the colon intact, so two ids
    that sanitise alike share a directory and still cannot read each other's rows.
    """
    return "".join("_" if ch in _UNSAFE_IN_PATH else ch for ch in model)


class VectorCache:
    """Cache keys to vectors, persistent, across every width an embedder produces.

    A router over :class:`VectorStore` — one per width, opened on first use. Holds no
    vectors itself, which is what lets ``VectorStore`` keep "one width" as an invariant.
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
        """Rows already present at open, over the widths touched."""
        return sum(store.opened_with for store in self._stores.values())

    @property
    def written(self) -> int:
        """Rows this process added; zero is what the reload loop depends on."""
        return sum(store.written for store in self._stores.values())

    def __len__(self) -> int:
        return sum(len(store) for store in self._stores.values())

    def keys(self) -> list[str]:
        """Every key, over the widths touched. A width nobody asked for is never opened,
        so it is not counted: this reports what the run actually consulted."""
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

    One database directory per model, one table per width inside it. The key already carries
    both, so the layout is defence in depth rather than the mechanism.

    ``model`` is the **requested** name, never ``Embedder.model``: reading that property on a
    cold ``OpenAIEmbedder`` issues a network probe, and a directory name is not worth a
    request at boot.

    The name is passed through :func:`_directory_name` because a Bedrock id is not a legal
    directory on Windows. Sanitising here rather than at the call sites keeps the server, the
    CLI and the eval driver pointing at one directory per model.
    """
    configured = os.environ.get(VECTOR_CACHE_VAR)
    root = Path(configured) if configured else REPO_ROOT / "runs" / "vectors"
    return VectorCache(uri=root / _directory_name(model))
