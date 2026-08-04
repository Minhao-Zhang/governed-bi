"""A vector cache that outlives the process, so a restart does not re-embed the corpus.

``retrieve/index.py`` already takes a ``vector_cache: MutableMapping[str, Vector]`` keyed by
:func:`~governed_bi.retrieve.semantic.cache_key` — ``model|dimensions|text``. Nothing in the
serve path ever handed it one, so switching the embedder on would have meant embedding every
summary in the corpus at every server start: 8035 files in the gold semantic layer, and
``langgraph dev`` restarts on file save. This is that mapping, backed by a file.

**Why a file and not LanceDB, for now.** LanceDB is the right destination and this is the seam it
plugs into — it is a ``MutableMapping`` boundary, so the store is swappable without touching
retrieval. But the thing LanceDB buys over this is *approximate nearest-neighbour search at
scale*, and the semantic channel does not search a vector store: ``semantic_search`` scores an
in-memory dict by exact cosine over an already-narrowed candidate set. Introducing a vector
database to serve as a key-value cache would be new machinery answering a question nobody has
yet asked, and the question that *is* being asked — "why is the semantic channel dead?" — is
answered by handing ``build_index`` a mapping. When ANN search or hybrid retrieval becomes the
requirement, this class is the one file that changes.

**The key carries the embedder identity, and that is load-bearing rather than tidy.**
``index.py`` records the defect a text-only key produces: ``text-embedding-3-large`` accepts a
``dimensions`` argument, so a 1536-wide ``3-large`` and a 1536-wide ``3-small`` are
**width-identical and semantically unrelated**, and a text-keyed cache hands one model's vector
to the other with nothing anywhere disagreeing — v1's cross-model cache hit that degraded routing
to "nothing scores" with no error. The key is built upstream by ``cache_key``; this class must
not reconstruct or shorten it.

**Every failure here is non-fatal, and that is a deliberate asymmetry.** A cache is an
optimisation: a corrupt or unreadable file must degrade to "embed everything again", never to a
server that will not start. The one thing it must *not* do is return a wrong vector, which is why
a malformed entry is dropped rather than repaired.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

from governed_bi.paths import REPO_ROOT

__all__ = ["FileVectorCache", "vector_cache_from_environment"]


class FileVectorCache(MutableMapping[str, Any]):
    """``MutableMapping[cache_key, Vector]`` persisted as one JSON file.

    Loaded once at construction and written by :meth:`flush`, not per write. ``build_index``
    fills the cache in one batch and the caller flushes after: a write per key would mean 8035
    file writes for one build, and the mapping protocol gives no hook for "the batch is done".
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, list[float]] = {}
        self._loaded_keys = 0
        #: Whether anything was written since load. **This is what stops a reload loop**, not an
        #: optimisation: the cache lives under ``runs/`` inside the repository, ``langgraph dev``
        #: watches the tree, and flushing an unchanged file made the server restart, re-import,
        #: flush again and restart forever — observed, "13 changes detected" every ten seconds
        #: with the server never becoming ready. A run that adds nothing now writes nothing, so a
        #: warm start is inert and a cold one costs exactly one reload.
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            # Unreadable or malformed. Start empty and re-embed — a cache that refuses to be a
            # cache is still correct, where a cache that halts the server is not.
            return
        if not isinstance(raw, dict):
            return
        for key, vector in raw.items():
            # Dropped rather than coerced. A "repaired" vector is a wrong vector, and
            # `semantic.py` raises on a width mismatch only when two vectors *differ* — a
            # uniformly wrong entry would pass every check downstream.
            if isinstance(key, str) and isinstance(vector, list) and vector:
                if all(isinstance(x, (int, float)) for x in vector):
                    self._data[key] = [float(x) for x in vector]
        self._loaded_keys = len(self._data)

    # ── MutableMapping ────────────────────────────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = [float(x) for x in value]
        self._dirty = True

    def __delitem__(self, key: str) -> None:
        del self._data[key]
        self._dirty = True

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    # ── persistence ───────────────────────────────────────────────────────────

    @property
    def hits(self) -> int:
        """How many keys were already on disk. What makes "the cache worked" reportable rather
        than assumed — a cache nobody can measure is one that can silently stop working."""
        return self._loaded_keys

    @property
    def dirty(self) -> bool:
        """Whether a flush would change the file. Read by the caller so it can say "unchanged"
        rather than claiming a write it did not make."""
        return self._dirty

    def flush(self) -> str | None:
        """Write the cache **if anything changed**. Returns an error string rather than raising.

        The no-op on a clean cache is load-bearing — see ``_dirty``. Written to a temporary
        sibling and moved, so an interrupted write leaves the previous cache rather than a
        truncated one that would be read back as a partial corpus.
        """
        if not self._dirty:
            return None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as err:
            return f"{type(err).__name__}: {err}"
        self._dirty = False
        return None


def vector_cache_from_environment(var: str, default_name: str) -> FileVectorCache:
    """The cache the server uses. ``$var`` overrides the location; a default always exists.

    A default rather than ``None`` because the cost of the cache being absent is paid on every
    restart and the cost of it existing is a file — so "off" is not a configuration worth
    offering, only "somewhere else" is.
    """
    configured = os.environ.get(var)
    path = Path(configured) if configured else REPO_ROOT / "runs" / "vectors" / default_name
    return FileVectorCache(path)
