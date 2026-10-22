"""One store per width. Keys carry embedder identity via ``cache_key``. See ADR 0005."""


from __future__ import annotations

from pathlib import Path

from governed_bi.paths import REPO_ROOT

from .vectors import MEMORY_URI, VectorStore

__all__ = ["VECTOR_CACHE_VAR", "VectorCache", "vector_cache_from_environment"]

VECTOR_CACHE_VAR = "GOVERNED_BI_VECTOR_CACHE"

#: Illegal in a Windows path. The same set is used on every platform so one cache has one name.
_UNSAFE_IN_PATH = '<>:"/\\|?*'


def _directory_name(model: str) -> str:
    """Replace only characters that cannot be a directory, so an existing store keeps its name.

    Not ``corpus.identity.slug``: its charset excludes ``-`` and would rename the store. The cache key still has the
    original id, so a shared directory cannot mix rows.
    """
    return "".join("_" if ch in _UNSAFE_IN_PATH else ch for ch in model)


class VectorCache:
    """One ``VectorStore`` per width. This object holds no vectors."""

    def __init__(self, *, uri: str | Path = MEMORY_URI) -> None:
        self._uri = str(uri)
        self._stores: dict[int, VectorStore] = {}

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def opened_with(self) -> int:
        return sum(store.opened_with for store in self._stores.values())

    @property
    def written(self) -> int:
        return sum(store.written for store in self._stores.values())

    def __len__(self) -> int:
        return sum(len(store) for store in self._stores.values())

    def keys(self) -> list[str]:
        return [key for store in self._stores.values() for key in store.keys()]

    def at_width(self, dimensions: int) -> VectorStore:
        store = self._stores.get(int(dimensions))
        if store is None:
            store = VectorStore(dimensions, uri=self._uri)
            self._stores[int(dimensions)] = store
        return store


def vector_cache_from_environment(*, model: str) -> VectorCache:
    """``model`` is the requested name. Reading ``Embedder.model`` on a cold client probes the network."""
    from governed_bi.registry.settings import effective_settings

    configured = str(effective_settings()["vector_cache"])
    root = Path(configured)
    if not root.is_absolute():
        root = REPO_ROOT / root
    return VectorCache(uri=root / _directory_name(model))
