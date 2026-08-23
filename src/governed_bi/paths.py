"""Where the repository is, and what a local path may not be.

One definition, because ``python -m governed_bi.serve`` and ``api/graph_app.py`` must agree
when a file moves (``tools/check_one_implementation.py`` enforces it).

**Resolved at import time, not in a function.** ``Path.resolve()`` calls ``os.getcwd``, the
blocking call `blockbuster` leaves armed under ``langgraph dev``; resolving inside an async
handler raised ``BlockingError: Blocking call to os.getcwd``. Module level runs before the
event loop exists.

Only the entry points may *read* ``.env``, and ``credentials`` is where the reading happens;
this module says where the repository is and refuses one thing a path must never be.

**Why the refusal lives here.** :func:`assert_not_a_warehouse` was written for the conversation
checkpointer and lived in ``serve/checkpointer.py``. The return path's store needs exactly the same
guard on exactly the same class of value, and ``feedback`` sits *below* ``serve`` in
``tools/check_imports.py::LAYERS`` -- so the choice was a duplicated copy that can drift or one
definition in the innermost layer every other may ask. It is stdlib-only and it is a fact about a
path, so it moved rather than being copied. ``serve/checkpointer.py`` re-exports it, and its
callers and tests did not change.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["REPO_ROOT", "assert_not_a_warehouse"]

#: The repository root: the directory holding ``pyproject.toml``.
#: ``src/governed_bi/paths.py`` → ``src/governed_bi`` → ``src`` → root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


#: Substrings that mean "this is a connection string, not a file". A denylist and not an
#: allowlist: the set of legal path shapes across platforms is not enumerable, and the set of
#: things a DSN says about itself is small and stable.
_WAREHOUSE_MARKERS: tuple[str, ...] = (
    "postgres://",
    "postgresql://",
    "redshift://",
    "host=",
    "dbname=",
    "password=",
)


def assert_not_a_warehouse(value: str, *, source: str) -> str:
    """Return ``value``, or raise if it names a database server rather than a file.

    Two local SQLite stores are configured by environment variable -- the conversation
    checkpointer and the return path's feedback store -- and neither may ever be pointed at the
    analytics warehouse. The failure this prevents is not a crash: a DSN in one of those variables
    would be *accepted* by something and the operational data would end up in the database the
    engine is meant to read from.
    """
    lowered = value.lower()
    for marker in _WAREHOUSE_MARKERS:
        if marker in lowered:
            raise ValueError(
                f"{source} looks like a database connection string ({marker!r} in it), not a "
                "file path. This store is a local SQLite file and must never share a database "
                "with the analytics warehouse. Set it to a path such as "
                "'runs/conversations.sqlite'."
            )
    return value
