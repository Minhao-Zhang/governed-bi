"""Paths resolve at import. ``Path.resolve`` calls ``os.getcwd``, which the dev server refuses on the event loop.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["REPO_ROOT", "assert_not_a_warehouse"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


#: A denylist. Legal path shapes are not enumerable; the strings a DSN uses are.
_WAREHOUSE_MARKERS: tuple[str, ...] = (
    "postgres://",
    "postgresql://",
    "redshift://",
    "host=",
    "dbname=",
    "password=",
)


def assert_not_a_warehouse(value: str, *, source: str) -> str:
    """Raise when ``value`` names a database server. A DSN here would write into the warehouse."""
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
