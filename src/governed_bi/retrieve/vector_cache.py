"""The vector cache that outlives the process, so a restart does not re-embed the corpus.

Was ``api/vector_cache.py`` and one JSON file: **888,884,348 bytes**, 13,968 summaries ×
3,072 floats, **21.7 s** to read/parse/validate at every server start and **1,685 MB**
resident once loaded — twice over, because ``build_index`` copied the same Python floats into
``UnifiedIndex.vectors``. ``vectors.py`` records what replaced it and what that costs
(172,374,167 bytes, 7 ms to open, 0.74 s to rebuild the whole index warm).

**The old file's own argument against LanceDB was right about the wrong thing.** It said
LanceDB's selling point is approximate nearest-neighbour search and that this system does
exact cosine over a narrowed candidate set — both true, and still true: no vector index is
built. What it got wrong was the cost of the file it was defending.

**One operational consequence, stated because nothing warns about it.** The JSON is not read
by anything now, so the first start after this change finds an empty store and re-embeds every
summary — about 420 k embedding tokens for the gold layer, roughly $0.01. The old file is left
in place rather than deleted: it is 13,968 vectors somebody already paid for.

**Why this moved out of ``api/``.** It worked there only because ``build_index`` took a
``MutableMapping`` and therefore never had to import it. A typed cache must be importable by
its consumer, and three entry points in three layers need this one — ``api/graph_app.py``,
``serve/__main__.py`` and ``tools/run_datalake_eval.py``, of which the last two passed an
embedder and **no cache at all**, re-embedding 13,968 summaries per invocation.
``tools/check_imports.py`` puts ``serve`` before ``api``, so the only legal home below all
three is here.

**One store per width, and that is not generality for its own sake.**
``text-embedding-3-large`` accepts a ``dimensions`` argument, so one model name is two
unrelated vector spaces; a ``fixed_size_list`` column holds one width; and ``index.py``
promises that **one cache is safe to share across every embedder in a pooled run**. A
single-width cache would break that promise in exactly the case the cache key was widened to
cover.

**The key carries the embedder identity, and that is load-bearing rather than tidy.**
``index.py`` records the defect a text-only key produces: a 1536-wide ``3-large`` and a
1536-wide ``3-small`` are **width-identical and semantically unrelated**, so a text-keyed
cache hands one model's vector to the other with nothing anywhere disagreeing — v1's
cross-model cache hit that degraded routing to "nothing scores" with no error. The key is
built upstream by :func:`~governed_bi.retrieve.semantic.cache_key`; nothing here may
reconstruct or shorten it.
"""

from __future__ import annotations

import os
from pathlib import Path

from governed_bi.paths import REPO_ROOT

from .vectors import MEMORY_URI, VectorStore

__all__ = ["VECTOR_CACHE_VAR", "VectorCache", "vector_cache_from_environment"]

#: Where the persistent cache lives. ``$var`` overrides the location; a default always exists,
#: because the cost of the cache being absent is paid on every restart and the cost of it
#: existing is a directory — so "off" is not a configuration worth offering, only "somewhere
#: else" is. **It now names a directory, not a file**: one JSON file per model became one
#: LanceDB database per model, holding one table per vector width.
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
