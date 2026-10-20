"""Where the repository is, and what a local path may not be.

One definition, so every entry point agrees when a file moves. Paths resolve at import time
because ``Path.resolve()`` calls ``os.getcwd``, which `blockbuster` refuses inside the event
loop under ``langgraph dev``. :func:`assert_not_a_warehouse` lives here because it is a
stdlib-only fact about a path that every layer may need.
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

    The conversation checkpointer is a local SQLite file. A DSN here would not crash; it would
    put operational data into the warehouse the engine reads from, so it is refused.
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
